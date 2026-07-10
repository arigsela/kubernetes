from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    plex_family_url: str
    plex_private_url: str
    qbit_url: str
    plex_family_token: str
    plex_private_token: str
    qbit_username: str
    qbit_password: str

    @staticmethod
    def from_env() -> "Settings":
        def required(name: str) -> str:
            val = os.environ.get(name)
            if not val:
                raise RuntimeError(f"missing required env var: {name}")
            return val

        return Settings(
            plex_family_url=os.environ.get("PLEX_FAMILY_URL", "http://10.0.1.200:32401"),
            plex_private_url=os.environ.get("PLEX_PRIVATE_URL", "http://10.0.1.200:32500"),
            qbit_url=os.environ.get("QBIT_URL", "http://10.0.1.200:8080"),
            plex_family_token=required("PLEX_FAMILY_TOKEN"),
            plex_private_token=required("PLEX_PRIVATE_TOKEN"),
            qbit_username=required("QBIT_USERNAME"),
            qbit_password=required("QBIT_PASSWORD"),
        )
