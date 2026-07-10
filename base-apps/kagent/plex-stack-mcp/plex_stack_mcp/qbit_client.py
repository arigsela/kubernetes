from __future__ import annotations
import httpx


class QbitClient:
    def __init__(self, base_url: str, username: str, password: str, client: httpx.Client):
        self._base = base_url.rstrip("/")
        self._client = client
        self._username = username
        self._password = password

    def login(self) -> None:
        resp = self._client.post(
            f"{self._base}/api/v2/auth/login",
            data={"username": self._username, "password": self._password},
            headers={"Referer": self._base})
        resp.raise_for_status()
        if resp.text.strip() != "Ok.":
            raise RuntimeError("qBittorrent login failed (bad credentials or IP-banned)")

    def transfer_info(self) -> dict:
        resp = self._client.get(f"{self._base}/api/v2/transfer/info")
        resp.raise_for_status()
        d = resp.json()
        return {"connection_status": d.get("connection_status"),
                "dl_info_speed": d.get("dl_info_speed"),
                "up_info_speed": d.get("up_info_speed")}

    def torrents(self) -> list[dict]:
        resp = self._client.get(f"{self._base}/api/v2/torrents/info")
        resp.raise_for_status()
        return [{"hash": t.get("hash"), "name": t.get("name"), "state": t.get("state")}
                for t in resp.json()]

    def resume(self, hashes: list[str]) -> None:
        resp = self._client.post(f"{self._base}/api/v2/torrents/resume",
                                 data={"hashes": "|".join(hashes)})
        resp.raise_for_status()

    def recheck(self, hashes: list[str]) -> None:
        resp = self._client.post(f"{self._base}/api/v2/torrents/recheck",
                                 data={"hashes": "|".join(hashes)})
        resp.raise_for_status()
