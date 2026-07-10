import httpx
import pytest
from plex_stack_mcp.qbit_client import QbitClient


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://qbit")


def test_login_ok():
    def handler(req):
        assert req.url.path == "/api/v2/auth/login"
        assert b"username=bot" in req.content
        return httpx.Response(200, text="Ok.")
    QbitClient("http://qbit", "bot", "pw", _client(handler)).login()


def test_login_failure_raises():
    def handler(req):
        return httpx.Response(200, text="Fails.")
    with pytest.raises(RuntimeError):
        QbitClient("http://qbit", "bot", "pw", _client(handler)).login()


def test_transfer_info():
    def handler(req):
        assert req.url.path == "/api/v2/transfer/info"
        return httpx.Response(200, json={"connection_status": "connected",
                                         "dl_info_speed": 1000, "up_info_speed": 20})
    out = QbitClient("http://qbit", "b", "p", _client(handler)).transfer_info()
    assert out == {"connection_status": "connected",
                   "dl_info_speed": 1000, "up_info_speed": 20}


def test_torrents():
    def handler(req):
        assert req.url.path == "/api/v2/torrents/info"
        return httpx.Response(200, json=[
            {"hash": "aaa", "name": "ubuntu.iso", "state": "stalledDL"},
            {"hash": "bbb", "name": "debian.iso", "state": "uploading"}])
    out = QbitClient("http://qbit", "b", "p", _client(handler)).torrents()
    assert out == [
        {"hash": "aaa", "name": "ubuntu.iso", "state": "stalledDL"},
        {"hash": "bbb", "name": "debian.iso", "state": "uploading"}]
