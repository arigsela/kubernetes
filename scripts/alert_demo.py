#!/usr/bin/env python3
"""alert-demo: the hk demo's workload (base-apps/alert-demo/, §T.139).

It sits idle until the hk demo page's Start button asks it to fill. A demo run then holds a fixed
amount of memory (FILL_MIB, 236 MiB by default) for HOLD_SECONDS (30 minutes) and lets it go. The
manifest sets FILL_MIB next to the container's memory limit, so while a run is on the container
is near its limit on purpose: Grafana's "Container near memory limit" fires, and the dispatcher
hands the alert to the implementer like any app's. The size is fixed, not a share of the limit,
so raising the limit really does fix the alert (design 2026-10-24, "Why a fixed size").

    GET  /healthz   {"ok": true}
    GET  /status    the state, the start and release times, held / target / limit / usage bytes
    POST /fill      202 and the status; 409 when already holding, without a limit, or at the limit
    POST /stop      200 and the status (releases early)

Standard library only: the Deployment runs this file from a ConfigMap with python:3.12-slim
(scripts/gen-alert-demo-configmap.py). Requests are not logged, so the last 100 log lines are
the state changes, which is what the implementer reads when it investigates.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = 4096
MI = 1 << 20


def read_cgroup(root: Path) -> tuple[int | None, int]:
    """The container's memory limit (None when unlimited) and its current usage, in bytes."""
    raw = (root / "memory.max").read_text().strip()
    usage = int((root / "memory.current").read_text().strip())
    return (None if raw == "max" else int(raw)), usage


def bytes_to_fill(target: int, usage: int) -> int:
    """How much more to hold so the container uses `target` bytes in all."""
    return max(0, target - usage)


def touched(n: int) -> bytearray:
    """n bytes with one byte written per page, so the kernel backs every page (a fresh
    bytearray is zero pages that cost nothing until written)."""
    buf = bytearray(n)
    buf[::PAGE] = b"\x01" * len(range(0, n, PAGE))
    return buf


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts is not None else None


class Filler:
    def __init__(self, *, cgroup: Callable[[], tuple[int | None, int]], clock: Callable[[], float],
                 timer_factory, hold: float, target: int, chunk: int = 4 * MI,
                 log: Callable[[dict], None], alloc: Callable[[int], object] = touched,
                 pod: str = "?"):
        self.cgroup, self.clock, self.timer_factory = cgroup, clock, timer_factory
        self.hold, self.target, self.chunk, self.log, self.alloc, self.pod = hold, target, chunk, log, alloc, pod
        self._lock = threading.RLock()
        self._held: list[object] = []
        self._held_bytes = 0
        self._started: float | None = None
        self._timer = None

    def fill(self) -> tuple[int, dict]:
        with self._lock:
            if self._started is not None:
                return 409, {"error": f"already holding since {_iso(self._started)}"}
            limit, usage = self.cgroup()
            if limit is None:
                return 409, {"error": "no memory limit"}
            if self.target >= limit:
                return 409, {"error": "the fill would reach the memory limit"}
            # Whole chunks, rounded up: the alert reads the working set, a few MiB under usage.
            for _ in range(-(-bytes_to_fill(self.target, usage) // self.chunk)):
                self._held.append(self.alloc(self.chunk))
                self._held_bytes += self.chunk
            self._started = self.clock()
            self._timer = self.timer_factory(self.hold, self.stop)
            self._timer.start()
            self.log({"event": "alert-demo", "state": "holding", "held_bytes": self._held_bytes,
                      "target_bytes": self.target, "limit_bytes": limit,
                      "release_at": _iso(self._started + self.hold)})
            return 202, self.status()

    def stop(self) -> dict:
        with self._lock:
            if self._started is not None:
                if self._timer is not None:
                    self._timer.cancel()
                self._held.clear()
                self._held_bytes, self._started, self._timer = 0, None, None
                self.log({"event": "alert-demo", "state": "idle", "held_bytes": 0})
            return self.status()

    def status(self) -> dict:
        with self._lock:
            limit, usage = self.cgroup()
            started = self._started
            return {"state": "holding" if started is not None else "idle", "started": _iso(started),
                    "release_at": _iso(started + self.hold) if started is not None else None,
                    "held_bytes": self._held_bytes, "target_bytes": self.target, "limit_bytes": limit,
                    "usage_bytes": usage, "percent": round(usage / limit * 100, 1) if limit else None,
                    "pod": self.pod}


def make_server(filler: Filler, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                return self._send(200, {"ok": True})
            if self.path == "/status":
                return self._send(200, filler.status())
            return self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(min(length, 4096))
            if self.path == "/fill":
                return self._send(*filler.fill())
            if self.path == "/stop":
                return self._send(200, filler.stop())
            return self._send(404, {"error": "not found"})

        def log_message(self, format, *args):  # noqa: A002 - requests are not logged (module doc)
            pass

    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def main() -> None:
    def log(obj: dict) -> None:
        print(json.dumps(obj, sort_keys=True), flush=True)

    root = Path(os.environ.get("CGROUP_DIR", "/sys/fs/cgroup"))
    filler = Filler(cgroup=lambda: read_cgroup(root), clock=time.time,
                    timer_factory=lambda s, fn: threading.Timer(s, fn),
                    hold=float(os.environ.get("HOLD_SECONDS", "1800")),
                    target=int(os.environ.get("FILL_MIB", "236")) * MI, log=log,
                    pod=socket.gethostname())
    port = int(os.environ.get("PORT", "8080"))
    log({"event": "alert-demo", "state": "idle", "target_bytes": filler.target, "hold_seconds": filler.hold,
         "port": port})
    make_server(filler, port).serve_forever()


if __name__ == "__main__":
    main()
