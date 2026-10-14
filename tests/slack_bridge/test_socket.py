# tests/slack_bridge/test_socket.py
"""The stdlib websocket client and the Socket Mode loop, against a fake RFC 6455 server."""
from pathlib import Path
import base64
import hashlib
import importlib.util
import json
import socket
import struct
import sys
import threading

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("slack_bridge", REPO / "scripts" / "slack_bridge.py")
sb = importlib.util.module_from_spec(_spec)
sys.modules["slack_bridge"] = sb
_spec.loader.exec_module(sb)


# ---------------------------------------------------------------- a fake server

def _frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    head = bytes([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack("!H", n)
    else:
        head += bytes([127]) + struct.pack("!Q", n)
    return head + payload


class FakeServer:
    """Accepts ONE websocket client per `accept()`, speaks unmasked frames, records what the
    client sent (unmasked). `script` is a list of frames to send after the handshake."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}/link?ticket=abc"
        self.received: list[tuple[int, bytes]] = []
        self.request_line = None
        self.conns = 0

    def handshake(self, conn: socket.socket) -> None:
        head = b""
        while b"\r\n\r\n" not in head:
            head += conn.recv(4096)
        lines = head.decode().split("\r\n")
        self.request_line = lines[0]
        key = next(ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("sec-websocket-key"))
        accept = base64.b64encode(hashlib.sha1((key + sb.WS_GUID).encode()).digest()).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        self.conns += 1

    def read_frame(self, conn: socket.socket) -> tuple[int, bytes]:
        def exact(n):
            buf = b""
            while len(buf) < n:
                chunk = conn.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError("client closed")
                buf += chunk
            return buf
        b1, b2 = exact(2)
        op, masked, length = b1 & 0x0F, b2 & 0x80, b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", exact(8))[0]
        assert masked, "a client MUST mask"
        mask = exact(4)
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(exact(length)))
        self.received.append((op, payload))
        return op, payload

    def close(self):
        self.sock.close()


def _serve(server: FakeServer, script, reads: int):
    """One connection: handshake, send the script, read `reads` client frames, keep open."""
    conn, _ = server.sock.accept()
    conn.settimeout(5)
    server.handshake(conn)
    for frame in script:
        conn.sendall(frame)
    try:
        for _ in range(reads):
            server.read_frame(conn)
    except (ConnectionError, OSError):
        pass
    return conn


# ---------------------------------------------------------------- WebSocket

def test_handshake_sends_the_path_and_query_and_checks_the_accept_key():
    srv = FakeServer()
    t = threading.Thread(target=_serve, args=(srv, [_frame(sb.OP_TEXT, b'{"type":"hello"}')], 0), daemon=True)
    t.start()
    ws = sb.WebSocket.connect(srv.url, timeout=5)
    assert srv.request_line == "GET /link?ticket=abc HTTP/1.1"
    assert ws.recv() == (sb.OP_TEXT, b'{"type":"hello"}')
    ws.close()
    srv.close()


def test_bad_accept_key_is_refused():
    srv = FakeServer()

    def bad():
        conn, _ = srv.sock.accept()
        head = b""
        while b"\r\n\r\n" not in head:
            head += conn.recv(4096)
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: nope\r\n\r\n")
        conn.recv(10)
    threading.Thread(target=bad, daemon=True).start()
    with pytest.raises(sb.WebSocketClosed, match="Accept"):
        sb.WebSocket.connect(srv.url, timeout=5)
    srv.close()


def test_non_101_is_refused():
    srv = FakeServer()

    def deny():
        conn, _ = srv.sock.accept()
        conn.recv(4096)
        conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
    threading.Thread(target=deny, daemon=True).start()
    with pytest.raises(sb.WebSocketClosed, match="403"):
        sb.WebSocket.connect(srv.url, timeout=5)
    srv.close()


@pytest.mark.parametrize("size", [0, 5, 125, 126, 65535, 65536, 70000])
def test_every_length_encoding_round_trips(size):
    payload = bytes(range(256)) * (size // 256 + 1)
    payload = payload[:size]
    srv = FakeServer()
    t = threading.Thread(target=_serve, args=(srv, [_frame(sb.OP_TEXT, payload)], 1), daemon=True)
    t.start()
    ws = sb.WebSocket.connect(srv.url, timeout=5)
    assert ws.recv() == (sb.OP_TEXT, payload)
    ws.send_text(payload.decode("latin-1"))
    t.join(5)
    op, got = srv.received[0]
    assert op == sb.OP_TEXT and got == payload.decode("latin-1").encode()
    ws.close()
    srv.close()


def test_fragments_are_reassembled_and_pings_answered_in_between():
    srv = FakeServer()
    script = [_frame(sb.OP_TEXT, b"hel", fin=False), _frame(sb.OP_PING, b"p1"), _frame(sb.OP_CONT, b"lo")]
    t = threading.Thread(target=_serve, args=(srv, script, 1), daemon=True)
    t.start()
    ws = sb.WebSocket.connect(srv.url, timeout=5)
    assert ws.recv() == (sb.OP_TEXT, b"hello")
    t.join(5)
    assert srv.received == [(sb.OP_PONG, b"p1")]
    ws.close()
    srv.close()


def test_close_frame_raises_and_is_answered():
    srv = FakeServer()
    t = threading.Thread(target=_serve, args=(srv, [_frame(sb.OP_CLOSE, struct.pack("!H", 1001))], 1), daemon=True)
    t.start()
    ws = sb.WebSocket.connect(srv.url, timeout=5)
    with pytest.raises(sb.WebSocketClosed, match="close frame"):
        ws.recv()
    t.join(5)
    assert srv.received[0][0] == sb.OP_CLOSE
    srv.close()


def test_idle_timeout_is_a_closed_link():
    srv = FakeServer()
    hold = threading.Event()

    def silent():
        conn, _ = srv.sock.accept()
        srv.handshake(conn)
        hold.wait(5)          # keep the connection open and say nothing
        conn.close()
    threading.Thread(target=silent, daemon=True).start()
    ws = sb.WebSocket.connect(srv.url, timeout=5, idle_timeout=0.2)
    with pytest.raises(sb.WebSocketClosed, match="idle"):
        ws.recv()
    hold.set()
    ws.close()
    srv.close()


def test_connect_rejects_non_websocket_urls():
    with pytest.raises(ValueError):
        sb.WebSocket.connect("https://slack.com/")


# ---------------------------------------------------------------- SocketMode

def envelope(kind: str, env_id: str, payload: dict) -> bytes:
    return _frame(sb.OP_TEXT, json.dumps({"envelope_id": env_id, "type": kind, "payload": payload,
                                          "accepts_response_payload": False}).encode())


def test_socket_mode_acks_each_envelope_after_handing_it_over_and_reconnects_on_disconnect():
    srv = FakeServer()
    first = [_frame(sb.OP_TEXT, b'{"type":"hello"}'),
             envelope("events_api", "e1", {"event": {"type": "message"}}),
             envelope("interactive", "e2", {"type": "block_actions"}),
             _frame(sb.OP_TEXT, b'{"type":"disconnect","reason":"refresh_requested"}')]
    second = [_frame(sb.OP_TEXT, b'{"type":"hello"}'), envelope("events_api", "e3", {"event": {}})]
    handed, logs, opened = [], [], []
    stop = threading.Event()

    def serve_two():
        _serve(srv, first, 2)
        conn = _serve(srv, second, 1)
        stop.set()
        conn.close()
    threading.Thread(target=serve_two, daemon=True).start()

    def post(url, payload, headers, timeout):
        opened.append((url, headers["Authorization"]))
        return {"ok": True, "url": srv.url}

    sm = sb.SocketMode("xapp-1", handed.append, post=post, sleep=lambda s: None, log=logs.append)
    sm.run(stop)
    assert [h["envelope_id"] for h in handed] == ["e1", "e2", "e3"]
    acks = [json.loads(p) for op, p in srv.received if op == sb.OP_TEXT]
    assert acks == [{"envelope_id": "e1"}, {"envelope_id": "e2"}, {"envelope_id": "e3"}]
    assert srv.conns == 2 and len(opened) == 2
    assert opened[0] == (f"{sb.SLACK_API}/apps.connections.open", "Bearer xapp-1")
    states = [x["state"] for x in logs if x["event"] == "socket"]
    assert states[:3] == ["hello", "disconnect", "hello"]
    srv.close()


def test_socket_mode_backs_off_when_the_open_call_fails_and_marks_not_ready():
    sleeps, logs = [], []
    stop = threading.Event()
    calls = {"n": 0}

    def post(url, payload, headers, timeout):
        calls["n"] += 1
        if calls["n"] >= 4:
            stop.set()
        return {"ok": False, "error": "invalid_auth"}

    sm = sb.SocketMode("xapp-bad", lambda e: None, post=post, sleep=sleeps.append, log=logs.append)
    sm.run(stop)
    assert not sm.ready.is_set()
    assert sleeps[:3] == [1.0, 2.0, 4.0]
    assert "invalid_auth" in logs[0]["error"]


def test_ack_follows_the_handoff_not_the_work():
    """The handler is called before the ack is written: a slow handler must not delay the ack,
    so main() hands the envelope to a thread; here the order is what is asserted."""
    srv = FakeServer()
    stop = threading.Event()
    order = []
    script = [_frame(sb.OP_TEXT, b'{"type":"hello"}'), envelope("events_api", "e1", {})]

    def serve():
        conn = _serve(srv, script, 1)
        order.append("acked")
        stop.set()
        conn.close()
    threading.Thread(target=serve, daemon=True).start()
    sm = sb.SocketMode("xapp", lambda e: order.append("handed"), post=lambda *a: {"ok": True, "url": srv.url},
                       sleep=lambda s: None, log=lambda x: None)
    sm.run(stop)
    assert order == ["handed", "acked"]
    srv.close()
