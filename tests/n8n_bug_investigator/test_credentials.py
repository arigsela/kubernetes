# tests/n8n_bug_investigator/test_credentials.py
"""Each POC token has its own Vault identity (§T.152, Task 8, Decision 4; templates/agent-identity invariant 1)."""
from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from conftest import REPO

N8N = REPO / "base-apps/n8n"
SCRIPT = REPO / "scripts/provision-n8n-bug-investigator-vault.sh"
CREDENTIALS = {
    # file: (ExternalSecret, Secret key, SecretStore, ESO ServiceAccount, Vault role/policy, Vault key, property)
    "bug-investigator-secrets.yaml": ("n8n-bug-investigator-gh", "token", "vault-n8n-bug-investigator",
                                      "eso-n8n-bug-investigator", "n8n-bug-investigator", "n8n-bug-investigator", "gh-token"),
    "github-read-mcp-secrets.yaml": ("n8n-github-read-mcp-token", "GITHUB_PERSONAL_ACCESS_TOKEN", "vault-n8n-github-read-mcp",
                                     "eso-n8n-github-read-mcp", "n8n-github-read-mcp", "n8n-github-read-mcp", "github-token"),
}


def kinds(file):
    out = {}
    for d in yaml.safe_load_all((N8N / file).read_text()):
        if d:
            assert d["kind"] not in out, f"one {d['kind']} per file"
            out[d["kind"]] = d
    return out


@pytest.mark.parametrize("file", sorted(CREDENTIALS))
def test_token_comes_from_its_own_store(file):
    es_name, secret_key, store, sa, role, key, prop = CREDENTIALS[file]
    k = kinds(file)
    assert set(k) == {"ServiceAccount", "SecretStore", "ExternalSecret"}
    assert all(d["metadata"]["namespace"] == "n8n" for d in k.values())
    assert k["ServiceAccount"]["metadata"]["name"] == sa
    vault = k["SecretStore"]["spec"]["provider"]["vault"]
    assert k["SecretStore"]["metadata"]["name"] == store
    assert (vault["path"], vault["version"], vault["auth"]["kubernetes"]["role"], vault["auth"]["kubernetes"]["serviceAccountRef"]["name"]) == \
        ("k8s-secrets", "v2", role, sa)
    es = k["ExternalSecret"]
    assert es["metadata"]["name"] == es_name and es["spec"]["target"]["name"] == es_name
    assert es["spec"]["secretStoreRef"] == {"name": store, "kind": "SecretStore"}
    assert es["spec"]["data"] == [{"secretKey": secret_key, "remoteRef": {"key": key, "property": prop}}]


def test_no_poc_credential_uses_the_shared_store():
    for file in CREDENTIALS:
        es = kinds(file)["ExternalSecret"]
        assert es["spec"]["secretStoreRef"]["name"] != "vault-backend"
        assert all(d["remoteRef"]["key"] != "n8n" for d in es["spec"]["data"])
    shared = (N8N / "external-secrets.yaml").read_text()
    assert "bug-investigator" not in shared and "github-read" not in shared


def test_provision_script_matches_manifests():
    text = SCRIPT.read_text()
    for _es, _sk, _store, sa, role, key, prop in CREDENTIALS.values():
        assert f'"{key}"' in text and f'"{role}"' in text and f'"{sa}"' in text and f'"{prop}"' in text
    assert 'ESO_NS="n8n"' in text
    assert set(re.findall(r"\$\{(N8N_[A-Z_]+_TOKEN)", text)) == {"N8N_BUG_INVESTIGATOR_GH_TOKEN", "N8N_GITHUB_READ_MCP_TOKEN"}
    # It writes only its own two paths, never the shared n8n key.
    assert set(re.findall(r'KV_PATH="([^"]+)"', text)) | set(re.findall(r'provision "([a-z0-9-]+)"', text)) >= {"n8n-bug-investigator", "n8n-github-read-mcp"}
    assert not re.search(r'kv (put|patch)\s+-mount=\S+\s+n8n\s', text)


def test_provision_script_parses_and_lints():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    shellcheck = subprocess.run(["which", "shellcheck"], capture_output=True)
    if shellcheck.returncode == 0:
        r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout
