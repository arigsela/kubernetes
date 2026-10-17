"""Slack bridge: talk to a kagent agent from Slack, decide pauses from Slack, as the owner only.

Design: docs/superpowers/specs/2026-10-14-slack-bridge-design.md (§T.120, §V.98).

One Socket Mode connection (an outbound websocket the bridge opens with the app-level token,
so nothing inbound crosses the IP allow-list), one replica, one owner. A mention or a DM is a
question to an agent over A2A (scripts/kagent_a2a.py, the HITL extension, the pod's projected
token); one Slack thread is one kagent session. The send is a SendStreamingMessage: the first
event (about a second in) carries the session and task ids, so a long run has a handle in the
thread before it ends ("still working" after two minutes, the session line on a timeout), and
the answer is the stored task (GetTask) once the stream says it settled or drops. Verified
2026-10-15: kagent ignores `configuration.blocking=false` and refuses a client-chosen
contextId; streaming through the gateway is what works. A mention under a message the bridge did not
post (an n8n alert, say) hands the agent that message as context, since the agent cannot read
Slack; the first message of every session also tells it the owner's time zone, so "last night
at 11pm" means something. A paused agent gets Approve / Reject buttons in the thread; the dispatcher's "Restart awaiting approval" messages (posted by n8n with the same
Slack app) carry the same buttons, and a click on those is forwarded to the dispatcher's decide
port with the owner's email as the approver.

Stdlib only (the ConfigMap pattern of the dispatcher): the websocket client below is the
subset of RFC 6455 a client needs (handshake, masked frames, ping/pong, close).

Environment:
    SLACK_BOT_TOKEN          xoxb-… (post, react, read the channels the app is in)
    SLACK_APP_TOKEN          xapp-… (apps.connections.open only)
    OWNER_SLACK_USER_ID      the ONE Slack user the bridge listens to (U…)
    OWNER_EMAIL              what the dispatcher records as the approver
    OWNER_TIMEZONE           IANA zone the owner speaks in (America/New_York); unset = nothing said
    DEFAULT_AGENT            homelab-knowledge
    AGENTS                   comma list of agent names a first word `name:` may pick
    KAGENT_A2A_BASE          http://kagent-controller.kagent.svc.cluster.local:8083
    KAGENT_TOKEN_FILE        /var/run/kagent/token (projected, audience kagent; "" = X-User-Id dev path)
    DISPATCHER_DECIDE_BASE   http://dispatcher-decide.dispatcher.svc.cluster.local:8082
    A2A_TIMEOUT              900 (seconds one ask may take, stream + polling; ask-hk's default)
    PORT                     8080 (health only: /healthz, /readyz)
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import re
import secrets
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kagent_a2a as a2a  # noqa: E402
import eval_release  # noqa: E402

SLACK_API = "https://slack.com/api"
TEXT_LIMIT = 3900            # Slack's hard limit is 4000 characters of text per message
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_BACKOFF = 60.0
SEEN_LIMIT = 512             # event ids remembered to drop Slack's redeliveries
PROGRESS_AFTER = 120.0       # seconds before the thread hears "still working"
POLL_EVERY = 15.0            # GetTask cadence once the stream is gone
TERMINAL_STATES = {a2a.STATE_COMPLETED} | a2a.FAILED_STATES | a2a.WAITING_STATES


def log_line(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True), flush=True)


# ------------------------------------------------------------------ websocket (RFC 6455, client)

class WebSocketClosed(ConnectionError):
    """The peer closed, the handshake failed, or the socket died."""


def _xor(data: bytes, mask: bytes) -> bytes:
    if not data:
        return b""
    full = (mask * (len(data) // 4 + 1))[:len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(full, "big")).to_bytes(len(data), "big")


class WebSocket:
    """A websocket client over one socket. `recv()` returns whole messages and answers pings
    itself; `send_text()` masks (a client MUST). Not a server, not for binary data."""

    def __init__(self, sock: socket.socket, leftover: bytes = b""):
        self.sock = sock
        self._buf = leftover
        self._send_lock = threading.Lock()

    @classmethod
    def connect(cls, url: str, *, timeout: float = 30.0, idle_timeout: float = 90.0,
                context: ssl.SSLContext | None = None) -> "WebSocket":
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("ws", "wss") or not u.hostname:
            raise ValueError(f"not a websocket url: {url}")
        secure = u.scheme == "wss"
        port = u.port or (443 if secure else 80)
        raw = socket.create_connection((u.hostname, port), timeout=timeout)
        if secure:
            raw = (context or ssl.create_default_context()).wrap_socket(raw, server_hostname=u.hostname)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        host = u.hostname if port in (80, 443) else f"{u.hostname}:{port}"
        raw.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = raw.recv(4096)
            if not chunk:
                raise WebSocketClosed("handshake: connection closed")
            head += chunk
            if len(head) > 65536:
                raise WebSocketClosed("handshake: response too large")
        head, rest = head.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        status = lines[0].split(" ", 2)
        if len(status) < 2 or status[1] != "101":
            raise WebSocketClosed(f"handshake: {lines[0][:120]}")
        headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        expect = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expect:
            raise WebSocketClosed("handshake: bad Sec-WebSocket-Accept")
        raw.settimeout(idle_timeout)   # Slack pings every few seconds; silence this long = dead link
        return cls(raw, rest)

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout as err:
                raise WebSocketClosed("idle timeout") from err
            if not chunk:
                raise WebSocketClosed("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv(self) -> tuple[int, bytes]:
        """The next complete message as (opcode, payload); pings are answered here."""
        message, opcode = b"", None
        while True:
            b1, b2 = self._read_exact(2)
            fin, op, masked, length = b1 & 0x80, b1 & 0x0F, b2 & 0x80, b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else None
            payload = self._read_exact(length)
            if mask:
                payload = _xor(payload, mask)
            if op == OP_PING:
                self._send_frame(OP_PONG, payload)
                continue
            if op == OP_PONG:
                continue
            if op == OP_CLOSE:
                try:
                    self._send_frame(OP_CLOSE, payload[:2])
                except OSError:
                    pass
                raise WebSocketClosed("close frame from peer")
            if op == OP_CONT:
                message += payload
            else:
                opcode, message = op, payload
            if fin:
                return opcode, message

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = secrets.token_bytes(4)
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", n)
        with self._send_lock:
            self.sock.sendall(head + mask + _xor(payload, mask))

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode())

    def close(self) -> None:
        try:
            self._send_frame(OP_CLOSE, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------ Slack Web API

class SlackError(RuntimeError):
    pass


def http_post_json(url: str, payload: bytes, headers: dict, timeout: float) -> dict:
    """POST and decode JSON; an HTTP error becomes a SlackError with the status."""
    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        body = err.read(300).decode("utf-8", "replace").strip()
        raise SlackError(f"HTTP {err.code} from {url}: {body or err.reason}") from err


class Slack:
    """The five Web API calls the bridge makes. `post(url, payload, headers, timeout) -> dict`
    is injectable (tests)."""

    def __init__(self, bot_token: str, *, post=http_post_json, timeout: float = 15.0):
        self.bot_token = bot_token
        self.post = post
        self.timeout = timeout

    def call(self, method: str, *, form: bool = False, **params) -> dict:
        """Write methods take JSON; read methods (conversations.*) take a form body."""
        headers = {"Authorization": f"Bearer {self.bot_token}"}
        if form:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            payload = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
        else:
            headers["Content-Type"] = "application/json; charset=utf-8"
            payload = json.dumps(params).encode()
        body = self.post(f"{SLACK_API}/{method}", payload, headers, self.timeout)
        if not isinstance(body, dict) or not body.get("ok"):
            raise SlackError(f"{method}: {body.get('error') if isinstance(body, dict) else body!r:.200}")
        return body

    def post_message(self, channel: str, text: str, *, thread_ts: str | None = None,
                     blocks: list | None = None) -> dict:
        params = {"channel": channel, "text": clip(text), "unfurl_links": False, "unfurl_media": False}
        if thread_ts:
            params["thread_ts"] = thread_ts
        if blocks:
            params["blocks"] = blocks
        return self.call("chat.postMessage", **params)

    def post_long(self, channel: str, text: str, *, thread_ts: str | None = None) -> dict:
        """A text of any length, as consecutive messages (numbered when more than one)."""
        parts = split_text(text, TEXT_LIMIT - 24)
        last: dict = {}
        for i, part in enumerate(parts, 1):
            head = f"_part {i}/{len(parts)}_\n" if len(parts) > 1 else ""
            last = self.post_message(channel, head + part, thread_ts=thread_ts)
        return last

    def post_ephemeral(self, channel: str, user: str, text: str, *, thread_ts: str | None = None) -> dict:
        params = {"channel": channel, "user": user, "text": clip(text)}
        if thread_ts:
            params["thread_ts"] = thread_ts
        return self.call("chat.postEphemeral", **params)

    def update_message(self, channel: str, ts: str, text: str, *, blocks: list | None = None) -> dict:
        return self.call("chat.update", channel=channel, ts=ts, text=clip(text), blocks=blocks or [])

    def parent_message(self, channel: str, thread_ts: str) -> dict:
        """The message a thread hangs off (conversations.replies lists it first)."""
        body = self.call("conversations.replies", form=True, channel=channel, ts=thread_ts, limit=1)
        messages = body.get("messages") or []
        if not messages or messages[0].get("ts") != thread_ts:
            raise SlackError("conversations.replies: no parent in the answer")
        return messages[0]

    def react(self, channel: str, ts: str, name: str, *, add: bool = True) -> None:
        """Best effort: a missing or duplicate reaction is not an error worth a thread message."""
        try:
            self.call("reactions.add" if add else "reactions.remove", channel=channel, timestamp=ts, name=name)
        except SlackError as err:
            if not any(tag in str(err) for tag in ("already_reacted", "no_reaction", "message_not_found")):
                log_line({"event": "slack-error", "method": "reactions", "error": str(err)[:200]})


def sse_events(lines):
    """JSON objects from a text/event-stream: `data:` lines joined per event, comments and
    blank lines skipped, a non-JSON event dropped."""
    buf = []
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if not line:
            if buf:
                try:
                    yield json.loads("\n".join(buf))
                except ValueError:
                    pass
                buf = []
            continue
        if line.startswith("data:"):
            buf.append(line[5:].strip())
    if buf:
        try:
            yield json.loads("\n".join(buf))
        except ValueError:
            pass


def open_stream(url: str, payload: bytes, headers: dict, timeout: float):
    """POST and iterate the response as SSE events (a JSON body, an error page, is one event).
    `timeout` is per read: a silent stream longer than that raises, and the caller polls."""
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as err:
        body = err.read(300).decode("utf-8", "replace").strip()
        raise a2a.A2AError(f"HTTP {err.code} from {url}: {body}") from None
    with resp:
        if "text/event-stream" not in (resp.headers.get("Content-Type") or ""):
            yield json.loads(resp.read().decode("utf-8", "replace") or "{}")
            return
        yield from sse_events(resp)


def _pack(pieces: list[str], sep: str, limit: int) -> list[str]:
    """Join pieces with `sep` while the result stays within `limit`; each piece already fits."""
    out, cur = [], ""
    for piece in pieces:
        cand = piece if not cur else cur + sep + piece
        if len(cand) <= limit:
            cur = cand
        else:
            if cur:
                out.append(cur)
            cur = piece
    if cur:
        out.append(cur)
    return out


def split_text(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    """Pieces of at most `limit` characters: paragraphs kept together while they fit, a
    paragraph too long split by lines, a line too long cut hard. Nothing is dropped
    (clip() is for the texts that may not grow)."""
    text = text or ""
    if len(text) <= limit:
        return [text]
    paragraphs: list[str] = []
    for para in text.split("\n\n"):
        if len(para) <= limit:
            paragraphs.append(para)
            continue
        lines: list[str] = []
        for line in para.split("\n"):
            lines.extend([line] if len(line) <= limit else [line[i:i + limit] for i in range(0, len(line), limit)])
        paragraphs.extend(_pack(lines, "\n", limit))
    return _pack(paragraphs, "\n\n", limit)


def clip(text: str, limit: int = TEXT_LIMIT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit - 16].rstrip() + "\n… (truncated)"


def mrkdwn_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------------------------------------------ Socket Mode

class SocketMode:
    """Open the socket, read envelopes, hand each to `on_envelope`, ack it, reconnect forever.

    `open_url()` and `connect(url)` are injectable so the tests run a local plain-ws server."""

    def __init__(self, app_token: str, on_envelope, *, post=http_post_json, connect=None,
                 sleep=time.sleep, log=log_line, timeout: float = 15.0):
        self.app_token = app_token
        self.on_envelope = on_envelope
        self.post = post
        self.connect = connect or WebSocket.connect
        self.sleep = sleep
        self.log = log
        self.timeout = timeout
        self.ready = threading.Event()     # set on `hello`, cleared when the link drops

    def open_url(self) -> str:
        headers = {"Authorization": f"Bearer {self.app_token}", "Content-Type": "application/x-www-form-urlencoded"}
        body = self.post(f"{SLACK_API}/apps.connections.open", b"", headers, self.timeout)
        if not isinstance(body, dict) or not body.get("ok") or not body.get("url"):
            raise SlackError(f"apps.connections.open: {body.get('error') if isinstance(body, dict) else body!r:.200}")
        return body["url"]

    def run(self, stop: threading.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            ws = None
            try:
                ws = self.connect(self.open_url())
                self.serve(ws, stop)
                backoff = 1.0            # a clean `disconnect`: reconnect at once
                continue
            except (OSError, WebSocketClosed, SlackError, ValueError) as err:
                self.ready.clear()
                self.log({"event": "socket", "state": "down", "error": f"{type(err).__name__}: {str(err)[:200]}",
                          "retry_in": backoff})
            finally:
                if ws is not None:
                    ws.close()
            if stop.is_set():
                return
            self.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    def serve(self, ws: WebSocket, stop: threading.Event) -> None:
        """Until Slack says `disconnect` (returns) or the link dies (raises)."""
        while not stop.is_set():
            opcode, data = ws.recv()
            if opcode != OP_TEXT:
                continue
            try:
                msg = json.loads(data)
            except ValueError:
                self.log({"event": "socket", "state": "bad-json", "bytes": len(data)})
                continue
            kind = msg.get("type")
            if kind == "hello":
                self.ready.set()
                self.log({"event": "socket", "state": "hello"})
            elif kind == "disconnect":
                self.ready.clear()
                self.log({"event": "socket", "state": "disconnect", "reason": msg.get("reason")})
                return
            elif msg.get("envelope_id"):
                # Queue the work first, ack second: a crash in between makes Slack redeliver
                # (duplicates are caught by the event id and the nonces), a crash after it
                # loses this one envelope. Never do the work before the ack: Slack retries
                # after 3 s and a slow agent would be asked twice.
                self.on_envelope(msg)
                ws.send_text(json.dumps({"envelope_id": msg["envelope_id"]}))


# ------------------------------------------------------------------ the bridge

@dataclass
class Config:
    bot_token: str
    app_token: str
    owner_user_id: str
    owner_email: str
    owner_timezone: str | None = None
    default_agent: str = "homelab-knowledge"
    agents: tuple = ("homelab-knowledge",)
    a2a_base: str = a2a.IN_CLUSTER_BASE
    token_file: str | None = "/var/run/kagent/token"
    user: str = "slack-bridge"                      # X-User-Id, only without a token file (dev)
    decide_base: str = "http://dispatcher-decide.dispatcher.svc.cluster.local:8082"
    timeout: float = 900.0
    port: int = 8080
    release_path: str = eval_release.DEFAULT_PATH   # the eval gate's holds (V99); absent = unknown = send

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        agents = tuple(a.strip() for a in env.get("AGENTS", "homelab-knowledge").split(",") if a.strip())
        default = env.get("DEFAULT_AGENT", "homelab-knowledge")
        if default not in agents:
            agents = (default, *agents)
        missing = [k for k in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "OWNER_SLACK_USER_ID", "OWNER_EMAIL") if not env.get(k)]
        if missing:
            raise ValueError(f"missing environment: {', '.join(missing)}")
        return cls(
            bot_token=env["SLACK_BOT_TOKEN"], app_token=env["SLACK_APP_TOKEN"],
            owner_user_id=env["OWNER_SLACK_USER_ID"], owner_email=env["OWNER_EMAIL"],
            owner_timezone=env.get("OWNER_TIMEZONE") or None,
            default_agent=default, agents=agents,
            a2a_base=env.get("KAGENT_A2A_BASE", a2a.IN_CLUSTER_BASE),
            token_file=env.get("KAGENT_TOKEN_FILE", "/var/run/kagent/token") or None,
            user=env.get("BRIDGE_USER", "slack-bridge"),
            decide_base=env.get("DISPATCHER_DECIDE_BASE", "http://dispatcher-decide.dispatcher.svc.cluster.local:8082"),
            timeout=float(env.get("A2A_TIMEOUT", "900")),
            release_path=env.get("EVAL_RELEASE_PATH", eval_release.DEFAULT_PATH),
            port=int(env.get("PORT", "8080")),
        )


@dataclass
class Thread:
    """One Slack thread = one agent and one kagent session."""
    agent: str
    context_id: str | None = None
    task_id: str | None = None
    question_id: str | None = None   # an ask_user_request the next reply answers
    replied: bool = False            # the first reply carries the session line


@dataclass
class Pending:
    """A pause in one of the bridge's OWN sessions, waiting for a button."""
    ctx: str
    task_id: str
    agent: str
    calls: list
    nonce: str
    channel: str
    thread_ts: str


_AGENT_PREFIX = re.compile(r"^\s*([a-z0-9][a-z0-9-]*)\s*:\s*(.*)$", re.S)
_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
_VALUE = re.compile(r"^([ds]):([^:]+):([A-Za-z0-9_-]+)$")


def pick_agent(text: str, agents, default: str) -> tuple[str, str]:
    """`name: question` picks `name` when it is an allowed agent; anything else is text."""
    m = _AGENT_PREFIX.match(text or "")
    if m and m.group(1) in agents:
        return m.group(1), m.group(2).strip()
    return default, (text or "").strip()


def strip_mention(text: str, bot_user_id: str | None) -> str:
    if not bot_user_id:
        return (text or "").strip()
    return _MENTION.sub(lambda m: "" if m.group(1) == bot_user_id else m.group(0), text or "").strip()


def parse_value(value: str) -> tuple[str, str, str] | None:
    """A button value: `d:<ctx>:<nonce>` (the dispatcher's) or `s:<ctx>:<nonce>` (ours)."""
    m = _VALUE.match(value or "")
    return (m.group(1), m.group(2), m.group(3)) if m else None


def decision_blocks(text: str, value: str) -> list:
    return [{"type": "section", "text": {"type": "mrkdwn", "text": clip(text, 2900)}},
            {"type": "actions", "block_id": "decision",
             "elements": [{"type": "button", "action_id": "approve", "style": "primary",
                           "text": {"type": "plain_text", "text": "Approve"}, "value": value},
                          {"type": "button", "action_id": "reject", "style": "danger",
                           "text": {"type": "plain_text", "text": "Reject"}, "value": value}]}]


def decided_blocks(message: dict, line: str) -> list:
    """The clicked message without its buttons, plus one line saying who decided."""
    keep = [b for b in (message.get("blocks") or []) if b.get("type") != "actions"]
    if not keep:
        keep = [{"type": "section", "text": {"type": "mrkdwn", "text": clip(message.get("text") or "", 2900)}}]
    return keep + [{"type": "context", "elements": [{"type": "mrkdwn", "text": line}]}]


def message_text(message: dict) -> str:
    """A message's text, or its section blocks' text when the text is empty (Block Kit posts)."""
    text = (message.get("text") or "").strip()
    if text:
        return text
    parts = []
    for block in message.get("blocks") or []:
        t = block.get("text")
        if isinstance(t, dict) and t.get("text"):
            parts.append(t["text"].strip())
    return "\n".join(p for p in parts if p)


def message_author(message: dict) -> str:
    profile = message.get("bot_profile") or {}
    if profile.get("name"):
        return profile["name"]
    if message.get("username"):
        return message["username"]
    return f"<@{message['user']}>" if message.get("user") else "someone"


def when(ts: str | float | None, zone: datetime.tzinfo) -> str:
    try:
        return datetime.datetime.fromtimestamp(float(ts), zone).strftime("%Y-%m-%d %H:%M %Z")
    except (TypeError, ValueError, OverflowError):
        return "an unknown time"


def with_context(question: str, parent: dict | None, zone: datetime.tzinfo, zone_name: str | None,
                 zone_note: str = "", now: float | None = None) -> str:
    """The first message of a session: the question, then what the agent cannot see for itself."""
    lines = [question]
    body = message_text(parent) if parent else ""
    if body:
        lines += ["", f"Context from Slack: this was asked in the thread of a message posted "
                      f"{when(parent.get('ts'), zone)} by {message_author(parent)}:", '"""', clip(body, 3000), '"""']
    if zone_name:
        lines += ["", f"The owner's local time zone is {zone_name}{zone_note}; "
                      f"now it is {when(now if now is not None else time.time(), zone)}."]
    return "\n".join(lines)


def _is_timeout(err: BaseException) -> bool:
    if isinstance(err, TimeoutError):
        return True
    return isinstance(err, urllib.error.URLError) and isinstance(getattr(err, "reason", None), TimeoutError)


class Bridge:
    def __init__(self, config: Config, slack: Slack, *, bot_user_id: str | None = None,
                 stream=open_stream, get_task=a2a.get_task, post=http_post_json, log=log_line,
                 sleep=time.sleep, clock=time.monotonic, timer=threading.Timer):
        self.config = config
        self.slack = slack
        self.bot_user_id = bot_user_id
        self.stream = stream          # (url, payload, headers, timeout) -> iterator of JSON-RPC events
        self.get_task = get_task
        self.post = post
        self.log = log
        self.sleep = sleep
        self.clock = clock
        self.timer = timer
        self.threads: dict[tuple[str, str], Thread] = {}
        self.pending: dict[str, Pending] = {}
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._zone: datetime.tzinfo = datetime.timezone.utc
        self._zone_note = ""
        if config.owner_timezone:
            try:
                self._zone = zoneinfo.ZoneInfo(config.owner_timezone)
            except (zoneinfo.ZoneInfoNotFoundError, ValueError) as err:
                self._zone_note = " (not installed here, so times are given in UTC)"
                self.log({"event": "timezone", "zone": config.owner_timezone, "error": str(err)[:200]})

    # ----------------------------------------------------------- envelopes

    def handle_envelope(self, envelope: dict) -> None:
        kind = envelope.get("type")
        payload = envelope.get("payload") or {}
        try:
            if kind == "events_api":
                self.handle_event(payload.get("event") or {})
            elif kind == "interactive":
                self.handle_action(payload)
        except Exception as err:  # noqa: BLE001 - one bad envelope must not kill the worker
            self.log({"event": "error", "kind": kind, "error": f"{type(err).__name__}: {str(err)[:300]}"})

    def _seen_before(self, key: str) -> bool:
        with self._lock:
            if key in self._seen:
                return True
            self._seen[key] = time.time()
            if len(self._seen) > SEEN_LIMIT:
                for old in sorted(self._seen, key=self._seen.get)[:SEEN_LIMIT // 4]:
                    del self._seen[old]
            return False

    def is_for_me(self, event: dict) -> bool:
        """A DM, a mention, or a reply in a thread this bridge knows. Bot and edited messages,
        and the `message` copy of a mention (both events fire), are not."""
        if event.get("bot_id") or event.get("subtype"):
            return False
        kind = event.get("type")
        if kind == "app_mention":
            return True
        if kind != "message":
            return False
        if event.get("channel_type") == "im":
            return True
        if self.bot_user_id and f"<@{self.bot_user_id}>" in (event.get("text") or ""):
            return False                     # the app_mention event carries this one
        thread_ts = event.get("thread_ts")
        return bool(thread_ts) and (event.get("channel"), thread_ts) in self.threads

    def handle_event(self, event: dict) -> None:
        if not self.is_for_me(event):
            return
        channel, ts, user = event.get("channel"), event.get("ts"), event.get("user")
        if not channel or not ts or self._seen_before(f"{channel}:{ts}"):
            return
        thread_ts = event.get("thread_ts") or ts
        if user != self.config.owner_user_id:
            self.log({"event": "refused", "user": user, "channel": channel})
            self.slack.post_ephemeral(channel, user, "This bridge answers only its owner.", thread_ts=thread_ts)
            return
        text = strip_mention(event.get("text") or "", self.bot_user_id)
        key = (channel, thread_ts)
        with self._lock:
            thread = self.threads.get(key)
            fresh = thread is None
            if fresh:
                agent, text = pick_agent(text, self.config.agents, self.config.default_agent)
                # The eval gate's hold (V99): no new session with an agent whose last scored
                # run regressed. The owner can still reach it with ask-hk (humans are not gated).
                held = eval_release.hold(agent, eval_release.load(self.config.release_path))
                if held:
                    self.log({"event": "held", "agent": agent, "reason": held, "channel": channel, "ts": ts})
                    self.slack.post_message(channel, f"`{agent}` is held by the eval gate ({held}). "
                                            "A later sync that scores it clean releases it; "
                                            f"`scripts/ask-hk --agent kagent/{agent}` still works.",
                                            thread_ts=thread_ts)
                    return
                thread = self.threads[key] = Thread(agent=agent)
            else:
                m = _AGENT_PREFIX.match(text)
                if m and m.group(1) == thread.agent:
                    text = m.group(2).strip()
        if not text:
            self.slack.post_message(channel, "Say something after the mention, e.g. `k8s-reader: which pods restarted today?`",
                                    thread_ts=thread_ts)
            return
        if fresh:
            # A new session: the agent cannot read Slack, so the message this thread hangs off
            # (when the bridge did not start the thread) and the owner's time zone go with it.
            parent = self._parent(channel, thread_ts) if thread_ts != ts else None
            text = with_context(text, parent, self._zone, self.config.owner_timezone, self._zone_note)
        self.slack.react(channel, ts, "eyes")
        metadata = task_id = None
        if thread.question_id:
            metadata, task_id = a2a.answer_metadata(thread.question_id, text), thread.task_id
            thread.question_id = None
        done = threading.Event()

        def ids_known(ctx: str, tid: str) -> None:
            with self._lock:
                thread.context_id, thread.task_id = ctx, tid

        def still_working() -> None:
            if done.is_set():
                return
            with self._lock:
                line = "" if thread.replied else "\n" + self._continue_line(thread)
                thread.replied = True
            self.slack.post_message(channel, f":hourglass_flowing_sand: `{thread.agent}` is still working after "
                                             f"{PROGRESS_AFTER / 60:.0f} min; the answer lands here." + line, thread_ts=thread_ts)
        progress = self.timer(PROGRESS_AFTER, still_working)
        progress.daemon = True
        progress.start()
        try:
            reply, error = self._send(thread.agent, text, context_id=thread.context_id, task_id=task_id,
                                      metadata=metadata, on_ids=ids_known)
        finally:
            done.set()
            progress.cancel()
        self.slack.react(channel, ts, "eyes", add=False)
        if reply is not None and reply.context_id:
            thread.context_id = reply.context_id
        self.log({"event": "ask", "user": user, "agent": thread.agent, "session": thread.context_id,
                  "state": reply.state if reply else None, "error": error})
        if error:
            self.slack.react(channel, ts, "x")
            with self._lock:
                thread.replied = True       # the error text carries the session line
            self.slack.post_message(channel, self._error_text(thread, error), thread_ts=thread_ts)
            return
        self.slack.react(channel, ts, "white_check_mark" if reply.completed else "x" if reply.failed else "hourglass_flowing_sand")
        self._render(thread, reply, channel, thread_ts)

    def _parent(self, channel: str, thread_ts: str) -> dict | None:
        try:
            return self.slack.parent_message(channel, thread_ts)
        except (SlackError, OSError, ValueError) as err:   # a private channel, a gone message: no context
            self.log({"event": "slack-error", "method": "conversations.replies", "error": str(err)[:200]})
            return None

    def _describe(self, err: BaseException) -> str:
        if _is_timeout(err):
            return f"no answer after {self.config.timeout:.0f}s (the agent may still be running)"
        return f"{type(err).__name__}: {str(err)[:400]}"

    def _send(self, agent: str, text: str, *, context_id: str | None = None, task_id: str | None = None,
              metadata: dict | None = None, on_ids=lambda ctx, tid: None) -> tuple[a2a.Reply | None, str | None]:
        """One turn: stream until the task settles (or the stream drops), then read the stored
        task. `on_ids(ctx, task)` fires at the first event, about a second in."""
        url = a2a.resolve_endpoint(self.config.a2a_base, f"kagent/{agent}")
        request = a2a.build_send_request(text, context_id=context_id, task_id=task_id, metadata=metadata)
        request["method"] = "SendStreamingMessage"
        token = a2a.read_token_file(self.config.token_file) if self.config.token_file else None
        user = None if token else self.config.user
        headers = a2a.request_headers(user, token)
        headers["Accept"] = "text/event-stream"
        deadline = self.clock() + self.config.timeout
        ids = final = None
        try:
            for event in self.stream(url, json.dumps(request).encode(), headers, self.config.timeout):
                error = event.get("error") if isinstance(event, dict) else None
                if error:
                    raise a2a.A2AError(f"A2A error {error.get('code')}: {error.get('message', error)}"
                                       if isinstance(error, dict) else f"A2A error: {error!r:.200}")
                result = (event.get("result") if isinstance(event, dict) else None) or {}
                if isinstance(result.get("message"), dict):
                    final = result            # a direct answer, no task
                    break
                kind = next((k for k in ("task", "statusUpdate", "artifactUpdate") if isinstance(result.get(k), dict)), None)
                if kind is None:
                    continue
                inner = result[kind]
                tid, ctx = inner.get("id") if kind == "task" else inner.get("taskId"), inner.get("contextId")
                if ids is None and tid and ctx:
                    ids = (ctx, tid)
                    on_ids(ctx, tid)
                state = (inner.get("status") or {}).get("state")
                if state in TERMINAL_STATES and (kind == "task" or inner.get("final")):
                    break
                if self.clock() > deadline:
                    break
        except Exception as err:  # noqa: BLE001 - a broken stream is survivable once the ids are known
            if ids is None:
                return None, self._describe(err)
            self.log({"event": "stream", "agent": agent, "session": ids[0], "error": f"{type(err).__name__}: {str(err)[:300]}"})
        if final is not None:
            return a2a.parse_result(final), None
        if ids is None:
            return None, "the stream ended without a task"
        # The stored task is the answer (artifacts, a pending request); the stream only said when.
        while True:
            try:
                reply = a2a.parse_result(self.get_task(url, ids[1], user=user, token=token, timeout=60.0))
            except Exception as err:  # noqa: BLE001
                return None, self._describe(err)
            if reply.kind == "message" or reply.state in TERMINAL_STATES:
                return reply, None
            if self.clock() > deadline:
                return None, (f"no answer after {self.config.timeout:.0f}s (the agent may still be running: "
                              "the run continues on kagent, and a reply in this thread continues the session)")
            self.sleep(POLL_EVERY)

    def _continue_line(self, thread: Thread) -> str:
        return (f"_session {thread.context_id or '?'} · continue: scripts/ask-hk --agent kagent/{thread.agent} "
                f"--as-serviceaccount slack-bridge/slack-bridge --context {thread.context_id or '?'}_")

    def _error_text(self, thread: Thread, error: str) -> str:
        return f":x: `{thread.agent}` failed: {mrkdwn_escape(error)}\n{self._continue_line(thread)}"

    def _render(self, thread: Thread, reply: a2a.Reply, channel: str, thread_ts: str) -> None:
        """What a settled send looks like in the thread."""
        footer = "" if thread.replied else "\n" + self._continue_line(thread)
        thread.replied = True
        if reply.completed:
            self.slack.post_long(channel, (reply.answer or reply.status_text or "(no text)") + footer, thread_ts=thread_ts)
            return
        if reply.waiting and reply.pending_calls:
            nonce = secrets.token_urlsafe(12)
            with self._lock:
                self.pending[reply.context_id] = Pending(ctx=reply.context_id, task_id=reply.task_id, agent=thread.agent,
                                                         calls=reply.pending_calls, nonce=nonce, channel=channel,
                                                         thread_ts=thread_ts)
            calls = "\n".join(f"{c['name']} {json.dumps(c['args'], sort_keys=True)}" for c in reply.pending_calls)
            text = f":raised_hand: `{thread.agent}` wants to run:\n```{mrkdwn_escape(calls)}```"
            self.slack.post_message(channel, text + footer, thread_ts=thread_ts,
                                    blocks=decision_blocks(text + footer, f"s:{reply.context_id}:{nonce}"))
            return
        if reply.waiting and reply.pending_question:
            thread.question_id, thread.task_id = (reply.hitl or {}).get("id"), reply.task_id
            question = reply.status_text or reply.text or "(the agent asked something without text)"
            self.slack.post_message(channel, f":question: `{thread.agent}` asks: {question}\n_reply in this thread to answer_" + footer,
                                    thread_ts=thread_ts)
            return
        if reply.waiting:
            self.slack.post_message(channel, f":hourglass: `{thread.agent}` paused without a request the bridge can "
                                             f"answer (state {reply.state}). {self._continue_line(thread)}", thread_ts=thread_ts)
            return
        self.slack.post_message(channel, f":x: `{thread.agent}` ended in {reply.state}: "
                                         f"{mrkdwn_escape(reply.status_text or reply.text or '')[:1500]}" + footer, thread_ts=thread_ts)

    # ----------------------------------------------------------- buttons

    def handle_action(self, payload: dict) -> None:
        if payload.get("type") != "block_actions":
            return
        user = (payload.get("user") or {}).get("id")
        channel = (payload.get("channel") or {}).get("id")
        message = payload.get("message") or {}
        ts = message.get("ts")
        actions = payload.get("actions") or []
        if not (user and channel and ts and actions):
            return
        action = actions[0]
        action_id, value = action.get("action_id"), action.get("value") or ""
        thread_ts = message.get("thread_ts") or ts
        if user != self.config.owner_user_id:
            self.log({"event": "refused", "user": user, "channel": channel, "action": action_id})
            self.slack.post_ephemeral(channel, user, "Only the owner decides here.", thread_ts=thread_ts)
            return
        if action_id not in ("approve", "reject") or self._seen_before(f"action:{ts}:{action_id}:{value}"):
            return
        approved = action_id == "approve"
        parsed = parse_value(value)
        if parsed is None:
            self.slack.post_message(channel, "That button carries no decision the bridge understands.", thread_ts=thread_ts)
            return
        kind, ctx, nonce = parsed
        reason = None if approved else "rejected in Slack"
        if kind == "d":
            text, state = self.decide_dispatcher(ctx, nonce, approved=approved, reason=reason)
        else:
            text, state = self.decide_own(ctx, nonce, approved=approved, reason=reason)
        who = f"{'Approved' if approved else 'Rejected'} by {self.config.owner_email} via Slack"
        try:
            self.slack.update_message(channel, ts, (message.get("text") or "") + f"\n{who}",
                                      blocks=decided_blocks(message, who))
        except SlackError as err:
            self.log({"event": "slack-error", "method": "chat.update", "error": str(err)[:200]})
        self.slack.post_message(channel, text, thread_ts=thread_ts)
        self.log({"event": "decision", "kind": kind, "session": ctx, "approved": approved,
                  "approved_by": self.config.owner_email, "state": state})

    def decide_dispatcher(self, ctx: str, nonce: str, *, approved: bool, reason: str | None) -> tuple[str, str]:
        """Forward the click to the dispatcher's decide port (§T.120); it claims, sends and
        posts the outcome itself. Returns (text for the thread, state for the log)."""
        url = f"{self.config.decide_base.rstrip('/')}/decide/{urllib.parse.quote(ctx, safe='')}/{'approve' if approved else 'reject'}"
        body = json.dumps({"nonce": nonce, "reason": reason, "approver": self.config.owner_email}).encode()
        try:
            out = self.post(url, body, {"Content-Type": "application/json"}, 30.0)
        except SlackError as err:   # http_post_json raises SlackError on a non-2xx
            msg = str(err)
            if " 404 " in msg:
                return (":warning: the dispatcher no longer holds that restart (it restarted since). "
                        "Use the ask-hk fallback in the message above.", "missing")
            if " 409 " in msg:
                return ":warning: already decided.", "stale"
            if " 403 " in msg:
                return f":x: the dispatcher refused the decision: {mrkdwn_escape(msg[:300])}", "refused"
            return f":x: could not reach the dispatcher: {mrkdwn_escape(msg[:300])}", "error"
        except OSError as err:
            return f":x: could not reach the dispatcher: {mrkdwn_escape(str(err)[:300])}", "error"
        text = out.get("text") if isinstance(out, dict) else None
        return (f":white_check_mark: {mrkdwn_escape(text or 'decision sent')}", "sent")

    def decide_own(self, ctx: str, nonce: str, *, approved: bool, reason: str | None) -> tuple[str, str]:
        """Decide a pause in one of the bridge's own sessions: exactly the registered call ids
        on the registered task, once (the nonce claims the item)."""
        with self._lock:
            item = self.pending.get(ctx)
            if item is None:
                return ":warning: nothing pending for that session (decided already, or the bridge restarted).", "missing"
            if not secrets.compare_digest(item.nonce, nonce):
                return ":warning: already decided.", "stale"
            del self.pending[ctx]
        thread = self.threads.get((item.channel, item.thread_ts)) or Thread(agent=item.agent, context_id=ctx, replied=True)
        reply, error = self._send(item.agent, "approved" if approved else f"rejected: {reason or ''}".strip(),
                                  context_id=ctx, task_id=item.task_id,
                                  metadata=a2a.approval_metadata([c["id"] for c in item.calls], approved=approved, reason=reason))
        if error:
            if "may still be running" in error:
                return (f":hourglass: decision sent to `{item.agent}`, outcome unknown ({mrkdwn_escape(error)}). "
                        f"{self._continue_line(thread)}", "unknown")
            # Provably not delivered: put the item back so the SAME buttons work again.
            with self._lock:
                self.pending.setdefault(ctx, item)
            return f":x: decision not delivered: {mrkdwn_escape(error)}. Click again.", "error"
        self._render(thread, reply, item.channel, item.thread_ts)
        return (f"{'Approved' if approved else 'Rejected'}; `{item.agent}` answered above.", reply.state or "done")


# ------------------------------------------------------------------ health + main

def make_health_handler(ready: threading.Event):
    class Handler(BaseHTTPRequestHandler):
        server_version = "slack-bridge/1"

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                code = 200
            elif self.path == "/readyz":
                code = 200 if ready.is_set() else 503
            else:
                code = 404
            body = json.dumps({"ok": code == 200}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve_health(ready: threading.Event, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), make_health_handler(ready))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main(argv=None) -> int:
    try:
        config = Config.from_env()
    except ValueError as err:
        log_line({"event": "fatal", "error": str(err)})
        return 1
    slack = Slack(config.bot_token)
    try:
        bot_user_id = slack.call("auth.test").get("user_id")
    except SlackError as err:
        log_line({"event": "fatal", "error": f"auth.test: {err}"})
        return 1
    bridge = Bridge(config, slack, bot_user_id=bot_user_id)

    def on_envelope(envelope: dict) -> None:
        threading.Thread(target=bridge.handle_envelope, args=(envelope,), daemon=True).start()

    sm = SocketMode(config.app_token, on_envelope)
    serve_health(sm.ready, config.port)
    log_line({"event": "start", "bot_user_id": bot_user_id, "owner": config.owner_user_id,
              "agents": list(config.agents), "default_agent": config.default_agent,
              "a2a_base": config.a2a_base, "identity": "token" if config.token_file else config.user})
    stop = threading.Event()
    try:
        sm.run(stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
