import httpx
from plex_stack_mcp.plex_client import PlexClient


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://plex")


def test_identity_reachable():
    def handler(req):
        assert req.headers["X-Plex-Token"] == "tok"
        assert req.url.path == "/identity"
        return httpx.Response(200, json={"MediaContainer": {
            "version": "1.40.0", "machineIdentifier": "abc"}})
    c = PlexClient("http://plex", "tok", _client(handler))
    out = c.identity()
    assert out == {"reachable": True, "version": "1.40.0",
                   "machine_identifier": "abc", "error": None}


def test_identity_unreachable():
    def handler(req):
        raise httpx.ConnectError("refused")
    c = PlexClient("http://plex", "tok", _client(handler))
    out = c.identity()
    assert out["reachable"] is False and "refused" in out["error"]


def test_sessions_parsed():
    def handler(req):
        assert req.url.path == "/status/sessions"
        return httpx.Response(200, json={"MediaContainer": {"Metadata": [
            {"title": "Drive", "User": {"title": "asela"},
             "TranscodeSession": {"key": "x"}},
            {"title": "Tombstone", "User": {"title": "guest"}}]}})
    c = PlexClient("http://plex", "tok", _client(handler))
    assert c.sessions() == [
        {"user": "asela", "title": "Drive", "transcode": True},
        {"user": "guest", "title": "Tombstone", "transcode": False}]


def test_libraries_parsed():
    def handler(req):
        assert req.url.path == "/library/sections"
        return httpx.Response(200, json={"MediaContainer": {"Directory": [
            {"key": "1", "title": "Movies", "type": "movie"},
            {"key": "2", "title": "TV Shows", "type": "show"}]}})
    c = PlexClient("http://plex", "tok", _client(handler))
    assert c.libraries() == [
        {"key": "1", "title": "Movies", "type": "movie"},
        {"key": "2", "title": "TV Shows", "type": "show"}]
