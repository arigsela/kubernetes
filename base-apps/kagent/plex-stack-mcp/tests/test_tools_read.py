import httpx
import pytest
from plex_stack_mcp.plex_client import PlexClient
from plex_stack_mcp.qbit_client import QbitClient
from plex_stack_mcp import tools


def plex_ok():
    def h(req):
        if req.url.path == "/identity":
            return httpx.Response(200, json={"MediaContainer": {"version": "1.40"}})
        if req.url.path == "/library/sections":
            return httpx.Response(200, json={"MediaContainer": {"Directory": [
                {"key": "1", "title": "Movies", "type": "movie"}]}})
        if req.url.path == "/status/sessions":
            return httpx.Response(200, json={"MediaContainer": {"Metadata": [
                {"title": "Drive", "User": {"title": "a"},
                 "TranscodeSession": {"k": 1}}]}})
        return httpx.Response(404)
    return PlexClient("http://p", "t", httpx.Client(transport=httpx.MockTransport(h)))


def plex_down():
    def h(req):
        raise httpx.ConnectError("refused")
    return PlexClient("http://p", "t", httpx.Client(transport=httpx.MockTransport(h)))


def test_resolve_plex_rejects_unknown():
    reg = tools.Registry(family=plex_ok(), private=plex_ok(), qbit=None)
    with pytest.raises(ValueError):
        tools.resolve_plex(reg, "public")


def test_plex_status_up():
    reg = tools.Registry(family=plex_ok(), private=plex_ok(), qbit=None)
    out = tools.tool_plex_status(reg, "family")
    assert out["reachable"] is True
    assert out["version"] == "1.40"
    assert out["session_count"] == 1
    assert out["transcode_count"] == 1
    assert out["libraries"] == [{"key": "1", "title": "Movies", "type": "movie"}]


def test_plex_status_down_has_note():
    reg = tools.Registry(family=plex_down(), private=plex_down(), qbit=None)
    out = tools.tool_plex_status(reg, "private")
    assert out["reachable"] is False
    assert out["libraries"] == [] and out["session_count"] == 0
    assert out["note"] == tools.DOWN_NOTE


def test_qbit_status_up_flags_stalled():
    def h(req):
        if req.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.")
        if req.url.path == "/api/v2/transfer/info":
            return httpx.Response(200, json={"connection_status": "connected",
                                             "dl_info_speed": 1, "up_info_speed": 2})
        if req.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[
                {"hash": "aaa", "name": "x", "state": "stalledDL"},
                {"hash": "bbb", "name": "y", "state": "uploading"}])
        return httpx.Response(404)
    q = QbitClient("http://q", "b", "p", httpx.Client(transport=httpx.MockTransport(h)))
    reg = tools.Registry(family=None, private=None, qbit=q)
    out = tools.tool_qbit_status(reg)
    assert out["reachable"] is True
    assert out["connection_status"] == "connected"
    assert out["stalled"] == ["aaa"]


def test_qbit_status_down_has_note():
    def h(req):
        raise httpx.ConnectError("refused")
    q = QbitClient("http://q", "b", "p", httpx.Client(transport=httpx.MockTransport(h)))
    reg = tools.Registry(family=None, private=None, qbit=q)
    out = tools.tool_qbit_status(reg)
    assert out["reachable"] is False and out["note"] == tools.DOWN_NOTE
