"""scripts/provision-model-gateway-vault.sh (spec §3.3), run for real against a stub `vault`.

What matters: every group in budgets.yaml gets a key and an existing key is never replaced unless
asked; no value ever reaches a command line or the terminal (values go in as key=@file); and
the names (policies, roles, service accounts, namespaces) match the manifests that use them."""
import json
import os
import re
import subprocess
import sys
import textwrap

import pytest
import yaml

import model_budgets
from conftest import REPO

SCRIPT = REPO / "scripts/provision-model-gateway-vault.sh"
HEX64 = re.compile(r"\b[0-9a-f]{48,64}\b")
CONTRACT = [  # (vault role/policy, service account, namespace)
    ("model-gateway", "eso-model-gateway", "model-gateway"),
    ("model-gateway-keys", "eso-model-gateway-keys", "kagent"),
    ("model-gateway-db", "eso-model-gateway-db", "postgresql"),
]

VAULT_STUB = textwrap.dedent('''\
    #!/usr/bin/env python3
    """A Vault CLI stand-in: state in $VAULT_STATE (json), every argv appended to $STUB_LOG."""
    import json, os, sys
    state_path, log = os.environ["VAULT_STATE"], os.environ["STUB_LOG"]
    state = json.load(open(state_path)) if os.path.exists(state_path) else {"kv": {}, "policies": {}, "roles": {}}
    args = sys.argv[1:]
    open(log, "a").write(json.dumps(args) + "\\n")
    def save():
        json.dump(state, open(state_path, "w"))
    if args[:2] == ["token", "lookup"]:
        sys.exit(0)
    if args[0] == "kv":
        op = args[1]
        rest = [a for a in args[2:] if not a.startswith("-")]
        name = rest[0]
        if op == "get":
            if name not in state["kv"]:
                sys.exit(2)
            print(json.dumps({"data": {"data": state["kv"][name]}}))
            sys.exit(0)
        if op in ("put", "patch"):
            if op == "patch" and name not in state["kv"]:
                sys.exit(2)
            data = {} if op == "put" else dict(state["kv"][name])
            for kv in rest[1:]:
                k, v = kv.split("=", 1)
                data[k] = open(v[1:]).read() if v.startswith("@") else v
            state["kv"][name] = data
            save()
            sys.exit(0)
    if args[:2] == ["policy", "write"]:
        state["policies"][args[2]] = sys.stdin.read()
        save()
        sys.exit(0)
    if args[0] == "write" and args[1].startswith("auth/kubernetes/role/"):
        state["roles"][args[1].rsplit("/", 1)[1]] = dict(a.split("=", 1) for a in args[2:])
        save()
        sys.exit(0)
    print("stub: unhandled " + " ".join(args), file=sys.stderr)
    sys.exit(1)
''')


@pytest.fixture
def stub(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    vault = bin_dir / "vault"
    vault.write_text(VAULT_STUB)
    vault.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "VAULT_ADDR": "http://127.0.0.1:8200",
           "VAULT_STATE": str(tmp_path / "state.json"), "STUB_LOG": str(tmp_path / "argv.log")}

    def run(*args):
        r = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)
        state = json.loads((tmp_path / "state.json").read_text()) if (tmp_path / "state.json").exists() else {}
        argv = (tmp_path / "argv.log").read_text() if (tmp_path / "argv.log").exists() else ""
        return r, state, argv

    return run


def test_script_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_first_run_writes_a_key_per_group_the_db_password_policies_and_roles(stub):
    r, state, argv = stub()
    assert r.returncode == 0, r.stderr
    groups = list(model_budgets.load()["groups"])
    keys = state["kv"]["model-gateway-keys"]
    assert sorted(keys) == sorted(groups)
    assert all(re.fullmatch(r"[0-9a-f]{64}", v) for v in keys.values()), "32 random bytes as hex, no newline"
    assert len(set(keys.values())) == len(groups)
    assert re.fullmatch(r"[0-9a-f]{48}", state["kv"]["model-gateway-db"]["password"])
    for role, sa, ns in CONTRACT:
        assert state["roles"][role]["bound_service_account_names"] == sa
        assert state["roles"][role]["bound_service_account_namespaces"] == ns
        assert state["roles"][role]["policies"] == role
    assert 'k8s-secrets/data/kagent-anthropic' in state["policies"]["model-gateway"]
    assert 'k8s-secrets/data/model-gateway-keys' in state["policies"]["model-gateway"]
    assert 'k8s-secrets/data/model-gateway-db' in state["policies"]["model-gateway"]
    assert state["policies"]["model-gateway-keys"].count("path ") == 1
    assert state["policies"]["model-gateway-db"].count("path ") == 1


def test_values_never_reach_argv_or_the_terminal(stub):
    r, state, argv = stub()
    values = list(state["kv"]["model-gateway-keys"].values()) + [state["kv"]["model-gateway-db"]["password"]]
    for v in values:
        assert v not in argv and v not in r.stdout and v not in r.stderr
    assert not HEX64.search(r.stdout + r.stderr)


def test_rerun_keeps_keys_and_rotate_replaces_exactly_one(stub):
    _, first, _ = stub()
    r, again, _ = stub()
    assert r.returncode == 0 and again["kv"] == first["kv"]
    r, rotated, _ = stub("--rotate", "k8s-reader")
    assert r.returncode == 0, r.stderr
    before, after = first["kv"]["model-gateway-keys"], rotated["kv"]["model-gateway-keys"]
    assert after["k8s-reader"] != before["k8s-reader"]
    assert {g: v for g, v in after.items() if g != "k8s-reader"} == {g: v for g, v in before.items() if g != "k8s-reader"}
    assert rotated["kv"]["model-gateway-db"] == first["kv"]["model-gateway-db"]


def test_rotate_refuses_an_unknown_group_and_dry_run_writes_nothing(stub):
    r, state, _ = stub("--rotate", "nope")
    assert r.returncode == 2 and "nope" in r.stderr and not state.get("kv")
    r, state, _ = stub("--dry-run")
    assert r.returncode == 0 and not state.get("kv") and "would write" in r.stdout


def test_groups_come_from_the_table():
    text = SCRIPT.read_text()
    assert "model_budgets.py\" --groups" in text or "model_budgets.py --groups" in text
    for g in model_budgets.load()["groups"]:
        if g not in ("model-gateway",):
            assert f" {g} " not in text and f"={g}" not in text, f"group {g} hard-coded"
    out = subprocess.run([sys.executable, str(REPO / "scripts/model_budgets.py"), "--groups"],
                         capture_output=True, text=True, check=True).stdout.split()
    assert out == list(model_budgets.load()["groups"])


def test_never_prints_values_by_construction():
    text = SCRIPT.read_text()
    assert "set -x" not in text
    assert "openssl rand -hex 32" in text
    assert "=@" in text


def _manifest_docs():
    docs = []
    for path in [*(REPO / "base-apps/model-gateway").glob("*.yaml"), REPO / "base-apps/kagent/model-gateway-keys.yaml",
                 *(REPO / "base-apps/postgresql").glob("*model-gateway*.yaml")]:
        if path.name == "budgets.yaml" or not path.exists():
            continue
        docs += [d for d in yaml.safe_load_all(path.read_text()) if d]
    return docs


@pytest.mark.xfail(strict=True, reason="the manifests arrive in Task 7")
def test_names_match_the_manifests():
    docs = _manifest_docs()
    stores = {(d["metadata"]["namespace"], d["spec"]["provider"]["vault"]["auth"]["kubernetes"]["role"],
               d["spec"]["provider"]["vault"]["auth"]["kubernetes"]["serviceAccountRef"]["name"])
              for d in docs if d.get("kind") == "SecretStore"}
    assert {(ns, role, sa) for role, sa, ns in CONTRACT} == stores
