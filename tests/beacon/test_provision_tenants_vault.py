# tests/beacon/test_provision_tenants_vault.py
"""beacon §T.42 / beacon §V.52: scripts/provision-beacon-tenants-vault.sh, run against a stub `vault` CLI.

One tenant role for every tenant namespace and one read role for beacon-api, written once by
the owner. The stub records policies and roles, so these tests read exactly what the script
would write: which namespaces and service accounts each role admits, and which paths each
policy reaches.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest
from conftest import REPO

SCRIPT = REPO / "scripts/provision-beacon-tenants-vault.sh"
ACCESSOR = "auth_kubernetes_5d3c4a1b"
NS = f"{{{{identity.entity.aliases.{ACCESSOR}.metadata.service_account_namespace}}}}"

STUB = textwrap.dedent(
    """\
    import json, os, sys
    db_path = os.environ["STUB_VAULT_DB"]
    db = json.load(open(db_path))
    args = sys.argv[1:]
    db["calls"].append(args)

    def save():
        json.dump(db, open(db_path, "w"))

    if args[:2] == ["token", "lookup"]:
        save(); sys.exit(0 if not os.environ.get("STUB_VAULT_NOAUTH") else 2)
    if args == ["read", "-field=accessor", "sys/auth/kubernetes/"]:
        if os.environ.get("STUB_VAULT_NO_K8S_AUTH"):
            print("No value found at sys/auth/kubernetes/", file=sys.stderr); save(); sys.exit(2)
        print(os.environ["STUB_VAULT_ACCESSOR"], end=""); save(); sys.exit(0)
    if args[0] == "kv":
        key = [a for a in args[2:] if not a.startswith("-")][0]
        if args[1] == "get":
            if key not in db["kv"]:
                print(f"No value found at k8s-secrets/data/{key}", file=sys.stderr); save(); sys.exit(2)
            print(json.dumps({"data": {"data": db["kv"][key]}})); save(); sys.exit(0)
        if args[1] == "put":
            pairs = dict(a.split("=", 1) for a in args[2:] if not a.startswith("-") and "=" in a)
            db["kv"][key] = pairs; db["puts"].append(key); save(); sys.exit(0)
    if args[:2] == ["policy", "write"]:
        db["policies"][args[2]] = sys.stdin.read(); save(); sys.exit(0)
    if args[0] == "write" and args[1].startswith("auth/kubernetes/role/"):
        db["roles"][args[1].rsplit("/", 1)[1]] = dict(a.split("=", 1) for a in args[2:]); save(); sys.exit(0)
    print("stub vault: unexpected call", args, file=sys.stderr); save(); sys.exit(99)
    """
)


@pytest.fixture
def vault(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "stub_vault.py").write_text(STUB)
    exe = bindir / "vault"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{bindir / "stub_vault.py"}" "$@"\n')
    exe.chmod(0o755)
    state = tmp_path / "vault.json"
    state.write_text(json.dumps({"policies": {}, "roles": {}, "calls": [], "kv": {}, "puts": []}))

    def run(*args, **env):
        e = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "VAULT_ADDR": "http://127.0.0.1:8200",
             "STUB_VAULT_DB": str(state), "STUB_VAULT_ACCESSOR": ACCESSOR, **env}
        p = subprocess.run(["bash", str(SCRIPT), *args], env=e, capture_output=True, text=True)
        return p, json.loads(state.read_text())

    return run


def go_glob(pattern: str, s: str) -> bool:
    """github.com/ryanuber/go-glob, which Vault's k8s auth uses for the namespace list (beacon §R.29)."""
    if pattern == "*":
        return True
    parts = pattern.split("*")
    if len(parts) == 1:
        return s == pattern
    if not s.startswith(parts[0]) or not s.endswith(parts[-1]):
        return False
    rest = s[len(parts[0]):]
    for p in parts[1:-1]:
        i = rest.find(p)
        if i < 0:
            return False
        rest = rest[i + len(p):]
    return len(rest) >= len(parts[-1])


def test_v52_tenant_role_admits_only_eso_tenant_in_tenant_namespaces(vault):
    p, db = vault()
    assert p.returncode == 0, p.stderr
    role = db["roles"]["beacon-tenant"]
    assert role == {"bound_service_account_names": "eso-tenant", "bound_service_account_namespaces": "tenant-*",
                    "policies": "default,beacon-tenant-rw", "ttl": "1h"}
    patterns = role["bound_service_account_namespaces"].split(",")
    for ns in ("tenant-demo", "tenant-acme-2"):
        assert any(go_glob(pt, ns) for pt in patterns), ns
    for ns in ("beacon", "kube-system", "tenants", "tenant", "postgresql", "my-tenant-x"):
        assert not any(go_glob(pt, ns) for pt in patterns), ns


def test_v52_tenant_policy_reaches_only_the_namespaces_own_folder(vault):
    p, db = vault()
    assert p.returncode == 0, p.stderr
    assert db["policies"]["beacon-tenant-rw"] == (
        f'path "k8s-secrets/data/tenants/{NS}/*" {{ capabilities = ["create", "read", "update", "patch"] }}\n'
        f'path "k8s-secrets/metadata/tenants/{NS}/*" '
        f'{{ capabilities = ["create", "read", "update", "patch", "list", "delete"] }}\n'
    )


def test_b26_a_tenant_may_write_its_keys_metadata(vault):
    """ESO's Vault PushSecret PUTs custom metadata on every key it pushes (managed-by:
    external-secrets); without create/update on metadata/<key> every push is a 403 (beacon §B.26).
    Still only the tenant's own folder."""
    _, db = vault()
    meta = [line for line in db["policies"]["beacon-tenant-rw"].splitlines() if "/metadata/" in line][0]
    assert f"k8s-secrets/metadata/tenants/{NS}/*" in meta
    for cap in ("create", "update", "read", "delete"):
        assert f'"{cap}"' in meta, cap


def test_v52_api_role_lists_only_tenants_and_reads_only_api_keys(vault):
    p, db = vault()
    assert p.returncode == 0, p.stderr
    assert db["roles"]["beacon-tenants"] == {"bound_service_account_names": "eso-beacon-tenants",
                                             "bound_service_account_namespaces": "beacon",
                                             "policies": "beacon-tenants-read", "ttl": "1h"}
    # `+` is exactly one path segment (beacon §R.47): tenants/<ns>/api, never the DB passwords
    # under tenants/<ns>/db, and no list outside the tenants/ folder.
    assert db["policies"]["beacon-tenants-read"] == (
        'path "k8s-secrets/metadata/tenants/*" { capabilities = ["list"] }\n'
        'path "k8s-secrets/data/tenants/+/api" { capabilities = ["read"] }\n'
    )


def test_v52_writes_no_secret_values_and_reruns_identically(vault):
    _, first = vault()
    p, again = vault()
    assert p.returncode == 0, p.stderr
    assert again["policies"] == first["policies"] and again["roles"] == first["roles"]
    # The only key it ever writes is the folder marker, once, and it holds no secret.
    assert again["puts"] == ["tenants/.keep"]
    assert set(again["kv"]) == {"tenants/.keep"} and set(again["kv"]["tenants/.keep"]) == {"purpose"}


def test_b25_the_tenants_folder_exists_before_the_first_tenant(vault):
    """ESO's find fails on a folder Vault cannot list (beacon §B.25): the script leaves a marker
    in it, which beacon-tenant-dsns's name filter skips without reading."""
    import re

    import yaml

    p, db = vault()
    assert p.returncode == 0, p.stderr
    assert "tenants/.keep" in db["kv"]
    es = [d for d in yaml.safe_load_all((REPO / "base-apps/beacon/external-secrets.yaml").read_text())
          if d and d["metadata"]["name"] == "beacon-tenant-dsns"][0]
    name = re.compile(es["spec"]["dataFrom"][0]["find"]["name"]["regexp"])
    assert name.fullmatch("tenants/tenant-demo/api") and not name.fullmatch("tenants/.keep")


def test_refuses_without_a_vault_login(vault):
    p, db = vault(STUB_VAULT_NOAUTH="1")
    assert p.returncode == 2 and "not logged in" in p.stderr
    assert db["policies"] == {} and db["roles"] == {}


def test_refuses_without_the_kubernetes_auth_accessor(vault):
    p, db = vault(STUB_VAULT_NO_K8S_AUTH="1")
    assert p.returncode == 2 and "accessor" in p.stderr
    assert db["policies"] == {} and db["roles"] == {}


def test_refuses_without_vault_addr():
    env = {k: v for k, v in os.environ.items() if k != "VAULT_ADDR"}
    p = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert p.returncode != 0 and "VAULT_ADDR" in p.stderr
