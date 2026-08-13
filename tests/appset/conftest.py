"""Fixtures for the managed-apps ApplicationSet config tests.

`expand` is a Python restatement of base-apps/managed-apps.yaml's template
plus its templatePatch. It is deliberately small: the ApplicationSet template
is reviewed once by a human, and this mirrors it so the CONFIGS can be checked
against the golden specs without a cluster. If the template changes, this must
change with it.
"""
import pathlib

import pytest
import yaml

REPO_URL = "https://github.com/arigsela/kubernetes"
TARGET_REVISION = "main"
DEST_SERVER = "https://kubernetes.default.svc"

CONFIG_DIR = "appsets/managed-apps"
GOLDEN_DIR = "tests/appset/golden"


@pytest.fixture(scope="session")
def repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def configs(repo_root) -> dict:
    out = {}
    for path in sorted((repo_root / CONFIG_DIR).glob("*.yaml")):
        out[path.stem] = yaml.safe_load(path.read_text())
    return out


@pytest.fixture(scope="session")
def goldens(repo_root) -> dict:
    out = {}
    for path in sorted((repo_root / GOLDEN_DIR).glob("*.yaml")):
        out[path.stem] = yaml.safe_load(path.read_text())
    return out


def expand(cfg: dict) -> dict:
    """Render the Application spec the ApplicationSet would produce."""
    spec = {
        "project": "default",
        "source": {
            "repoURL": REPO_URL,
            "targetRevision": TARGET_REVISION,
            "path": cfg["sourcePath"],
        },
        "destination": {
            "server": DEST_SERVER,
            "namespace": cfg["namespace"],
        },
        "syncPolicy": {
            "automated": {"prune": True, "selfHeal": True},
        },
    }
    # templatePatch renders `syncOptions:` with no items when the list is
    # empty, which is YAML null; a merge patch treats null as "delete the
    # key", and the template never set it, so the field is simply absent.
    if cfg["syncOptions"]:
        spec["syncPolicy"]["syncOptions"] = list(cfg["syncOptions"])
    return spec
