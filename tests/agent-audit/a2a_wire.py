"""A minimal protobuf ENCODER for tests: the inverse of agent-audit.py's v1 reader.

The kagent 1.0 scenarios in fixtures/kagent-v1/events.json were written by kagent's
own code. This builds the edge cases that code does not produce on demand (a Python
ADK runtime's DataParts, nested approvals, corrupt fields). Field numbers are the
ones the reader uses: lf.a2a.v1 (a2a-go v2.6.0 a2apb/v1) and google.protobuf.Struct.
"""
from __future__ import annotations

import struct


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _key(number: int, wire: int) -> bytes:
    return _varint(number << 3 | wire)


def _len(number: int, payload: bytes) -> bytes:
    return _key(number, 2) + _varint(len(payload)) + payload


def _str(number: int, text: str) -> bytes:
    return _len(number, text.encode())


def value(v) -> bytes:
    """google.protobuf.Value"""
    if v is None:
        return _key(1, 0) + _varint(0)
    if isinstance(v, bool):
        return _key(4, 0) + _varint(int(v))
    if isinstance(v, (int, float)):
        return _key(2, 1) + struct.pack("<d", float(v))
    if isinstance(v, str):
        return _str(3, v)
    if isinstance(v, dict):
        return _len(5, pb_struct(v))
    if isinstance(v, list):
        return _len(6, b"".join(_len(1, value(i)) for i in v))
    raise TypeError(type(v))


def pb_struct(d: dict) -> bytes:
    """google.protobuf.Struct"""
    return b"".join(_len(1, _str(1, k) + _len(2, value(v))) for k, v in d.items())


def part(text: str | None = None, data=None, metadata: dict | None = None) -> bytes:
    out = b""
    if text is not None:
        out += _str(1, text)
    if data is not None:
        out += _len(4, value(data))
    if metadata:
        out += _len(5, pb_struct(metadata))
    return out


def message(message_id: str, role: int, parts: list[bytes], metadata: dict | None = None,
            task_id: str = "", context_id: str = "", extensions: list[str] = ()) -> bytes:
    out = _str(1, message_id) + (_str(2, context_id) if context_id else b"")
    out += (_str(3, task_id) if task_id else b"") + _key(4, 0) + _varint(role)
    out += b"".join(_len(5, p) for p in parts)
    if metadata:
        out += _len(6, pb_struct(metadata))
    return out + b"".join(_str(7, e) for e in extensions)


def artifact(artifact_id: str, parts: list[bytes], metadata: dict | None = None) -> bytes:
    out = _str(1, artifact_id) + b"".join(_len(4, p) for p in parts)
    return out + (_len(5, pb_struct(metadata)) if metadata else b"")


def status(state: int, msg: bytes | None = None) -> bytes:
    return _key(1, 0) + _varint(state) + (_len(2, msg) if msg is not None else b"")


def stream_message(msg: bytes) -> bytes:
    return _len(2, msg)


def stream_status(task_id: str, context_id: str, state: int, msg: bytes | None = None) -> bytes:
    return _len(3, _str(1, task_id) + _str(2, context_id) + _len(3, status(state, msg)))


def stream_artifact(task_id: str, context_id: str, art: bytes, append: bool = False,
                    last_chunk: bool = True) -> bytes:
    body = _str(1, task_id) + _str(2, context_id) + _len(3, art)
    body += (_key(4, 0) + _varint(1) if append else b"") + (_key(5, 0) + _varint(1) if last_chunk else b"")
    return _len(4, body)


def session(namespace: str, name: str) -> bytes:
    """kagent.api.v1alpha1.Session with only `agent` (field 15) set."""
    return _len(15, _str(1, namespace) + _str(2, name))


def unknown_field(number: int, payload: bytes) -> bytes:
    """A length-delimited field the reader does not know (a newer kagent's)."""
    return _len(number, payload)
