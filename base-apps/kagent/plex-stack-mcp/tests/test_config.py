import pytest
from plex_stack_mcp.config import Settings


def test_from_env_uses_defaults_and_secrets(monkeypatch):
    monkeypatch.setenv("PLEX_FAMILY_TOKEN", "ftok")
    monkeypatch.setenv("PLEX_PRIVATE_TOKEN", "ptok")
    monkeypatch.setenv("QBIT_USERNAME", "botuser")
    monkeypatch.setenv("QBIT_PASSWORD", "botpass")
    s = Settings.from_env()
    assert s.plex_family_url == "http://10.0.1.200:32401"
    assert s.plex_private_url == "http://10.0.1.200:32500"
    assert s.qbit_url == "http://10.0.1.200:8080"
    assert s.plex_family_token == "ftok"
    assert s.qbit_username == "botuser"


def test_from_env_missing_secret_raises(monkeypatch):
    monkeypatch.delenv("PLEX_FAMILY_TOKEN", raising=False)
    monkeypatch.setenv("PLEX_PRIVATE_TOKEN", "ptok")
    monkeypatch.setenv("QBIT_USERNAME", "botuser")
    monkeypatch.setenv("QBIT_PASSWORD", "botpass")
    with pytest.raises(RuntimeError):
        Settings.from_env()
