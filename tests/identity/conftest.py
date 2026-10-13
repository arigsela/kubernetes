# tests/identity/conftest.py
from pathlib import Path
import yaml
import pytest

REPO = Path(__file__).resolve().parents[2]


def load(rel: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((REPO / rel).read_text()) if d]


def one(rel: str, kind: str, name: str) -> dict:
    docs = [d for d in load(rel) if d.get("kind") == kind and d["metadata"]["name"] == name]
    assert len(docs) == 1, f"{rel}: expected one {kind}/{name}, found {len(docs)}"
    return docs[0]


@pytest.fixture
def repo() -> Path:
    return REPO
