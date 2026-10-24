# tests/alert_demo/test_alert_demo.py
"""scripts/alert_demo.py: the hk demo's workload holds a fixed amount of memory on request (§T.139)."""
from pathlib import Path
import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("alert_demo", REPO / "scripts" / "alert_demo.py")
ad = importlib.util.module_from_spec(_spec)
sys.modules["alert_demo"] = ad
_spec.loader.exec_module(ad)

MI = 1 << 20


class FakeTimer:
    def __init__(self, seconds, fn):
        self.seconds, self.fn, self.started, self.cancelled = seconds, fn, False, False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.fn()


class Rig:
    """A Filler on a fake cgroup, clock, timer and allocator (no real memory in the tests)."""

    def __init__(self, limit=256 * MI, usage=20 * MI, target=236 * MI, chunk=4 * MI):
        self.limit, self.usage = limit, usage
        self.timers, self.logged, self.allocated = [], [], []
        self.filler = ad.Filler(cgroup=lambda: (self.limit, self.usage), clock=lambda: 1792850000.0,
                                timer_factory=self._timer, hold=1800, target=target, chunk=chunk,
                                log=self.logged.append, alloc=self._alloc, pod="alert-demo-abc")

    def _timer(self, seconds, fn):
        t = FakeTimer(seconds, fn)
        self.timers.append(t)
        return t

    def _alloc(self, n):
        self.allocated.append(n)
        return object()


def test_bytes_to_fill():
    assert ad.bytes_to_fill(236 * MI, 20 * MI) == 216 * MI
    assert ad.bytes_to_fill(100, 200) == 0


def test_read_cgroup_max_is_none(tmp_path):
    (tmp_path / "memory.max").write_text("max\n")
    (tmp_path / "memory.current").write_text("1234\n")
    assert ad.read_cgroup(tmp_path) == (None, 1234)
    (tmp_path / "memory.max").write_text("268435456\n")
    assert ad.read_cgroup(tmp_path) == (268435456, 1234)


def test_fill_holds_then_releases():
    rig = Rig()
    code, status = rig.filler.fill()
    assert code == 202 and status["state"] == "holding"
    assert status["held_bytes"] == 216 * MI == sum(rig.allocated)
    assert status["release_at"] and status["started"] and status["pod"] == "alert-demo-abc"
    assert status["percent"] == round(20 * MI / (256 * MI) * 100, 1)
    assert rig.timers[0].seconds == 1800 and rig.timers[0].started
    rig.timers[0].fire()
    after = rig.filler.status()
    assert after["state"] == "idle" and after["held_bytes"] == 0 and after["started"] is None
    assert [(x["event"], x["state"]) for x in rig.logged] == [("alert-demo", "holding"), ("alert-demo", "idle")]


def test_held_bytes_round_up_to_whole_chunks():
    # Up, never down: Grafana's working set runs a few MiB under memory.current, and a fill that
    # stopped short could sit just under the alert's 90%.
    rig = Rig(usage=21 * MI)
    assert rig.filler.fill()[1]["held_bytes"] == 216 * MI   # 215 MiB to go, in 4 MiB chunks


def test_second_fill_is_409():
    rig = Rig()
    rig.filler.fill()
    code, body = rig.filler.fill()
    assert code == 409 and body["error"].startswith("already holding since ")
    assert len(rig.timers) == 1


def test_no_limit_is_409():
    rig = Rig(limit=None)
    assert rig.filler.fill() == (409, {"error": "no memory limit"})
    assert rig.allocated == []


def test_fill_refuses_at_or_over_the_limit():
    rig = Rig(limit=256 * MI, target=256 * MI)
    assert rig.filler.fill() == (409, {"error": "the fill would reach the memory limit"})
    assert rig.allocated == []


def test_fill_does_not_track_the_limit():
    rig = Rig(limit=384 * MI)   # a merged demo PR raised the limit by half
    assert rig.filler.fill()[1]["held_bytes"] == 216 * MI   # still 236 MiB in all, now 61% of the limit


def test_stop_releases_early_and_cancels_the_timer():
    rig = Rig()
    rig.filler.fill()
    assert rig.filler.stop()["state"] == "idle"
    assert rig.timers[0].cancelled
    assert rig.filler.stop()["state"] == "idle"   # stopping twice is harmless
    assert [x["state"] for x in rig.logged] == ["holding", "idle"]


def test_touch_writes_every_page():
    buf = ad.touched(3 * 4096 + 10)
    assert len(buf) == 3 * 4096 + 10
    assert all(buf[i] == 1 for i in range(0, len(buf), 4096))


def _call(port, method, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_http_routes():
    rig = Rig()
    server = ad.make_server(rig.filler, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        assert _call(port, "GET", "/healthz") == (200, {"ok": True})
        assert _call(port, "GET", "/status")[1]["state"] == "idle"
        code, body = _call(port, "POST", "/fill")
        assert code == 202 and body["state"] == "holding"
        assert _call(port, "POST", "/fill")[0] == 409
        assert _call(port, "POST", "/stop")[1]["state"] == "idle"
        assert _call(port, "GET", "/nope")[0] == 404
        assert _call(port, "POST", "/status")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
