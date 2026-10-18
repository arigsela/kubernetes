# tests/beacon/test_provision_vault.py
"""beacon §T.40: scripts/provision-beacon-vault.sh, run against a stub `vault` CLI.

The stub keeps kv, policies and roles in a JSON file, so these tests run the real script
end to end: what it writes, that a re-run changes nothing, that it never prints a secret,
and that a read error is never mistaken for "absent".
"""
import base64
import json
import os
import re
import subprocess
import sys
import textwrap

import pytest
from conftest import REPO

SCRIPT = REPO / "scripts/provision-beacon-vault.sh"

STUB = textwrap.dedent(
    """\
    import json, os, sys
    db_path = os.environ["STUB_VAULT_DB"]
    db = json.load(open(db_path))
    args = sys.argv[1:]
    db["calls"].append(args)

    def save():
        json.dump(db, open(db_path, "w"))

    def opts(rest):
        flags = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in rest if a.startswith("-") and "=" in a}
        pos = [a for a in rest if not a.startswith("-")]
        return flags, pos

    if args[:2] == ["token", "lookup"]:
        save(); sys.exit(0 if not os.environ.get("STUB_VAULT_NOAUTH") else 2)
    if args[0] == "kv":
        flags, pos = opts(args[2:])
        key = pos[0]
        if args[1] == "get":
            if os.environ.get("STUB_VAULT_READ_ERROR"):
                print("Error making API request. Code: 403. permission denied", file=sys.stderr); save(); sys.exit(2)
            if key not in db["kv"]:
                print(f"No value found at {flags['-mount']}/data/{key}", file=sys.stderr); save(); sys.exit(2)
            print(json.dumps({"data": {"data": db["kv"][key]}})); save(); sys.exit(0)
        pairs = dict(p.split("=", 1) for p in pos[1:])
        if args[1] == "put":
            db["kv"][key] = pairs
        elif args[1] == "patch":
            assert key in db["kv"], "patch on a missing key"
            db["kv"][key].update(pairs)
        db["writes"].append([args[1], key, sorted(pairs)])
        save(); sys.exit(0)
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
    state.write_text(json.dumps({"kv": {}, "policies": {}, "roles": {}, "calls": [], "writes": []}))

    def run(*args, **env):
        e = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "VAULT_ADDR": "http://127.0.0.1:8200",
             "STUB_VAULT_DB": str(state), **env}
        p = subprocess.run(["bash", str(SCRIPT), *args], env=e, capture_output=True, text=True)
        return p, json.loads(state.read_text())

    return run


def secrets_of(db):
    return [db["kv"]["beacon"]["signing-key"], db["kv"]["beacon-db"]["owner-password"],
            db["kv"]["beacon-db"]["app-password"]]


def test_first_run_generates_every_value_with_the_shapes_beacon_reads(vault):
    p, db = vault()
    assert p.returncode == 0, p.stderr
    app, dbk = db["kv"]["beacon"], db["kv"]["beacon-db"]
    assert set(app) == {"signing-key"}
    # SIGNING_KEY_REF: a file holding base64 of exactly 32 bytes (AES-256-GCM, beacon §V.4).
    assert len(base64.b64decode(app["signing-key"], validate=True)) == 32
    assert dbk["owner-user"] == "beacon_owner" and dbk["app-user"] == "beacon_app" and dbk["db-name"] == "beacon"
    for pw in (dbk["owner-password"], dbk["app-password"]):
        # hex: safe in a DATABASE_URL and a SQL literal without escaping
        assert re.fullmatch(r"[0-9a-f]{48}", pw)
    assert dbk["owner-password"] != dbk["app-password"]


def test_policies_read_only_their_own_path_and_roles_bind_the_eso_identities(vault):
    p, db = vault()
    assert p.returncode == 0, p.stderr
    assert db["policies"] == {
        "beacon": 'path "k8s-secrets/data/beacon" { capabilities = ["read"] }\n',
        "beacon-db": 'path "k8s-secrets/data/beacon-db" { capabilities = ["read"] }\n',
    }
    assert db["roles"]["beacon"] == {"bound_service_account_names": "eso-beacon",
                                     "bound_service_account_namespaces": "beacon",
                                     "policies": "beacon", "ttl": "1h"}
    # The postgresql init Job sets the role passwords, so it reads beacon-db, never beacon.
    assert db["roles"]["beacon-db"] == {"bound_service_account_names": "eso-beacon-db",
                                        "bound_service_account_namespaces": "beacon,postgresql",
                                        "policies": "beacon-db", "ttl": "1h"}


def test_rerun_keeps_every_value(vault):
    _, first = vault()
    p, again = vault()
    assert p.returncode == 0, p.stderr
    assert again["kv"] == first["kv"]


def test_rotate_db_changes_only_the_two_passwords(vault):
    _, first = vault()
    p, rotated = vault("--rotate-db")
    assert p.returncode == 0, p.stderr
    assert rotated["kv"]["beacon"] == first["kv"]["beacon"]  # signing key untouched
    for k in ("owner-password", "app-password"):
        assert rotated["kv"]["beacon-db"][k] != first["kv"]["beacon-db"][k]
    for k in ("owner-user", "app-user", "db-name"):
        assert rotated["kv"]["beacon-db"][k] == first["kv"]["beacon-db"][k]


def test_smtp_url_is_stored_when_given_and_kept_after(vault):
    p, db = vault("--smtp-url", "smtp://u:p@mail.example.test:587")
    assert p.returncode == 0, p.stderr
    assert db["kv"]["beacon"]["smtp-url"] == "smtp://u:p@mail.example.test:587"
    _, db = vault()
    assert db["kv"]["beacon"]["smtp-url"] == "smtp://u:p@mail.example.test:587"
    p, _ = vault("--smtp-url", "http://nope")
    assert p.returncode == 2


def test_no_secret_is_ever_printed(vault):
    p1, db = vault()
    p2, _ = vault("--rotate-db")
    _, db2 = vault()
    out = p1.stdout + p1.stderr + p2.stdout + p2.stderr
    for s in secrets_of(db) + secrets_of(db2):
        assert s not in out
    assert "signing-key" in p1.stdout  # names are reported, values are not


def test_a_read_error_is_not_absent_and_writes_nothing(vault):
    p, db = vault(STUB_VAULT_READ_ERROR="1")
    assert p.returncode == 2 and "permission denied" in p.stderr
    assert db["kv"] == {} and db["writes"] == [] and db["policies"] == {} and db["roles"] == {}


def test_refuses_without_a_vault_login(vault):
    p, db = vault(STUB_VAULT_NOAUTH="1")
    assert p.returncode == 2 and "not logged in" in p.stderr
    assert db["writes"] == []


def test_refuses_without_vault_addr(vault):
    env = {k: v for k, v in os.environ.items() if k != "VAULT_ADDR"}
    p = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert p.returncode != 0 and "VAULT_ADDR" in p.stderr
