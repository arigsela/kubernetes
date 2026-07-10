import httpx
import pytest
from plex_stack_mcp.plex_client import PlexClient
from plex_stack_mcp.qbit_client import QbitClient
from plex_stack_mcp import tools


def qbit_recorder(records):
    def h(req):
        records.append((req.url.path, req.content.decode()))
        if req.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.")
        if req.url.path == "/api/v2/transfer/info":
            return httpx.Response(200, json={"connection_status": "connected",
                                             "dl_info_speed": 0, "up_info_speed": 0})
        if req.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[
                {"hash": "aaa", "name": "x", "state": "stalledDL"}])
        return httpx.Response(200, text="Ok.")
    return QbitClient("http://q", "b", "p", httpx.Client(transport=httpx.MockTransport(h)))


def test_resume_explicit_hashes():
    recs = []
    reg = tools.Registry(family=None, private=None, qbit=qbit_recorder(recs))
    out = tools.tool_qbit_resume(reg, hashes=["h1", "h2"])
    assert out == {"resumed": ["h1", "h2"], "count": 2}
    assert any(p == "/api/v2/torrents/resume" and "h1%7Ch2" in c for p, c in recs)


def test_resume_all_stalled():
    reg = tools.Registry(family=None, private=None, qbit=qbit_recorder([]))
    out = tools.tool_qbit_resume(reg, hashes=None, all_stalled=True)
    assert out == {"resumed": ["aaa"], "count": 1}


def test_resume_requires_input():
    reg = tools.Registry(family=None, private=None, qbit=qbit_recorder([]))
    with pytest.raises(ValueError):
        tools.tool_qbit_resume(reg, hashes=None, all_stalled=False)


def test_recheck_empty_raises():
    reg = tools.Registry(family=None, private=None, qbit=qbit_recorder([]))
    with pytest.raises(ValueError):
        tools.tool_qbit_recheck(reg, hashes=[])


def test_plex_scan():
    recs = []
    def h(req):
        recs.append(req.url.path)
        if req.url.path == "/identity":
            return httpx.Response(200, json={"MediaContainer": {"version": "1.40"}})
        return httpx.Response(200, text="")
    plex = PlexClient("http://p", "t", httpx.Client(transport=httpx.MockTransport(h)))
    reg = tools.Registry(family=plex, private=plex, qbit=None)
    out = tools.tool_plex_scan_library(reg, "family", "1")
    assert out == {"instance": "family", "section_key": "1", "scan_triggered": True, "reachable": True}
    assert "/library/sections/1/refresh" in recs


def test_plex_scan_down_returns_note_no_raise():
    def h(req):
        raise httpx.ConnectError("refused")
    plex = PlexClient("http://p", "t", httpx.Client(transport=httpx.MockTransport(h)))
    reg = tools.Registry(family=plex, private=plex, qbit=None)
    out = tools.tool_plex_scan_library(reg, "family", "1")
    assert out["reachable"] is False
    assert out["note"] == tools.DOWN_NOTE
    assert out["instance"] == "family"


def _qbit_requires_login(records, calls):
    """qBittorrent-like handler: resume/recheck 403 unless login happened first."""
    def h(req):
        calls.append(req.url.path)
        records.append((req.url.path, req.content.decode()))
        if req.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.")
        if req.url.path in ("/api/v2/torrents/resume", "/api/v2/torrents/recheck"):
            if "/api/v2/auth/login" not in calls:
                return httpx.Response(403, text="Forbidden")
            return httpx.Response(200, text="Ok.")
        if req.url.path == "/api/v2/transfer/info":
            return httpx.Response(200, json={"connection_status": "connected",
                                             "dl_info_speed": 0, "up_info_speed": 0})
        if req.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[
                {"hash": "aaa", "name": "x", "state": "stalledDL"}])
        return httpx.Response(200, text="Ok.")
    return QbitClient("http://q", "b", "p", httpx.Client(transport=httpx.MockTransport(h)))


def test_resume_explicit_hashes_authenticates_first():
    recs = []
    calls = []
    reg = tools.Registry(family=None, private=None, qbit=_qbit_requires_login(recs, calls))
    out = tools.tool_qbit_resume(reg, hashes=["h1", "h2"])
    assert out == {"resumed": ["h1", "h2"], "count": 2}
    assert "/api/v2/auth/login" in calls
    assert calls.index("/api/v2/auth/login") < calls.index("/api/v2/torrents/resume")


def test_recheck_authenticates_first():
    recs = []
    calls = []
    reg = tools.Registry(family=None, private=None, qbit=_qbit_requires_login(recs, calls))
    out = tools.tool_qbit_recheck(reg, hashes=["h1"])
    assert out == {"rechecked": ["h1"], "count": 1}
    assert "/api/v2/auth/login" in calls
    assert calls.index("/api/v2/auth/login") < calls.index("/api/v2/torrents/recheck")


def test_resume_all_stalled_when_qbit_down_reports_down_not_fake_success():
    def h(req):
        raise httpx.ConnectError("refused")
    q = QbitClient("http://q", "b", "p", httpx.Client(transport=httpx.MockTransport(h)))
    reg = tools.Registry(family=None, private=None, qbit=q)
    out = tools.tool_qbit_resume(reg, hashes=None, all_stalled=True)
    assert out["reachable"] is False
    assert out["note"] == tools.DOWN_NOTE
    assert out != {"resumed": [], "count": 0}
