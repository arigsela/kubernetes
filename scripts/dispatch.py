#!/usr/bin/env python3
"""Signal dispatcher — routes a Grafana alert to a read-only agent investigation, by table.

    POST /signal/grafana      Grafana contact-point webhook payload (its second integration)
    GET  /healthz             process is up
    GET  /readyz              the table loaded

Humans get every alert regardless (Grafana → n8n → Slack on its own integration, V86);
this service only ADDS investigations. For each alert: match the table
(/etc/dispatch/dispatch.json, see base-apps/dispatcher/dispatch-table.yaml), check the
budget, and for an `agent:<name>` route open a NEW A2A session as X-User-Id `dispatcher`
with the rendered prompt, then post the finding to n8n's agent-findings webhook (→ Slack).
A `proposes: true` row (its agent may open a pull request, §T.117) is skipped with
`skip-open-prs` while the implementer has `limits.open_prs_max` pull requests open.

Never forwards the alert to n8n, never answers an approval or a question (V88): if the
agent pauses, the finding says so and points at the kagent UI. Logs one JSON line per
decision and per finding, never the answer text.

Environment (all have defaults for the cluster):
    DISPATCH_TABLE          /etc/dispatch/dispatch.json
    KAGENT_A2A_BASE         http://kagent-controller.kagent.svc.cluster.local:8083
    N8N_WEBHOOK_BASE        http://n8n.n8n.svc.cluster.local:5678/webhook
    KAGENT_TOKEN_FILE       /var/run/kagent/token (projected SA token, audience kagent; the mesh
                            sets the identity from it; empty = X-User-Id DISPATCH_USER, dev only)
    DISPATCH_USER           dispatcher (only without a token file)
    INVESTIGATION_TIMEOUT   300   (seconds the A2A send may block)
    KAGENT_UI_SESSION_URL   https://kagent.arigsela.com/agents/{session}/chat
    PORT                    8080
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kagent_a2a as a2a  # noqa: E402

ANSWER_LIMIT = 3000       # chars of the agent's answer forwarded to Slack
SOURCES = {"grafana"}

# The answer goes to Slack verbatim, and a read-only agent can quote pod logs or a
# resource's YAML. Best-effort scrub of the shapes a secret usually takes; the prompts
# also tell the agent not to quote such values. Not a guarantee, a seatbelt.
SECRET_PATTERNS = [
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]+"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bhvs\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
]


def scrub(text: str) -> str:
    """Replace secret-looking values with [redacted] before the text leaves the cluster."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


class TableError(ValueError):
    """The dispatch table is malformed; refuse to serve rather than route wrongly."""


# ------------------------------------------------------------------ the table

def load_table(text: str) -> dict:
    table = json.loads(text)
    if table.get("version") != 1:
        raise TableError("version must be 1")
    limits = table.get("limits") or {}
    for key in ("investigations_per_hour", "repeat_after_hours"):
        if not isinstance(limits.get(key), int) or limits[key] < 0:
            raise TableError(f"limits.{key} must be a non-negative integer")
    # The open-PR cap (§T.117): a `proposes: true` row is skipped while the implementer has this
    # many pull requests open. Absent means 3, never unlimited.
    limits.setdefault("open_prs_max", 3)
    if not isinstance(limits["open_prs_max"], int) or limits["open_prs_max"] < 0:
        raise TableError("limits.open_prs_max must be a non-negative integer")
    table["limits"] = limits
    seen = set()
    for rule in table.get("rules") or []:
        rid = rule.get("id") or "?"
        for key in ("id", "source", "match", "route", "why"):
            if key not in rule:
                raise TableError(f"rule {rid}: missing {key}")
        if rule["id"] in seen:
            raise TableError(f"duplicate rule id {rule['id']}")
        seen.add(rule["id"])
        if rule["source"] not in SOURCES:
            raise TableError(f"rule {rid}: unknown source {rule['source']!r}")
        if not isinstance(rule["match"], dict) or not rule["match"]:
            raise TableError(f"rule {rid}: match must be a non-empty object")
        route = rule["route"]
        if route != "none" and not (isinstance(route, str) and route.startswith("agent:")):
            raise TableError(f"rule {rid}: route must be 'none' or 'agent:<name>', got {route!r}")
        prompt = rule.get("prompt")
        if isinstance(prompt, list):          # a list of strings keeps the JSON lines short
            if not all(isinstance(p, str) for p in prompt):
                raise TableError(f"rule {rid}: prompt list must contain only strings")
            rule["prompt"] = prompt = " ".join(p.strip() for p in prompt)
        if route.startswith("agent:") and not str(prompt or "").strip():
            raise TableError(f"rule {rid}: an agent route needs a prompt")
        if "proposes" in rule and not isinstance(rule["proposes"], bool):
            raise TableError(f"rule {rid}: proposes must be true or false")
        if rule.get("proposes") and route == "none":
            raise TableError(f"rule {rid}: proposes needs an agent route")
        if not str(rule["why"]).strip():
            raise TableError(f"rule {rid}: why is required")
    table.setdefault("rules", [])
    return table


def match_rule(table: dict, source: str, labels: dict) -> dict | None:
    """First rule of this source whose match keys all equal the alert's labels."""
    for rule in table["rules"]:
        if rule["source"] != source:
            continue
        if all(labels.get(k) == v for k, v in rule["match"].items()):
            return rule
    return None


class _Safe(dict):
    """A mapping that renders a missing key as '?' instead of raising."""

    def __missing__(self, key):
        return "?"


def render_prompt(rule: dict, alert: dict) -> str:
    labels = _Safe(alert.get("labels") or {})
    return rule["prompt"].format_map(_Safe(
        alertname=labels.get("alertname", "?"),
        status=alert.get("status") or "?",
        starts_at=alert.get("startsAt") or "?",
        labels=labels,
        annotations=_Safe(alert.get("annotations") or {}),
    ))


# ------------------------------------------------------------------ the budget

class Budget:
    """Bounds investigations: at most `per_hour` sends, and one per fingerprint per
    `repeat_after_hours`. In memory: a restart forgets, which errs on the side of looking."""

    def __init__(self, per_hour: int, repeat_after_hours: int, clock=time.time):
        self.per_hour = per_hour
        self.repeat_s = repeat_after_hours * 3600
        self.clock = clock
        self._starts: list[float] = []
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def admit(self, fingerprint: str) -> str | None:
        now = self.clock()
        with self._lock:
            self._starts = [t for t in self._starts if now - t < 3600]
            last = self._seen.get(fingerprint)
            if last is not None and now - last < self.repeat_s:
                return "repeat"
            if len(self._starts) >= self.per_hour:
                return "budget"
            self._starts.append(now)
            self._seen[fingerprint] = now
            return None
# ------------------------------------------------------------- the open-PR cap

def fetch_open_prs(repo: str, login: str, opener=urllib.request.urlopen, timeout: float = 10.0) -> list[dict]:
    """Open PRs on the public repo authored by `login`, unauthenticated (60 requests/hour is
    plenty at one call per five minutes)."""
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=100",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "dispatcher/1"})
    with opener(req, timeout=timeout) as resp:
        items = json.loads(resp.read() or b"[]")
    return [p for p in items if (p.get("user") or {}).get("login") == login]


class OpenPRs:
    """How many pull requests the implementer currently has open (§T.117, limits.open_prs_max).
    Cached for `ttl` seconds; a failed count returns None and the caller routes anyway: a
    read-only investigation never fails closed on a GitHub outage."""

    def __init__(self, repo: str, login: str, *, fetch=None, clock=time.time, ttl: float = 300.0):
        self.fetch = fetch or (lambda: fetch_open_prs(repo, login))
        self.clock, self.ttl = clock, ttl
        self._at, self._value = -1e18, 0
        self.last_error: str | None = None
        self._lock = threading.Lock()

    def count(self) -> int | None:
        with self._lock:
            now = self.clock()
            if now - self._at < self.ttl:
                return self._value
            try:
                self._value, self._at, self.last_error = len(self.fetch()), now, None
                return self._value
            except (OSError, ValueError) as err:
                self.last_error = str(err)[:200]
                return None


# ------------------------------------------------------------------ the service

@dataclass
class Config:
    table_path: str
    a2a_base: str
    n8n_base: str
    user: str
    timeout: float
    ui_url: str
    token_file: str | None = "/var/run/kagent/token"   # projected SA token; "" = none (dev)
    implementer_login: str = "arigsela-implementer[bot]"  # PR author login the open-PR cap counts
    github_repo: str = "arigsela/kubernetes"

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        return cls(
            table_path=env.get("DISPATCH_TABLE", "/etc/dispatch/dispatch.json"),
            a2a_base=env.get("KAGENT_A2A_BASE", a2a.IN_CLUSTER_BASE),
            n8n_base=env.get("N8N_WEBHOOK_BASE", "http://n8n.n8n.svc.cluster.local:5678/webhook"),
            user=env.get("DISPATCH_USER", "dispatcher"),
            timeout=float(env.get("INVESTIGATION_TIMEOUT", "300")),
            ui_url=env.get("KAGENT_UI_SESSION_URL", "https://kagent.arigsela.com/agents/{session}/chat"),
            token_file=env.get("KAGENT_TOKEN_FILE", "/var/run/kagent/token") or None,
            implementer_login=env.get("IMPLEMENTER_LOGIN", "arigsela-implementer[bot]"),
            github_repo=env.get("GITHUB_REPO", "arigsela/kubernetes"),
        )


def log_line(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True), flush=True)


def _is_timeout(err: BaseException) -> bool:
    """urllib raises a bare TimeoutError (socket.timeout) or a URLError wrapping one."""
    if isinstance(err, TimeoutError):
        return True
    reason = getattr(err, "reason", None)
    return isinstance(reason, TimeoutError) or "timed out" in str(err).lower()


def post_json(url: str, obj: dict, timeout: float = 10.0) -> None:
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


class Dispatcher:
    def __init__(self, table: dict, config: Config, *, budget: Budget | None = None,
                 send=a2a.send_message, post=post_json, log=log_line, run_async: bool = True,
                 open_prs: OpenPRs | None = None):
        self.table = table
        self.config = config
        limits = table["limits"]
        self.budget = budget or Budget(limits["investigations_per_hour"], limits["repeat_after_hours"])
        self.open_prs = open_prs or OpenPRs(config.github_repo, config.implementer_login)
        self.send, self.post, self.log, self.run_async = send, post, log, run_async

    # One decision per alert; investigations start here (in a thread when serving HTTP).
    def handle_grafana(self, payload: dict) -> list[dict]:
        decisions = []
        for alert in payload.get("alerts") or []:
            if not isinstance(alert, dict):
                continue
            decision = self.decide(alert)
            decisions.append(decision)
            self.log(decision)
            if decision["decision"] == "investigate":
                rule = next(r for r in self.table["rules"] if r["id"] == decision["rule"])
                if self.run_async:
                    threading.Thread(target=self.investigate, args=(alert, rule), daemon=True).start()
                else:
                    self.investigate(alert, rule)
        return decisions

    def decide(self, alert: dict) -> dict:
        labels = alert.get("labels") or {}
        name = labels.get("alertname") or "?"
        fingerprint = alert.get("fingerprint") or name
        base = {"event": "dispatch", "source": "grafana", "alertname": name,
                "fingerprint": fingerprint, "status": alert.get("status"), "rule": None, "route": "none"}
        rule = match_rule(self.table, "grafana", labels)
        if rule is None:
            return {**base, "decision": "unmatched"}
        base.update(rule=rule["id"], route=rule["route"])
        if rule["route"] == "none":
            return {**base, "decision": "none"}
        if alert.get("status") != "firing":
            return {**base, "decision": "resolved"}
        if rule.get("proposes"):
            n = self.open_prs.count()
            base["open_prs"] = n
            if n is None:
                self.log({"event": "open-prs", "error": self.open_prs.last_error, "rule": rule["id"]})
            elif n >= self.table["limits"]["open_prs_max"]:
                return {**base, "decision": "skip-open-prs"}
        skipped = self.budget.admit(fingerprint)
        if skipped:
            return {**base, "decision": f"skip-{skipped}"}
        return {**base, "decision": "investigate"}

    def investigate(self, alert: dict, rule: dict) -> dict:
        labels = alert.get("labels") or {}
        agent = rule["route"].split(":", 1)[1]
        url = a2a.resolve_endpoint(self.config.a2a_base, f"kagent/{agent}")
        finding = {"rule": rule["id"], "alertname": labels.get("alertname") or "?", "labels": labels,
                   "agent": agent, "session": None, "state": None, "text": None, "ui": None, "error": None}
        try:
            # A new session every time, as the dispatcher: no context_id, no task_id, no metadata (V88).
            reply = a2a.parse_result(self.send(url, render_prompt(rule, alert),
                                               user=None if self.config.token_file else self.config.user,
                                               token_file=self.config.token_file,
                                               timeout=self.config.timeout))
            finding["session"], finding["state"] = reply.context_id, reply.state
            if reply.context_id:
                finding["ui"] = self.config.ui_url.format(session=reply.context_id)
            if reply.completed:
                finding["text"] = scrub((reply.answer or "")[:ANSWER_LIMIT])
            elif reply.waiting:
                finding["text"] = ("The agent paused for input (an approval or a question). "
                                   "The dispatcher never answers; continue it in the kagent UI.")
            else:
                finding["error"] = f"task ended in {reply.state}: {scrub(reply.status_text or '')[:300]}"
        except (TimeoutError, urllib.error.URLError, OSError, a2a.A2AError, ValueError) as err:
            if _is_timeout(err):
                # The unary send gave up waiting; the task keeps running server-side and the
                # answer lands in its session. The session id only comes back with the reply,
                # so there is no link yet: say where to look.
                finding["text"] = (f"The agent is still running after {self.config.timeout:.0f}s; "
                                   "the answer lands in its session, owned by the dispatcher's service "
                                   "account: continue it with ask-hk --as-serviceaccount "
                                   "dispatcher/dispatcher --context <id>.")
            else:
                finding["error"] = scrub(str(err))[:500]
        except Exception as err:  # noqa: BLE001 - every investigation must produce a finding
            finding["error"] = f"unexpected {type(err).__name__}: {scrub(str(err))[:400]}"
        try:
            self.post(self.config.n8n_base.rstrip("/") + "/agent-findings", finding)
        except (OSError, ValueError) as err:
            finding["error"] = (finding["error"] or "") + f" | post failed: {str(err)[:200]}"
        self.log({"event": "finding", "rule": rule["id"], "alertname": finding["alertname"],
                  "agent": agent, "session": finding["session"], "state": finding["state"],
                  "error": finding["error"]})
        return finding
# ------------------------------------------------------------------ http

def make_handler(dispatcher: Dispatcher):
    class Handler(BaseHTTPRequestHandler):
        server_version = "dispatcher/1"

        def _reply(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path in ("/healthz", "/readyz"):
                self._reply(200, {"ok": True})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path != "/signal/grafana":
                self._reply(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("payload must be an object")
            except ValueError as err:
                self._reply(400, {"error": f"bad json: {err}"})
                return
            decisions = dispatcher.handle_grafana(payload)
            self._reply(200, {"decisions": [d["decision"] for d in decisions]})

        def log_message(self, fmt, *args):  # access log as JSON, like everything else
            log_line({"event": "http", "path": self.path, "message": fmt % args})

    return Handler


def serve(dispatcher: Dispatcher, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(dispatcher))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main(argv=None) -> int:
    config = Config.from_env()
    try:
        table = load_table(Path(config.table_path).read_text())
    except (OSError, ValueError) as err:
        log_line({"event": "fatal", "error": f"cannot load {config.table_path}: {err}"})
        return 1
    dispatcher = Dispatcher(table, config)
    port = int(os.environ.get("PORT", "8080"))
    server = serve(dispatcher, port)
    log_line({"event": "start", "port": port, "rules": len(table["rules"]),
              "a2a_base": config.a2a_base,
              "identity": "token" if config.token_file else config.user})
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
