"""Minimal A2A v1 JSON-RPC client for the kagent 1.0 controller (stdlib only).

Shared by scripts/ask-hk and scripts/capture-eval-answers.py.

kagent 1.0 has no per-agent Service. The controller serves every Agent's A2A
endpoint on its API listener (kagent-controller:8083):

    POST <base>/agents/<namespace>/<name>                                  JSON-RPC
    GET  <base>/agents/<namespace>/<name>/.well-known/agent-card.json      agent card

Sources (kagent v1.0.0-alpha11): docs/architecture/a2a-transports.md,
docs/architecture/a2a-gateway.md, go/core/internal/a2agateway/http.go. Wire
types come from the a2a-go v2.6.0 SDK the controller pins (a2a/core.go,
internal/jsonrpc/jsonrpc.go). This is A2A **v1**, not the 0.3 protocol kagent
0.10 spoke, and the differences break 0.3 clients:

- method `SendMessage` (was `message/send`); roles `ROLE_USER`/`ROLE_AGENT`;
- a part is `{"text": ...}` or `{"data": ...}` with no `kind` field;
- the result is wrapped: `{"task": {...}}` or `{"message": {...}}`;
- task states are `TASK_STATE_COMPLETED`, `TASK_STATE_FAILED`, ...

A send without a contextId creates a Session; the returned contextId IS the
Session id, and sending it back continues the conversation. Sessions are owned
by the verified identity (the Dex `email` for a human, system:serviceaccount:<ns>:<sa>
for an automation), so a follow-up must present the same identity. X-User-Id is honoured
only where no mesh sits in front of the controller (a port-forward). In the
controller's default `insecure` auth mode that header is the whole identity: a
missing header means `admin@kagent.dev` (go/core/internal/httpserver/auth/authn.go).

Every send requests kagent's human-in-the-loop extension
(HITL_EXTENSION, `A2A-Extensions` header + the message's `extensions` list).
Without it a gated tool call still pauses the task, but the pause carries no
request id, so nothing can answer it and it waits until cancelled
(docs: examples/a2a-agents, agents/human-in-the-loop). With it, a paused task's
status.message.metadata[HITL_EXTENSION] is a `tool_approval_request` (each
pending call has an `id`, `name`, `args`) or an `ask_user_request`, and the
reply is a message on the same taskId + contextId whose metadata carries a
`tool_approval_response` (`approvals`, one per call) or an `ask_user_response`.
The reply's `extensions` list must name the URI again or kagent ignores the
payload and resumes without a decision.

The unary send blocks until the task settles (completed, failed, or waiting for
input), and the gateway returns the stored task. One artifact per ADK event
(Go ADK `OutputArtifactPerEvent`): text parts are what the agent said, data
parts are tool calls and results. The answer is the text parts, as in the
kagent CLI (go/core/cli/internal/commands/session/invoke.go, sendResultText).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

# The kagent-mcp host routes /agents/kagent/ to the controller (httproute-mcp.yaml)
# behind the Gateway IP allow-list. In-cluster callers use the Service directly.
PUBLIC_BASE = "https://kagent-mcp.arigsela.com"
IN_CLUSTER_BASE = "http://kagent-controller.kagent.svc.cluster.local:8083"
DEFAULT_USER = "admin@kagent.dev"

# Callers as principals (docs/superpowers/specs/2026-10-13-callers-as-principals-and-eval-gate-design.md).
# The mesh verifies the bearer token and sets X-User-Id from its claim; a client that sends a
# token must not also name itself. Humans reuse kubectl's Dex login (kubelogin cache);
# automations present a projected service account token with this audience.
DEX_ISSUER = "https://dex.arigsela.com"
DEX_CLIENT_ID = "kubernetes"
TOKEN_AUDIENCE = "kagent"
A2A_VERSION = "1.0"
# kagent's HITL A2A extension (go/core/internal/a2a/hitl): tool approvals and ask_user.
HITL_EXTENSION = "https://kagent.dev/extensions/hitl/v1"
HITL_TOOL_APPROVAL_REQUEST = "tool_approval_request"
HITL_TOOL_APPROVAL_RESPONSE = "tool_approval_response"
HITL_ASK_USER_REQUEST = "ask_user_request"
HITL_ASK_USER_RESPONSE = "ask_user_response"
# Multi-step agents (docs walk + live reads) take minutes; the unary call is
# silent until the task settles. Nothing on the path times out first: Istio
# 1.30 sets no route timeout and disables the stream idle timeout.
DEFAULT_TIMEOUT = 900.0

STATE_COMPLETED = "TASK_STATE_COMPLETED"
FAILED_STATES = {"TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}
WAITING_STATES = {"TASK_STATE_INPUT_REQUIRED", "TASK_STATE_AUTH_REQUIRED"}
# A task in any other state (SUBMITTED, WORKING, unknown) is still running: its artifacts
# are not the answer yet. SendMessage is asked to block, but a task handed back early is
# polled with GetTask until it settles (wait_for_task). §B.35: the eval gate scored one
# such task as "no answer" and failed a canary question the agent had answered correctly.
SETTLED_STATES = {STATE_COMPLETED} | FAILED_STATES | WAITING_STATES
POLL_INTERVAL = 3.0

# a2a-go maps ErrUnsupportedOperation to -32004. kagent returns it with this
# ErrorInfo reason only when the input was provably NOT dispatched (the session
# was busy pausing or suspending), so resending the identical request is safe.
# Every other error, including a transport error, must not be blindly retried:
# the input may already be running (docs/architecture/a2a-gateway.md).
CODE_UNSUPPORTED_OPERATION = -32004
REASON_SEND_NOT_ACCEPTED = "KAGENT_SEND_NOT_ACCEPTED"
RETRY_DEADLINE = 30.0


class A2AError(RuntimeError):
    """A JSON-RPC error, an HTTP error, or a result this client cannot read."""

    def __init__(self, message: str, *, code: int | None = None, data=None):
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass
class Reply:
    """What a SendMessage returned, reduced to what callers print or score."""

    kind: str                 # "task" or "message"
    state: str | None         # TASK_STATE_* for a task, None for a direct message
    context_id: str | None    # the Session id; pass it back to continue
    task_id: str | None
    text: str | None          # text parts of the artifacts (or the message)
    status_text: str | None   # text parts of task.status.message
    hitl: dict | None = None  # status.message.metadata[HITL_EXTENSION] of a waiting task
    tool_calls: list = None   # [{name, args}] the agent CALLED, from the artifacts' data parts

    def __post_init__(self):
        if self.tool_calls is None:
            self.tool_calls = []

    @property
    def failed(self) -> bool:
        return self.state in FAILED_STATES

    @property
    def waiting(self) -> bool:
        return self.state in WAITING_STATES

    @property
    def completed(self) -> bool:
        return self.kind == "message" or self.state == STATE_COMPLETED

    @property
    def settled(self) -> bool:
        """Nothing more will arrive for this turn without another message: a direct message,
        or a task that completed, failed or paused. False for SUBMITTED/WORKING."""
        return self.kind == "message" or self.state in SETTLED_STATES

    @property
    def pending_calls(self) -> list[dict]:
        """The tool calls a waiting task wants approved: [{id, name, args}, ...].

        Empty unless the task paused with a tool_approval_request. alpha11 lists
        them under `tools` (go/core/internal/a2a/hitl); the docs say `calls`."""
        if not self.hitl or self.hitl.get("type") != HITL_TOOL_APPROVAL_REQUEST:
            return []
        calls = self.hitl.get("tools")
        if not isinstance(calls, list):
            calls = self.hitl.get("calls")
        return [{"id": c.get("id"), "name": c.get("name"), "args": c.get("args") or {}}
                for c in (calls or []) if isinstance(c, dict) and c.get("id")]

    @property
    def pending_question(self) -> bool:
        """True when the task paused with an ask_user_request (the agent asked something)."""
        return bool(self.hitl) and self.hitl.get("type") == HITL_ASK_USER_REQUEST

    @property
    def answer(self) -> str | None:
        """The agent's answer: only a completed task (or a direct message) has one.

        A failed, canceled or rejected task puts its error text in
        status.message; that is a tooling failure, not an answer. A task that
        is still working or waiting for input has not answered yet."""
        if not self.completed:
            return None
        return self.text or self.status_text


def agent_path(agent: str) -> str:
    """`namespace/name` -> `/agents/namespace/name`."""
    ns, sep, name = agent.partition("/")
    if not sep or not ns or not name or "/" in name:
        raise ValueError(f"agent must be namespace/name, got {agent!r}")
    return f"/agents/{urllib.parse.quote(ns, safe='')}/{urllib.parse.quote(name, safe='')}"


def resolve_endpoint(endpoint: str, agent: str) -> str:
    """A bare origin gets /agents/<ns>/<name> appended; a URL with a path is used as-is.

    So `--endpoint http://127.0.0.1:8083` (a port-forward) and a full agent URL
    both work."""
    parsed = urllib.parse.urlsplit(endpoint)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"endpoint must be an absolute http(s) URL, got {endpoint!r}")
    if parsed.path in ("", "/"):
        return endpoint.rstrip("/") + agent_path(agent)
    return endpoint


def build_send_request(text: str, *, context_id: str | None = None,
                       task_id: str | None = None, metadata: dict | None = None,
                       message_id: str | None = None, rpc_id: str | None = None) -> dict:
    """A SendMessage. `task_id` + `metadata` make it a reply to a paused task."""
    message = {
        "messageId": message_id or str(uuid.uuid4()),
        "role": "ROLE_USER",
        "parts": [{"text": text}],
        # Request HITL on every message: an approval pause raised during this
        # turn is only answerable if the message that started the turn asked.
        "extensions": [HITL_EXTENSION],
    }
    if context_id:
        message["contextId"] = context_id
    if task_id:
        message["taskId"] = task_id
    if metadata:
        message["metadata"] = metadata
    # blocking: return the settled task, not a SUBMITTED/WORKING one (a2a v1
    # SendMessageConfiguration). The caller still polls if the server returns early.
    return {"jsonrpc": "2.0", "id": rpc_id or str(uuid.uuid4()),
            "method": "SendMessage",
            "params": {"message": message, "configuration": {"blocking": True}}}


def build_get_task_request(task_id: str, rpc_id: str | None = None) -> dict:
    """A GetTask: the stored task, with its status.message (and so a pending approval)."""
    return {"jsonrpc": "2.0", "id": rpc_id or str(uuid.uuid4()),
            "method": "GetTask", "params": {"id": task_id}}


def get_task(endpoint: str, task_id: str, *, user: str | None = DEFAULT_USER,
             token: str | None = None, token_file: str | None = None,
             timeout: float = 60.0, transport=None) -> dict:
    """Fetch a task by id and return it in SendMessage's result shape ({"task": ...}),
    so parse_result reads it. How a laptop learns what a session it never saw is waiting
    on (the dispatcher's restarter sessions, §T.118). Verified live 2026-10-14: the gateway
    answers GetTask {"id"} with the bare task; {"name": "tasks/<id>"} is invalid params."""
    post = transport or http_post
    payload = json.dumps(build_get_task_request(task_id)).encode()
    headers = request_headers(user, token or (read_token_file(token_file) if token_file else None))
    body = post(endpoint, payload, headers, timeout)
    if not isinstance(body, dict):
        raise A2AError(f"not a JSON-RPC response: {body!r:.200}")
    error = body.get("error")
    if error:
        raise A2AError(f"A2A error {error.get('code') if isinstance(error, dict) else '?'}: "
                       f"{error.get('message', error) if isinstance(error, dict) else error}")
    result = body.get("result")
    if not isinstance(result, dict) or "status" not in result:
        raise A2AError("GetTask response has no task")
    return {"task": result}


def wait_for_task(endpoint: str, task_id: str, *, user: str | None = DEFAULT_USER,
                  token: str | None = None, token_file: str | None = None,
                  timeout: float = DEFAULT_TIMEOUT, interval: float = POLL_INTERVAL,
                  transport=None, sleep=time.sleep, clock=time.monotonic) -> dict:
    """GetTask until the task settles (completed, failed or paused) or `timeout` passes.

    Returns the last result in SendMessage's shape ({"task": ...}); the caller reads
    parse_result(...).settled to tell a settled task from one that ran out of time. One
    GetTask is always made, so a task that is already settled costs a single round trip."""
    deadline = clock() + timeout
    while True:
        result = get_task(endpoint, task_id, user=user, token=token, token_file=token_file,
                          timeout=min(60.0, timeout), transport=transport)
        if parse_result(result).settled:
            return result
        remaining = deadline - clock()
        if remaining <= 0:
            return result
        sleep(min(interval, remaining))


def approval_metadata(call_ids: list[str], *, approved: bool, reason: str | None = None) -> dict:
    """message.metadata for a tool_approval_response deciding every listed call the same way.

    kagent requires a decision for every call in the request; a partial reply
    is a protocol error. `reason` (rejections only) is shown to the agent so it
    can adapt instead of just failing."""
    approvals = []
    for cid in call_ids:
        entry = {"id": cid, "approved": approved}
        if not approved and reason:
            entry["rejection_reason"] = reason
        approvals.append(entry)
    return {HITL_EXTENSION: {"type": HITL_TOOL_APPROVAL_RESPONSE, "approvals": approvals}}


def answer_metadata(request_id: str, answer: str) -> dict:
    """message.metadata for an ask_user_response to the request with this id."""
    return {HITL_EXTENSION: {"type": HITL_ASK_USER_RESPONSE, "id": request_id,
                             "answers": [{"answer": [answer]}]}}


def request_headers(user: str | None, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               # Not enforced by the alpha11 JSON-RPC handler, but the kagent CLI
               # and the a2a-go client always send it (go/core/internal/utils/a2a_version.go).
               "A2A-Version": A2A_VERSION,
               # The HITL extension is negotiated per request (a2a-go reads this header).
               "A2A-Extensions": HITL_EXTENSION}
    if token:
        headers["Authorization"] = f"Bearer {token}"   # identity = the verified claim (mesh)
    elif user:
        headers["X-User-Id"] = user                     # the no-mesh dev path (port-forward)
    return headers


def read_token_file(path: str) -> str:
    return Path(path).read_text().strip()


def _run_kubectl(cmd: list[str], run) -> str:
    r = run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def kubelogin_token(run=subprocess.run) -> str:
    """The Dex ID token kubectl caches (int128/kubelogin). The browser opens only when the
    cached refresh token is gone. The ExecCredential's status.token is the ID token."""
    out = _run_kubectl(["kubectl", "oidc-login", "get-token",
                        f"--oidc-issuer-url={DEX_ISSUER}", f"--oidc-client-id={DEX_CLIENT_ID}",
                        "--oidc-extra-scope=email", "--oidc-extra-scope=offline_access"], run)
    return json.loads(out)["status"]["token"]


def serviceaccount_token(ref: str, run=subprocess.run) -> str:
    """A 10-minute projected token for namespace/name with the kagent audience: an audited
    TokenRequest, which is how the owner continues a session an automation owns."""
    ns, _, name = ref.partition("/")
    if not ns or not name or "/" in name:
        raise ValueError(f"--as-serviceaccount wants namespace/name, got {ref!r}")
    return _run_kubectl(["kubectl", "create", "token", name, "-n", ns,
                         "--audience", TOKEN_AUDIENCE, "--duration", "10m"], run).strip()


def acquire_token(*, token: str | None = None, token_file: str | None = None,
                  as_serviceaccount: str | None = None, env=os.environ, run=subprocess.run) -> str:
    """Explicit token, then a file, then $KAGENT_TOKEN, $KAGENT_TOKEN_FILE, a service account
    to impersonate, else the kubelogin cache."""
    if token:
        return token
    if token_file:
        return read_token_file(token_file)
    if env.get("KAGENT_TOKEN"):
        return env["KAGENT_TOKEN"]
    if env.get("KAGENT_TOKEN_FILE"):
        return read_token_file(env["KAGENT_TOKEN_FILE"])
    if as_serviceaccount:
        return serviceaccount_token(as_serviceaccount, run)
    return kubelogin_token(run)


def http_post(url: str, payload: bytes, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        # JSON-RPC errors come back as HTTP 200. A non-2xx is the path, not the
        # agent: 403 "RBAC: access denied" is the Gateway allow-list, 404 a
        # missing route or Agent, 401 an auth mode other than insecure.
        body = err.read(300).decode("utf-8", "replace").strip()
        raise A2AError(f"HTTP {err.code} from {url}: {body or err.reason}") from err


def _send_not_accepted(error: dict) -> float | None:
    """Seconds to wait before resending, or None if this error is not a proven rejection."""
    if error.get("code") != CODE_UNSUPPORTED_OPERATION:
        return None
    data = error.get("data")
    details = data if isinstance(data, list) else [data]
    for detail in details:
        meta = detail.get("metadata") if isinstance(detail, dict) else None
        if isinstance(meta, dict) and meta.get("reason") == REASON_SEND_NOT_ACCEPTED:
            try:
                return max(int(meta.get("retryAfterMs", 100)), 100) / 1000
            except (TypeError, ValueError):
                return 0.1
    return None


def send_message(endpoint: str, text: str, *, context_id: str | None = None,
                 task_id: str | None = None, metadata: dict | None = None,
                 user: str | None = DEFAULT_USER, token: str | None = None,
                 token_file: str | None = None, timeout: float = DEFAULT_TIMEOUT,
                 transport=None, sleep=time.sleep, clock=time.monotonic,
                 retry_deadline: float = RETRY_DEADLINE) -> dict:
    """POST one SendMessage and return the JSON-RPC `result` ({"task"|"message": ...}).

    `task_id` + `metadata` (see approval_metadata / answer_metadata) answer a
    paused task instead of starting a new turn.
    `transport(url, payload_bytes, headers, timeout) -> dict` is injectable for tests.
    A KAGENT_SEND_NOT_ACCEPTED rejection is retried with the byte-identical
    request (same messageId) until `retry_deadline`; anything else raises."""
    post = transport or http_post
    payload = json.dumps(build_send_request(text, context_id=context_id, task_id=task_id,
                                            metadata=metadata)).encode()
    headers = request_headers(user, token or (read_token_file(token_file) if token_file else None))
    give_up = clock() + retry_deadline
    while True:
        body = post(endpoint, payload, headers, timeout)
        if not isinstance(body, dict):
            raise A2AError(f"not a JSON-RPC response: {body!r:.200}")
        error = body.get("error")
        if not error:
            result = body.get("result")
            if not isinstance(result, dict):
                raise A2AError("JSON-RPC response has no result")
            return result
        if not isinstance(error, dict):
            raise A2AError(f"A2A error: {error!r:.200}")
        wait = _send_not_accepted(error)
        if wait is None or clock() + wait > give_up:
            raise A2AError(f"A2A error {error.get('code')}: {error.get('message', error)}",
                           code=error.get("code"), data=error.get("data"))
        sleep(wait)


def parts_text(parts) -> str | None:
    """Join the text parts. Data parts (tool calls and results) are not the answer."""
    texts = [p["text"] for p in (parts or [])
             if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"]]
    return "\n".join(texts) if texts else None


def parts_tool_calls(parts) -> list[dict]:
    """The tool CALLS among the data parts: {id, name, args} (a result part carries `response`
    instead of `args` and is skipped). Verified live on alpha11 (k8s-reader, 2026-10-17):
    one data part per call, one per result, in order. The eval gate scores these (V99)."""
    out = []
    for p in parts or []:
        d = p.get("data") if isinstance(p, dict) else None
        if isinstance(d, dict) and isinstance(d.get("name"), str) and "args" in d and "response" not in d:
            out.append({"name": d["name"], "args": d.get("args") if isinstance(d.get("args"), dict) else {}})
    return out


def parse_result(result: dict) -> Reply:
    """Reduce a SendMessage result to a Reply. Raises A2AError on an unknown shape."""
    if isinstance(result.get("task"), dict):
        task = result["task"]
        status = task.get("status") or {}
        texts = [t for t in (parts_text(a.get("parts")) for a in (task.get("artifacts") or [])
                             if isinstance(a, dict)) if t]
        status_msg = status.get("message") or {}
        hitl = (status_msg.get("metadata") or {}).get(HITL_EXTENSION)
        calls = [c for a in (task.get("artifacts") or []) if isinstance(a, dict)
                 for c in parts_tool_calls(a.get("parts"))]
        return Reply(kind="task", state=status.get("state"), context_id=task.get("contextId"),
                     task_id=task.get("id"), text="\n".join(texts) if texts else None,
                     status_text=parts_text(status_msg.get("parts")),
                     hitl=hitl if isinstance(hitl, dict) else None, tool_calls=calls)
    if isinstance(result.get("message"), dict):
        msg = result["message"]
        return Reply(kind="message", state=None, context_id=msg.get("contextId"),
                     task_id=msg.get("taskId"), text=parts_text(msg.get("parts")),
                     status_text=None)
    raise A2AError(f"unrecognised SendMessage result (keys: {sorted(result)})")
