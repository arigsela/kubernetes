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

Never forwards the alert to n8n, never answers an approval or a question (V88): if the
agent pauses, the finding says so and points at the kagent UI. Logs one JSON line per
decision and per finding, never the answer text.

Environment (all have defaults for the cluster):
    DISPATCH_TABLE          /etc/dispatch/dispatch.json
    KAGENT_A2A_BASE         http://kagent-controller.kagent.svc.cluster.local:8083
    N8N_WEBHOOK_BASE        http://n8n.n8n.svc.cluster.local:5678/webhook
    DISPATCH_USER           dispatcher
    INVESTIGATION_TIMEOUT   300   (seconds the A2A send may block)
    KAGENT_UI_SESSION_URL   https://kagent.arigsela.com/agents/{session}/chat
    PORT                    8080
"""
from __future__ import annotations

import json
import os
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
# ------------------------------------------------------------------ the service

@dataclass
class Config:
    table_path: str
    a2a_base: str
    n8n_base: str
    user: str
    timeout: float
    ui_url: str

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        return cls(
            table_path=env.get("DISPATCH_TABLE", "/etc/dispatch/dispatch.json"),
            a2a_base=env.get("KAGENT_A2A_BASE", a2a.IN_CLUSTER_BASE),
            n8n_base=env.get("N8N_WEBHOOK_BASE", "http://n8n.n8n.svc.cluster.local:5678/webhook"),
            user=env.get("DISPATCH_USER", "dispatcher"),
            timeout=float(env.get("INVESTIGATION_TIMEOUT", "300")),
            ui_url=env.get("KAGENT_UI_SESSION_URL", "https://kagent.arigsela.com/agents/{session}/chat"),
        )


def log_line(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True), flush=True)


def post_json(url: str, obj: dict, timeout: float = 10.0) -> None:
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


class Dispatcher:
    def __init__(self, table: dict, config: Config, *, budget: Budget | None = None,
                 send=a2a.send_message, post=post_json, log=log_line, run_async: bool = True):
        self.table = table
        self.config = config
        limits = table["limits"]
        self.budget = budget or Budget(limits["investigations_per_hour"], limits["repeat_after_hours"])
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
            reply = a2a.parse_result(self.send(url, render_prompt(rule, alert), user=self.config.user,
                                               timeout=self.config.timeout))
            finding["session"], finding["state"] = reply.context_id, reply.state
            if reply.context_id:
                finding["ui"] = self.config.ui_url.format(session=reply.context_id)
            if reply.completed:
                finding["text"] = (reply.answer or "")[:ANSWER_LIMIT]
            elif reply.waiting:
                finding["text"] = ("The agent paused for input (an approval or a question). "
                                   "The dispatcher never answers; continue it in the kagent UI.")
            else:
                finding["error"] = f"task ended in {reply.state}: {(reply.status_text or '')[:300]}"
        except (a2a.A2AError, OSError, ValueError) as err:
            finding["error"] = str(err)[:500]
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
              "a2a_base": config.a2a_base, "user": config.user})
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
