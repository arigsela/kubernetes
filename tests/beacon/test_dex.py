# tests/beacon/test_dex.py
"""beacon §T.39: Dex client `beacon` (beacon §V.35, §V.25)."""
import hashlib
import re

import yaml
from conftest import ORIGIN, REPO, dex_config, load, one


def test_beacon_client_is_public_with_exactly_the_spa_callback():
    client = next(c for c in dex_config()["staticClients"] if c["id"] == "beacon")
    # A single-page app cannot keep a secret: public, and PKCE does the binding.
    assert client.get("public") is True
    assert "secret" not in client and "secretEnv" not in client
    assert client["redirectURIs"] == [f"{ORIGIN}/callback"]


def test_no_client_secret_for_beacon_is_synced_into_dex():
    es = one("base-apps/dex/external-secret.yaml", "ExternalSecret", "dex-secrets")
    assert not [d for d in es["spec"]["data"] if "beacon" in d["secretKey"].lower()
                or "beacon" in d["remoteRef"]["property"]]


def test_cors_admits_the_beacon_origin_and_never_a_wildcard():
    origins = dex_config()["web"].get("allowedOrigins", [])
    assert ORIGIN in origins
    # A wildcard would let any site run a code exchange against every public client.
    assert "*" not in origins and all(o.startswith("https://") for o in origins)


def test_dex_rolls_when_its_config_changes():
    # Dex does not watch its config file (base-apps/dex/runbook.md): without this
    # bump a new client syncs cleanly and Dex keeps serving the old config.
    cm = one("base-apps/dex/configmap.yaml", "ConfigMap", "dex-config")
    want = hashlib.sha256(cm["data"]["config.yaml"].encode()).hexdigest()[:16]
    dep = one("base-apps/dex/deployment.yaml", "Deployment", "dex")
    assert dep["spec"]["template"]["metadata"]["annotations"]["checksum/config"] == want


def test_beacon_comments_cite_beacon_sections_not_bare_ones():
    # beacon §V.27: this repo has its own SPEC.md numbering; a bare §T.n next to
    # beacon's name would point at the wrong task.
    bare = re.compile(r"(?<!beacon )§[TV]\.\d+")
    text = (REPO / "base-apps/dex/configmap.yaml").read_text()
    block = text[text.index("# beacon, added"):text.index("- id: beacon")]
    assert "beacon §T.39" in block and not bare.search(block)
