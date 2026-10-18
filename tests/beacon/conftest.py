# tests/beacon/conftest.py
"""Shared helpers for the beacon suite: the GitOps side of arigsela/beacon (beacon §T.39+).

beacon's app code lives in the private arigsela/beacon repo; every manifest it needs
lives here. These tests pin the cross-repo contract that repo's SPEC.md states, so a
change on this side that breaks it fails here, not at login or rollout time.
"""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
HOST = "beacon.arigsela.com"
ORIGIN = f"https://{HOST}"


def load(rel: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((REPO / rel).read_text()) if d]


def one(rel: str, kind: str, name: str) -> dict:
    docs = [d for d in load(rel) if d.get("kind") == kind and d["metadata"]["name"] == name]
    assert len(docs) == 1, f"{rel}: expected one {kind}/{name}, found {len(docs)}"
    return docs[0]


def dex_config() -> dict:
    cm = one("base-apps/dex/configmap.yaml", "ConfigMap", "dex-config")
    return yaml.safe_load(cm["data"]["config.yaml"])
