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

The remediation step (§T.118, §V.96): a row may carry `remediate: {on: [<verdict>...],
agent: <AgentTemplate>, prompt: ...}`. The implementer ends its answer with a line
`verdict: <x>`; when that verdict is in `on`, and the restart budget admits it
(`limits.restarts_per_day` per rolling day, once per alert fingerprint per day), the
dispatcher opens a SECOND new session with that agent (the restarter: one gated pod delete)
and posts a second finding carrying the pending call's arguments and the ask-hk command the
owner approves or rejects it with (`--task <id>`: the laptop fetches the pause it never saw).
The approve command is printed only when the pending call is exactly one delete of the
alert's pod; deny-listed namespaces are refused before any session opens; the once-a-day
rule is keyed per namespace/workload. The dispatcher still never answers the approval.

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
# The implementer's diagnosis, the last line of its resource-alert answer (its prompt pins it).
VERDICTS = {"limit-too-low", "leak", "contention", "inconclusive"}
# Tolerates the decorations a model adds (`verdict: leak`, **verdict: leak**, '- verdict: leak.'):
# a formatting drift must not silently switch remediation off (review 2026-10-14, finding 5).
VERDICT_RE = re.compile(r"^\s*[-*`'\"]*\s*verdict:\s*([a-z-]+)[`'\"*.\s]*$", re.IGNORECASE | re.MULTILINE)
# The dispatcher refuses to open a restarter session for these namespaces BEFORE the call, and
# the restarter's prompt refuses them again (tests/dispatch/test_restarter.py pins both lists
# equal). The prompt is the first fence, the human the second; this is the zeroth.
RESTART_DENY_NAMESPACES = frozenset({
    "kube-system", "kube-public", "kube-node-lease", "vault", "postgresql", "cnpg-system", "argo-cd",
    "istio-system", "istio-ingress", "cert-manager", "external-secrets", "ate-system",
    "podcertificate-controller-system", "kagent", "kmcp", "kmcp-system", "dispatcher",
})
DNS_LABEL_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
RESTART_TOOL = "k8s_delete_resource"

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


def parse_verdict(text: str | None) -> str | None:
    """The verdict line of an answer: the LAST line of the form `verdict: <x>`, lower-cased,
    and only if `<x>` is a known verdict. The prose is never parsed; a missing or unknown
    verdict is None, which never remediates (a prompt regression shows in the log)."""
    if not text:
        return None
    found = VERDICT_RE.findall(text)
    if not found:
        return None
    verdict = found[-1].lower()
    return verdict if verdict in VERDICTS else None


def restart_key(labels: dict) -> str:
    """What "the same leak" means for the once-a-day rule: namespace/workload, the pod name
    with its replica suffix, ReplicaSet hash and StatefulSet ordinal stripped. The alert
    fingerprint would not do: a Deployment's replacement pod has a new name, and two
    containers in one pod would be two keys (review 2026-10-14, finding 3)."""
    pod = str(labels.get("pod") or "?")
    name = re.sub(r"-[a-z0-9]{5}$", "", pod)          # Deployment / DaemonSet pod suffix
    name = re.sub(r"-[a-z0-9]{8,10}$", "", name)       # ReplicaSet pod-template-hash
    name = re.sub(r"-\d+$", "", name)                  # StatefulSet ordinal
    return f"{labels.get('namespace') or '?'}/{name}"


def restart_target_problem(labels: dict) -> str | None:
    """Why the dispatcher will not even ask for a restart of this alert's pod, or None."""
    ns, pod = labels.get("namespace"), labels.get("pod")
    if not isinstance(ns, str) or not DNS_LABEL_RE.match(ns) or not isinstance(pod, str) \
            or not DNS_LABEL_RE.match(pod):
        return "the alert names no valid namespace/pod"
    if ns in RESTART_DENY_NAMESPACES:
        return f"namespace {ns} is never restarted by the dispatcher"
    return None


def pending_restart_matches(calls: list[dict], labels: dict) -> bool:
    """True only if the restarter paused on exactly one pod delete of the alert's pod. A
    single --approve approves every pending call, so anything else (another pod, another
    kind, two calls) gets a reject command, never an approve one (finding 4)."""
    if len(calls) != 1 or calls[0].get("name") != RESTART_TOOL:
        return False
    args = calls[0].get("args") or {}
    return (args.get("resource_type") == "pod" and args.get("resource_name") == labels.get("pod")
            and args.get("namespace") == labels.get("namespace"))


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
    # The restart cap (§T.118): restarter sessions per rolling day, across every row. Absent
    # means 2, never unlimited.
    limits.setdefault("restarts_per_day", 2)
    if not isinstance(limits["restarts_per_day"], int) or limits["restarts_per_day"] < 0:
        raise TableError("limits.restarts_per_day must be a non-negative integer")
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
        rem = rule.get("remediate")
        if rem is not None:
            if not isinstance(rem, dict):
                raise TableError(f"rule {rid}: remediate must be an object")
            if route == "none":
                raise TableError(f"rule {rid}: remediate needs an agent route")
            on = rem.get("on")
            if not isinstance(on, list) or not on or not set(on) <= VERDICTS:
                raise TableError(f"rule {rid}: remediate.on must be a non-empty list from {sorted(VERDICTS)}")
            if not isinstance(rem.get("agent"), str) or not rem["agent"].strip():
                raise TableError(f"rule {rid}: remediate.agent must name an AgentTemplate")
            rp = rem.get("prompt")
            if isinstance(rp, list):
                if not all(isinstance(x, str) for x in rp):
                    raise TableError(f"rule {rid}: remediate.prompt list must contain only strings")
                rem["prompt"] = rp = " ".join(x.strip() for x in rp)
            if not str(rp or "").strip():
                raise TableError(f"rule {rid}: remediate.prompt is required")
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


def render_text(template: str, alert: dict, **extra) -> str:
    """Fill a table prompt: the alert's placeholders plus any extra ones (`verdict`,
    `session` for a remediation prompt). A missing key renders as `?`."""
    labels = _Safe(alert.get("labels") or {})
    return template.format_map(_Safe(
        alertname=labels.get("alertname", "?"),
        status=alert.get("status") or "?",
        starts_at=alert.get("startsAt") or "?",
        labels=labels,
        annotations=_Safe(alert.get("annotations") or {}),
        **extra,
    ))


def render_prompt(rule: dict, alert: dict) -> str:
    return render_text(rule["prompt"], alert)


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


class RestartBudget:
    """Bounds remediations (§V.96): at most `per_day` restarter sessions per rolling 24 h,
    and one per alert fingerprint per 24 h ("recurring": the second leak in a day is a finding
    again, the fix is upstream). Charged before the send: a restart that then fails still
    counts, erring on the side of fewer restarts. In memory: a restart forgets."""

    def __init__(self, per_day: int, clock=time.time):
        self.per_day = per_day
        self.clock = clock
        self._starts: list[float] = []
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def admit(self, fingerprint: str) -> str | None:
        now = self.clock()
        with self._lock:
            self._starts = [t for t in self._starts if now - t < 86400]
            last = self._seen.get(fingerprint)
            if last is not None and now - last < 86400:
                return "recurring"
            if len(self._starts) >= self.per_day:
                return "restarts"
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
            except Exception as err:  # noqa: BLE001 - a read must never fail the webhook closed
                # http.client raises its own errors (IncompleteRead, BadStatusLine), not
                # OSError; any of them escaping here would kill the handler thread and leave
                # the alert undecided. The failure is cached for the ttl too, so an outage
                # costs one 10 s fetch per five minutes, not one per alert under the lock.
                self._value, self._at = None, now
                self.last_error = f"{type(err).__name__}: {str(err)[:160]}"
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
                 open_prs: OpenPRs | None = None, restarts: RestartBudget | None = None):
        self.table = table
        self.config = config
        limits = table["limits"]
        self.budget = budget or Budget(limits["investigations_per_hour"], limits["repeat_after_hours"])
        self.restarts = restarts or RestartBudget(limits["restarts_per_day"])
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

    def _open_session(self, agent: str, prompt: str, finding: dict) -> a2a.Reply | None:
        """Open a NEW session with `agent` as the dispatcher and fill the finding's session,
        state, ui, text and error from the reply. Returns the Reply, or None when the send
        failed or timed out (the finding says which). Never a context_id, task_id or metadata:
        every send is a fresh session and never an answer to a pause (V88)."""
        try:
            url = a2a.resolve_endpoint(self.config.a2a_base, f"kagent/{agent}")
            reply = a2a.parse_result(self.send(url, prompt,
                                               user=None if self.config.token_file else self.config.user,
                                               token_file=self.config.token_file,
                                               timeout=self.config.timeout))
        except (TimeoutError, urllib.error.URLError, OSError, a2a.A2AError, ValueError) as err:
            if _is_timeout(err):
                # The unary send gave up waiting; the task keeps running server-side and the
                # answer lands in its session. The session id only comes back with the reply,
                # so there is no link yet: say where to look.
                finding["text"] = (f"The agent is still running after {self.config.timeout:.0f}s; "
                                   "the answer lands in its session, owned by the dispatcher's service "
                                   f"account: continue it with ask-hk --agent kagent/{agent} "
                                   "--as-serviceaccount dispatcher/dispatcher --context <id>.")
            else:
                finding["error"] = scrub(str(err))[:500]
            return None
        except Exception as err:  # noqa: BLE001 - every session must produce a finding
            finding["error"] = f"unexpected {type(err).__name__}: {scrub(str(err))[:400]}"
            return None
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
        return reply

    def _post_finding(self, finding: dict, rule: dict, **log_extra) -> None:
        try:
            self.post(self.config.n8n_base.rstrip("/") + "/agent-findings", finding)
        except (OSError, ValueError) as err:
            finding["error"] = (finding["error"] or "") + f" | post failed: {str(err)[:200]}"
        self.log({"event": "finding", "rule": rule["id"], "alertname": finding["alertname"],
                  "agent": finding["agent"], "session": finding["session"], "state": finding["state"],
                  "error": finding["error"], **log_extra})

    def investigate(self, alert: dict, rule: dict) -> dict:
        labels = alert.get("labels") or {}
        agent = rule["route"].split(":", 1)[1]
        finding = {"rule": rule["id"], "alertname": labels.get("alertname") or "?", "labels": labels,
                   "agent": agent, "session": None, "state": None, "text": None, "ui": None, "error": None,
                   "verdict": None}
        reply = self._open_session(agent, render_prompt(rule, alert), finding)
        if reply is not None and reply.completed:
            # The RAW answer: the finding's text is truncated and scrubbed, and either can
            # eat the last line (finding 2).
            finding["verdict"] = parse_verdict(reply.answer)
        # The remediation decision is made, and said in the finding, BEFORE the finding is
        # posted: Slack shows the evidence first, then the pending call (§T.118).
        remediate = False
        rem = rule.get("remediate")
        if rem:
            decision = "no-match"
            if finding["verdict"] in rem["on"]:
                problem = restart_target_problem(labels)
                skipped = "target" if problem else self.restarts.admit(restart_key(labels))
                if skipped == "target":
                    decision = "skip-target"
                    finding["text"] = (finding["text"] or "") + f"\nNo restart requested: {problem}."
                elif skipped == "recurring":
                    decision = "skip-recurring"
                    finding["text"] = (finding["text"] or "") + ("\nRecurring leak: a restart was already "
                                                                 "requested for this alert in the last 24 h; "
                                                                 "no new restart. The fix is upstream.")
                elif skipped == "restarts":
                    decision = "skip-restarts"
                    finding["text"] = (finding["text"] or "") + ("\nRestart cap reached for today "
                                                                 "(restarts_per_day); no restart requested.")
                else:
                    decision, remediate = "remediate", True
            self.log({"event": "remediate", "rule": rule["id"], "verdict": finding["verdict"],
                      "decision": decision})
        self._post_finding(finding, rule)
        if remediate:
            self.remediate(alert, rule, finding["verdict"], finding["session"])
        return finding

    def remediate(self, alert: dict, rule: dict, verdict: str, evidence_session: str | None) -> dict:
        """The gated operator call (§T.118, §V.96): a NEW session with the row's
        `remediate.agent` (the restarter), which pauses on its gated delete. The second
        finding carries the pending call's exact arguments and the ask-hk command that
        approves or rejects it. The dispatcher never answers the pause (V88)."""
        labels = alert.get("labels") or {}
        rem = rule["remediate"]
        agent = rem["agent"]
        finding = {"kind": "remediation", "rule": rule["id"], "alertname": labels.get("alertname") or "?",
                   "labels": labels, "agent": agent, "session": None, "state": None, "text": None,
                   "ui": None, "error": None, "verdict": verdict, "evidence_session": evidence_session,
                   "pending": []}
        try:
            prompt = render_text(rem["prompt"], alert, verdict=verdict or "?", session=evidence_session or "?")
            reply = self._open_session(agent, prompt, finding)
            if reply is not None and reply.waiting:
                calls = reply.pending_calls
                finding["pending"] = [{"name": c["name"], "args": c["args"]} for c in calls]
                if calls:
                    # --task: the laptop never saw this pause; ask-hk fetches the request by id.
                    cmd = (f"scripts/ask-hk --agent kagent/{agent} --as-serviceaccount dispatcher/dispatcher "
                           f"--context {reply.context_id or '?'} --task {reply.task_id or '?'}")
                    lines = [f"  {c['name']} {json.dumps(c['args'], sort_keys=True)}" for c in calls]
                    lines.append(f"Evidence: {rule['route'].split(':', 1)[1]} session "
                                 f"{evidence_session or '?'} (verdict: {verdict}).")
                    if pending_restart_matches(calls, labels):
                        lines.insert(0, "Restart awaiting approval. Pending call:")
                        lines += [f"Approve: {cmd} --approve", f"Reject: {cmd} --reject \"<reason>\""]
                    else:
                        lines.insert(0, "MISMATCH: the pending call(s) are not one delete of the alert's pod "
                                        f"{labels.get('namespace')}/{labels.get('pod')}. Reject:")
                        lines.append(f"{cmd} --reject \"not the alert's pod\"")
                    lines.append("The dispatcher never answers; the kagent UI cannot see this session "
                                 "(it is owned by the dispatcher).")
                    finding["text"] = scrub("\n".join(lines))
            elif reply is None and finding["text"] and "still running" in finding["text"]:
                finding["text"] = (f"The restarter is still running after {self.config.timeout:.0f}s and its "
                                   "session id is unknown; if it pauses, nobody can approve it and nothing "
                                   "is deleted. The restart was charged to today's budget.")
        except Exception as err:  # noqa: BLE001 - a remediation must always produce a finding
            finding["error"] = f"unexpected {type(err).__name__}: {scrub(str(err))[:400]}"
        self._post_finding(finding, rule, kind="remediation")
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
