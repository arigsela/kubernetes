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
by the caller identity, so a follow-up must use the same X-User-Id. In the
controller's default `insecure` auth mode that header is the whole identity: a
missing header means `admin@kagent.dev` (go/core/internal/httpserver/auth/authn.go).

The unary send blocks until the task settles (completed, failed, or waiting for
input), and the gateway returns the stored task. One artifact per ADK event
(Go ADK `OutputArtifactPerEvent`): text parts are what the agent said, data
parts are tool calls and results. The answer is the text parts, as in the
kagent CLI (go/core/cli/internal/commands/session/invoke.go, sendResultText).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass

# The kagent-mcp host routes /agents/kagent/ to the controller (httproute-mcp.yaml)
# behind the Gateway IP allow-list. In-cluster callers use the Service directly.
PUBLIC_BASE = "https://kagent-mcp.arigsela.com"
IN_CLUSTER_BASE = "http://kagent-controller.kagent.svc.cluster.local:8083"
DEFAULT_USER = "admin@kagent.dev"
A2A_VERSION = "1.0"
# Multi-step agents (docs walk + live reads) take minutes; the unary call is
# silent until the task settles. Nothing on the path times out first: Istio
# 1.30 sets no route timeout and disables the stream idle timeout.
DEFAULT_TIMEOUT = 900.0

STATE_COMPLETED = "TASK_STATE_COMPLETED"
FAILED_STATES = {"TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}
WAITING_STATES = {"TASK_STATE_INPUT_REQUIRED", "TASK_STATE_AUTH_REQUIRED"}

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
                       message_id: str | None = None, rpc_id: str | None = None) -> dict:
    message = {
        "messageId": message_id or str(uuid.uuid4()),
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }
    if context_id:
        message["contextId"] = context_id
    return {"jsonrpc": "2.0", "id": rpc_id or str(uuid.uuid4()),
            "method": "SendMessage", "params": {"message": message}}


def request_headers(user: str | None) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               # Not enforced by the alpha11 JSON-RPC handler, but the kagent CLI
               # and the a2a-go client always send it (go/core/internal/utils/a2a_version.go).
               "A2A-Version": A2A_VERSION}
    if user:
        headers["X-User-Id"] = user
    return headers


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
                 user: str | None = DEFAULT_USER, timeout: float = DEFAULT_TIMEOUT,
                 transport=None, sleep=time.sleep, clock=time.monotonic,
                 retry_deadline: float = RETRY_DEADLINE) -> dict:
    """POST one SendMessage and return the JSON-RPC `result` ({"task"|"message": ...}).

    `transport(url, payload_bytes, headers, timeout) -> dict` is injectable for tests.
    A KAGENT_SEND_NOT_ACCEPTED rejection is retried with the byte-identical
    request (same messageId) until `retry_deadline`; anything else raises."""
    post = transport or http_post
    payload = json.dumps(build_send_request(text, context_id=context_id)).encode()
    headers = request_headers(user)
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


def parse_result(result: dict) -> Reply:
    """Reduce a SendMessage result to a Reply. Raises A2AError on an unknown shape."""
    if isinstance(result.get("task"), dict):
        task = result["task"]
        status = task.get("status") or {}
        texts = [t for t in (parts_text(a.get("parts")) for a in (task.get("artifacts") or [])
                             if isinstance(a, dict)) if t]
        return Reply(kind="task", state=status.get("state"), context_id=task.get("contextId"),
                     task_id=task.get("id"), text="\n".join(texts) if texts else None,
                     status_text=parts_text((status.get("message") or {}).get("parts")))
    if isinstance(result.get("message"), dict):
        msg = result["message"]
        return Reply(kind="message", state=None, context_id=msg.get("contextId"),
                     task_id=msg.get("taskId"), text=parts_text(msg.get("parts")),
                     status_text=None)
    raise A2AError(f"unrecognised SendMessage result (keys: {sorted(result)})")
