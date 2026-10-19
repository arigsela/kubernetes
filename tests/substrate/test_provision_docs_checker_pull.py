# tests/substrate/test_provision_docs_checker_pull.py
"""scripts/provision-docs-checker-pull.sh (SPEC §T.129), run end to end against stub `vault`, `aws`
and `ansible-playbook` CLIs that keep their state in JSON files.

What it must do: create the pull user's access key only when needed, keep Vault the source of truth,
hand the key to Ansible through the environment (never argv, never output), clean up a key it could not
store, and rotate in two steps so a key is only deleted after its successor works on every worker.
"""
import hashlib
import json
import os
import subprocess
import sys
import textwrap

import pytest
from conftest import SCRIPT

VAULT_STUB = textwrap.dedent(
    """\
    import json, os, sys
    path = os.environ["STUB_VAULT_DB"]
    db = json.load(open(path))
    args = sys.argv[1:]
    db["calls"].append(args)
    def save(): json.dump(db, open(path, "w"))
    if args[:2] == ["token", "lookup"]:
        save(); sys.exit(2 if os.environ.get("STUB_VAULT_NOAUTH") else 0)
    if args[0] == "kv":
        flags = dict(a.split("=", 1) for a in args[2:] if a.startswith("-") and "=" in a)
        pos = [a for a in args[2:] if not a.startswith("-") or a == "-"]
        key = pos[0]
        if args[1] == "get":
            if os.environ.get("STUB_VAULT_READ_ERROR"):
                print("Error making API request. Code: 403. permission denied", file=sys.stderr); save(); sys.exit(2)
            if key not in db["kv"]:
                print(f"No value found at {flags['-mount']}/data/{key}", file=sys.stderr); save(); sys.exit(2)
            print(json.dumps({"data": {"data": db["kv"][key]}})); save(); sys.exit(0)
        if args[1] == "put":
            if os.environ.get("STUB_VAULT_WRITE_ERROR"):
                sys.stdin.read(); print("Error writing data: 403", file=sys.stderr); save(); sys.exit(2)
            assert pos[1:] == ["-"], f"kv put must read its data from stdin, got {pos[1:]}"
            db["kv"][key] = json.load(sys.stdin)
            db["writes"].append(key); save(); sys.exit(0)
    print("stub vault: unexpected call", args, file=sys.stderr); save(); sys.exit(99)
    """
)

AWS_STUB = textwrap.dedent(
    """\
    import json, os, random, string, sys
    path = os.environ["STUB_AWS_DB"]
    db = json.load(open(path))
    args = sys.argv[1:]
    db["calls"].append(args)
    def save(): json.dump(db, open(path, "w"))
    def opt(name): return args[args.index(name) + 1]
    if args[:2] == ["iam", "get-user"]:
        if os.environ.get("STUB_AWS_NO_USER"):
            print("An error occurred (NoSuchEntity) when calling the GetUser operation", file=sys.stderr); save(); sys.exit(254)
        assert opt("--user-name") == "docs-checker-pull"
        print(json.dumps({"User": {"UserName": "docs-checker-pull", "Path": "/system/"}})); save(); sys.exit(0)
    if args[:2] == ["iam", "list-access-keys"]:
        print(json.dumps({"AccessKeyMetadata": [{"UserName": "docs-checker-pull", "AccessKeyId": k["id"],
                                                 "Status": k["status"]} for k in db["keys"]]}))
        save(); sys.exit(0)
    if args[:2] == ["iam", "create-access-key"]:
        if len(db["keys"]) >= 2:
            print("An error occurred (LimitExceeded)", file=sys.stderr); save(); sys.exit(254)
        k = {"id": "AKIA" + "".join(random.choices(string.ascii_uppercase + string.digits, k=16)),
             "secret": "".join(random.choices(string.ascii_letters + string.digits + "/+", k=40)),
             "status": "Active"}
        db["keys"].append(k)
        print(json.dumps({"AccessKey": {"UserName": "docs-checker-pull", "AccessKeyId": k["id"], "Status": "Active",
                                        "SecretAccessKey": k["secret"]}}))
        save(); sys.exit(0)
    if args[:2] == ["iam", "delete-access-key"]:
        kid = opt("--access-key-id")
        before = len(db["keys"])
        db["keys"] = [k for k in db["keys"] if k["id"] != kid]
        db["deleted"].append(kid); save(); sys.exit(0 if len(db["keys"]) < before else 254)
    print("stub aws: unexpected call", args, file=sys.stderr); save(); sys.exit(99)
    """
)

# Records argv, the working directory, and the key it was handed (the secret only as a hash).
ANSIBLE_STUB = textwrap.dedent(
    """\
    import hashlib, json, os, sys
    path = os.environ["STUB_ANSIBLE_DB"]
    db = json.load(open(path))
    secret = os.environ.get("DOCS_CHECKER_PULL_AWS_SECRET_ACCESS_KEY")
    db["runs"].append({"argv": sys.argv[1:], "cwd": os.getcwd(),
                       "key_id": os.environ.get("DOCS_CHECKER_PULL_AWS_ACCESS_KEY_ID"),
                       "secret_sha": hashlib.sha256(secret.encode()).hexdigest() if secret else None})
    json.dump(db, open(path, "w"))
    sys.exit(2 if os.environ.get("STUB_ANSIBLE_FAIL") else 0)
    """
)


@pytest.fixture
def env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, src in (("vault", VAULT_STUB), ("aws", AWS_STUB), ("ansible-playbook", ANSIBLE_STUB)):
        (bindir / f"stub_{name}.py").write_text(src)
        exe = bindir / name
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{bindir / f"stub_{name}.py"}" "$@"\n')
        exe.chmod(0o755)
    dbs = {"vault": tmp_path / "vault.json", "aws": tmp_path / "aws.json", "ansible": tmp_path / "ansible.json"}
    dbs["vault"].write_text(json.dumps({"kv": {}, "calls": [], "writes": []}))
    dbs["aws"].write_text(json.dumps({"keys": [], "calls": [], "deleted": []}))
    dbs["ansible"].write_text(json.dumps({"runs": []}))

    class Env:
        def run(self, *args, **extra):
            e = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "VAULT_ADDR": "http://127.0.0.1:8200",
                 "STUB_VAULT_DB": str(dbs["vault"]), "STUB_AWS_DB": str(dbs["aws"]),
                 "STUB_ANSIBLE_DB": str(dbs["ansible"]), **extra}
            return subprocess.run(["bash", str(SCRIPT), *args], env=e, capture_output=True, text=True)

        def state(self, which):
            return json.loads(dbs[which].read_text())

        def seed_aws_key(self, kid="AKIAOLDOLDOLDOLDOLD1", secret="o" * 40):
            db = self.state("aws")
            db["keys"].append({"id": kid, "secret": secret, "status": "Active"})
            dbs["aws"].write_text(json.dumps(db))

        def seed_vault(self, kid="AKIAOLDOLDOLDOLDOLD1", secret="o" * 40):
            db = self.state("vault")
            db["kv"]["docs-checker-pull"] = {"aws-access-key-id": kid, "aws-secret-access-key": secret,
                                             "aws-region": "us-east-2"}
            dbs["vault"].write_text(json.dumps(db))

    return Env()


def vault_secret(env):
    return env.state("vault")["kv"]["docs-checker-pull"]


def all_argv(env):
    return [a for which in ("vault", "aws") for call in env.state(which)["calls"] for a in call] + \
           [a for run in env.state("ansible")["runs"] for a in run["argv"]]


def aws_writes(env):
    return [c for c in env.state("aws")["calls"] if c[1] in ("create-access-key", "delete-access-key")]


def assert_no_secret_leaked(env, *procs):
    secrets = [k["secret"] for k in env.state("aws")["keys"]]
    stored = env.state("vault")["kv"].get("docs-checker-pull", {}).get("aws-secret-access-key")
    secrets += [stored] if stored else []
    assert secrets, "nothing to check"
    for s in secrets:
        assert all(s not in a for a in all_argv(env)), "a secret was passed on a command line"
        for p in procs:
            assert s not in p.stdout and s not in p.stderr, "a secret was printed"


def test_first_run_creates_one_key_stores_it_in_vault_and_places_it(env):
    p = env.run()
    assert p.returncode == 0, p.stderr
    keys = env.state("aws")["keys"]
    assert len(keys) == 1
    stored = vault_secret(env)
    assert stored == {"aws-access-key-id": keys[0]["id"], "aws-secret-access-key": keys[0]["secret"],
                      "aws-region": "us-east-2"}
    [run] = env.state("ansible")["runs"]
    assert run["argv"][0] == "playbooks/ate-image-credentials.yml" and run["cwd"].endswith("/ansible")
    assert run["key_id"] == keys[0]["id"]
    assert run["secret_sha"] == hashlib.sha256(keys[0]["secret"].encode()).hexdigest()
    assert "--check" not in run["argv"]
    assert_no_secret_leaked(env, p)


def test_a_rerun_creates_no_key_and_places_what_vault_holds(env):
    env.run()
    p = env.run()
    assert p.returncode == 0, p.stderr
    assert len(env.state("aws")["keys"]) == 1
    assert len([c for c in aws_writes(env) if c[1] == "create-access-key"]) == 1
    runs = env.state("ansible")["runs"]
    assert len(runs) == 2 and runs[0]["key_id"] == runs[1]["key_id"] and runs[0]["secret_sha"] == runs[1]["secret_sha"]
    assert_no_secret_leaked(env, p)


def test_not_logged_in_to_vault_stops_before_anything(env):
    p = env.run(STUB_VAULT_NOAUTH="1")
    assert p.returncode != 0 and "vault login" in p.stderr
    assert env.state("aws")["calls"] == [] and env.state("ansible")["runs"] == []


def test_a_missing_iam_user_stops_before_any_write(env):
    p = env.run(STUB_AWS_NO_USER="1")
    assert p.returncode != 0 and "Terraform" in p.stderr
    assert aws_writes(env) == [] and env.state("vault")["writes"] == [] and env.state("ansible")["runs"] == []


def test_a_vault_read_error_is_never_taken_for_absent(env):
    p = env.run(STUB_VAULT_READ_ERROR="1")
    assert p.returncode != 0 and "not found" in p.stderr
    assert aws_writes(env) == [] and env.state("ansible")["runs"] == []


def test_a_failed_vault_write_deletes_the_new_key(env):
    p = env.run(STUB_VAULT_WRITE_ERROR="1")
    assert p.returncode != 0
    created = [c for c in aws_writes(env) if c[1] == "create-access-key"]
    assert len(created) == 1
    assert env.state("aws")["keys"] == [], "the key whose secret could not be stored must be deleted"
    assert env.state("ansible")["runs"] == []


def test_keys_on_the_user_that_vault_does_not_know_are_refused(env):
    env.seed_aws_key()
    p = env.run()
    assert p.returncode != 0 and "AKIAOLDOLDOLDOLDOLD1" in p.stderr
    assert aws_writes(env) == [] and env.state("vault")["writes"] == [] and env.state("ansible")["runs"] == []


def test_a_vault_key_that_is_not_on_the_user_is_refused(env):
    env.seed_vault(kid="AKIAGONEGONEGONEGONE")
    p = env.run()
    assert p.returncode != 0 and "--rotate" in p.stderr
    assert env.state("ansible")["runs"] == []


def test_rotate_adds_a_second_key_places_it_and_keeps_the_old_one(env):
    env.seed_aws_key()
    env.seed_vault()
    p = env.run("--rotate")
    assert p.returncode == 0, p.stderr
    ids = [k["id"] for k in env.state("aws")["keys"]]
    assert len(ids) == 2 and "AKIAOLDOLDOLDOLDOLD1" in ids
    new = [i for i in ids if i != "AKIAOLDOLDOLDOLDOLD1"][0]
    assert vault_secret(env)["aws-access-key-id"] == new
    assert env.state("ansible")["runs"][-1]["key_id"] == new
    assert env.state("aws")["deleted"] == [], "rotate never deletes: --retire-old does, after the new key works"
    assert "--retire-old" in p.stdout
    assert_no_secret_leaked(env, p)


def test_rotate_refuses_when_the_user_already_has_two_keys(env):
    env.seed_aws_key()
    env.seed_aws_key(kid="AKIASECONDSECONDSEC2")
    env.seed_vault()
    p = env.run("--rotate")
    assert p.returncode != 0 and "--retire-old" in p.stderr
    assert aws_writes(env) == [] and env.state("ansible")["runs"] == []


def test_a_failed_placement_fails_the_run_and_deletes_nothing(env):
    env.seed_aws_key()
    env.seed_vault()
    p = env.run("--rotate", STUB_ANSIBLE_FAIL="1")
    assert p.returncode != 0
    assert len(env.state("aws")["keys"]) == 2 and env.state("aws")["deleted"] == []


def test_retire_old_deletes_every_key_but_vaults_and_places_nothing(env):
    env.seed_aws_key()
    env.seed_aws_key(kid="AKIANEWNEWNEWNEWNEW2", secret="n" * 40)
    env.seed_vault(kid="AKIANEWNEWNEWNEWNEW2", secret="n" * 40)
    p = env.run("--retire-old")
    assert p.returncode == 0, p.stderr
    assert [k["id"] for k in env.state("aws")["keys"]] == ["AKIANEWNEWNEWNEWNEW2"]
    assert env.state("aws")["deleted"] == ["AKIAOLDOLDOLDOLDOLD1"]
    assert env.state("ansible")["runs"] == [] and env.state("vault")["writes"] == []


def test_retire_old_refuses_when_vaults_key_is_not_on_the_user(env):
    env.seed_aws_key()
    env.seed_vault(kid="AKIAGONEGONEGONEGONE")
    p = env.run("--retire-old")
    assert p.returncode != 0 and "not an active key" in p.stderr
    assert env.state("aws")["deleted"] == []


def test_check_previews_the_placement_and_writes_nothing(env):
    env.seed_aws_key()
    env.seed_vault()
    p = env.run("--check")
    assert p.returncode == 0, p.stderr
    [run] = env.state("ansible")["runs"]
    assert "--check" in run["argv"] and "--diff" in run["argv"]
    assert aws_writes(env) == [] and env.state("vault")["writes"] == []


def test_check_with_nothing_in_vault_creates_nothing(env):
    p = env.run("--check")
    assert p.returncode != 0 and "without --check first" in p.stderr
    assert aws_writes(env) == [] and env.state("vault")["writes"] == [] and env.state("ansible")["runs"] == []


def test_limit_is_passed_to_ansible(env):
    p = env.run("--limit", "k3s-worker-02")
    assert p.returncode == 0, p.stderr
    argv = env.state("ansible")["runs"][0]["argv"]
    assert argv[argv.index("--limit") + 1] == "k3s-worker-02"


@pytest.mark.parametrize("args", [["--rotate", "--retire-old"], ["--rotate", "--check"], ["--bogus"]])
def test_conflicting_or_unknown_options_are_refused(env, args):
    p = env.run(*args)
    assert p.returncode == 2
    assert env.state("vault")["calls"] == [] and env.state("aws")["calls"] == []
