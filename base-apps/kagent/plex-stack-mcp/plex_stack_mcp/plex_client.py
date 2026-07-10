from __future__ import annotations
import httpx


class PlexClient:
    def __init__(self, base_url: str, token: str, client: httpx.Client):
        self._base = base_url.rstrip("/")
        self._client = client
        self._headers = {"X-Plex-Token": token, "Accept": "application/json"}

    def _get(self, path: str) -> dict:
        resp = self._client.get(self._base + path, headers=self._headers)
        resp.raise_for_status()
        return resp.json()

    def identity(self) -> dict:
        try:
            mc = self._get("/identity").get("MediaContainer", {})
            return {"reachable": True, "version": mc.get("version"),
                    "machine_identifier": mc.get("machineIdentifier"), "error": None}
        except Exception as e:  # noqa: BLE001 - report, never raise to the tool layer
            return {"reachable": False, "version": None,
                    "machine_identifier": None, "error": str(e)}

    def libraries(self) -> list[dict]:
        mc = self._get("/library/sections").get("MediaContainer", {})
        return [{"key": d.get("key"), "title": d.get("title"), "type": d.get("type")}
                for d in mc.get("Directory", [])]

    def sessions(self) -> list[dict]:
        mc = self._get("/status/sessions").get("MediaContainer", {})
        out = []
        for m in mc.get("Metadata", []):
            out.append({
                "user": (m.get("User") or {}).get("title"),
                "title": m.get("title"),
                "transcode": "TranscodeSession" in m,
            })
        return out

    def scan(self, section_key: str) -> None:
        resp = self._client.get(
            f"{self._base}/library/sections/{section_key}/refresh",
            headers=self._headers)
        resp.raise_for_status()
