"""playbooks/ate-image-credentials.yml against the test container (SPEC §T.129, §V.100).

The real binary is replaced by a fake provider served over file:// with its own sha256. The fake
records the environment it was run with and answers like ecr-credential-provider does, including the
registry token, which must never reach the play's output.
"""
import hashlib
import re

import yaml
from conftest import ANSIBLE, needs_docker, node_exec, node_read, node_write, run_playbook

DIR = "/opt/ate-image-credential-provider"
# AWS's documented example pair (docs.aws.amazon.com): the shape of a real key, and known to scanners as fake.
KEY_ID = "AKIAIOSFODNN7EXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
TOKEN = "ECR-REGISTRY-TOKEN-NEVER-PRINTED"
KEYS = {"DOCS_CHECKER_PULL_AWS_ACCESS_KEY_ID": KEY_ID, "DOCS_CHECKER_PULL_AWS_SECRET_ACCESS_KEY": SECRET}
# Variables the shell running the fake provider adds by itself (dash/bash), not passed by the play.
SHELL_NOISE = {"PWD", "OLDPWD", "SHLVL", "_"}

FAKE_PROVIDER = f"""#!/bin/sh
env > /tmp/provider-env
req=$(cat)
case "$req" in *'"image":"852893458518.dkr.ecr.us-east-2.amazonaws.com/docs-checker"'*) ;;
  *) echo "unexpected request: $req" >&2; exit 1 ;; esac
grep -qx '\\[docs-checker-pull\\]' "$AWS_SHARED_CREDENTIALS_FILE" || {{ echo "failed to get shared config profile" >&2; exit 1; }}
echo '{{"apiVersion":"credentialprovider.kubelet.k8s.io/v1","kind":"CredentialProviderResponse","cacheKeyType":"Registry","cacheDuration":"6h0m0s","auth":{{"852893458518.dkr.ecr.us-east-2.amazonaws.com":{{"username":"AWS","password":"{TOKEN}"}}}}}}'
"""
DENIED_PROVIDER = """#!/bin/sh
cat >/dev/null
echo "operation error ECR: GetAuthorizationToken, StatusCode: 400, UnrecognizedClientException" >&2
exit 1
"""


def arch(node):
    return node_exec(node, "uname -m").stdout.strip()


def stage(node, content):
    """Put a fake provider in the container; return the -e overrides that point the play at it."""
    node_write(node, "/srv/fake-provider", content)
    sha = hashlib.sha256(content.encode()).hexdigest()
    return ("-e", "ate_cp_url=file:///srv/fake-provider", "-e", f"ate_cp_sha256={sha}",
            "-e", f"ate_cp_expected_arch={arch(node)}", "-e", "ate_cp_verify_retries=1", "-e", "ate_cp_verify_delay=0")


def stat(node, path):
    r = node_exec(node, f"stat -c '%a %U:%G' {path}")
    return r.stdout.strip() if r.returncode == 0 else None


def assert_nothing_secret_in(r):
    out = r.stdout + r.stderr
    assert SECRET not in out, "the secret access key reached the play output"
    assert TOKEN not in out, "the registry token reached the play output"


@needs_docker
def test_installs_the_binary_and_a_root_only_credentials_file(node, inventory_for):
    r = run_playbook("ate-image-credentials.yml", inventory_for("k3s_workers"), *stage(node, FAKE_PROVIDER), env=KEYS)
    assert r.returncode == 0, r.stdout + r.stderr
    assert stat(node, DIR) == "700 root:root"
    assert stat(node, f"{DIR}/ecr-credential-provider") == "755 root:root"
    assert node_read(node, f"{DIR}/ecr-credential-provider") == FAKE_PROVIDER
    assert stat(node, f"{DIR}/aws-credentials") == "600 root:root"
    assert node_read(node, f"{DIR}/aws-credentials") == (
        f"[docs-checker-pull]\naws_access_key_id = {KEY_ID}\naws_secret_access_key = {SECRET}\n")
    assert_nothing_secret_in(r)


@needs_docker
def test_the_verify_runs_the_provider_with_only_the_configs_environment(node, inventory_for):
    r = run_playbook("ate-image-credentials.yml", inventory_for("k3s_workers"), *stage(node, FAKE_PROVIDER), env=KEYS)
    assert r.returncode == 0, r.stdout + r.stderr
    seen = dict(line.split("=", 1) for line in node_read(node, "/tmp/provider-env").splitlines() if "=" in line)
    seen = {k: v for k, v in seen.items() if k not in SHELL_NOISE}
    # Exactly atelet's stand-in env (rustfs's variables) overlaid with the config's: nothing else from the
    # host or the play (env -i), so this is the environment atelet will give the binary.
    assert set(seen) == {"AWS_REGION", "AWS_S3_USE_PATH_STYLE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_PROFILE",
                         "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_ENDPOINT_URL",
                         "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN", "AWS_CONFIG_FILE",
                         "AWS_IGNORE_CONFIGURED_ENDPOINT_URLS", "AWS_EC2_METADATA_DISABLED"}, seen
    assert seen["AWS_SHARED_CREDENTIALS_FILE"] == f"{DIR}/aws-credentials"
    assert seen["AWS_PROFILE"] == "docs-checker-pull"
    # The seeded dummies (a fake key, an unreachable endpoint) must have been overridden by the blanks.
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_ENDPOINT_URL",
                 "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN", "AWS_CONFIG_FILE"):
        assert seen[name] == "", f"{name}={seen[name]!r}: the inherited value survived"
    assert seen["AWS_IGNORE_CONFIGURED_ENDPOINT_URLS"] == seen["AWS_EC2_METADATA_DISABLED"] == "true"
    assert "credentials for 852893458518.dkr.ecr.us-east-2.amazonaws.com" in r.stdout
    assert_nothing_secret_in(r)


@needs_docker
def test_a_rerun_changes_nothing(node, inventory_for):
    inv, extra = inventory_for("k3s_workers"), stage(node, FAKE_PROVIDER)
    first = run_playbook("ate-image-credentials.yml", inv, *extra, env=KEYS)
    assert first.returncode == 0, first.stdout + first.stderr
    again = run_playbook("ate-image-credentials.yml", inv, *extra, env=KEYS)
    assert again.returncode == 0, again.stdout + again.stderr
    assert re.search(r"changed=0\s", again.stdout), again.stdout


@needs_docker
def test_refuses_without_the_key_and_touches_nothing(node, inventory_for):
    r = run_playbook("ate-image-credentials.yml", inventory_for("k3s_workers"), *stage(node, FAKE_PROVIDER))
    assert r.returncode != 0
    assert "provision-docs-checker-pull.sh" in r.stdout, r.stdout
    assert stat(node, DIR) is None


def override(extra, name, value):
    return [f"{name}={value}" if a.startswith(f"{name}=") else a for a in extra]


@needs_docker
def test_a_checksum_mismatch_writes_no_credentials(node, inventory_for):
    extra = override(stage(node, FAKE_PROVIDER), "ate_cp_sha256", "0" * 64)
    r = run_playbook("ate-image-credentials.yml", inventory_for("k3s_workers"), *extra, env=KEYS)
    assert r.returncode != 0
    assert stat(node, f"{DIR}/ecr-credential-provider") is None
    assert stat(node, f"{DIR}/aws-credentials") is None


@needs_docker
def test_a_provider_that_cannot_get_a_token_fails_the_play_with_its_error(node, inventory_for):
    r = run_playbook("ate-image-credentials.yml", inventory_for("k3s_workers"), *stage(node, DENIED_PROVIDER), env=KEYS)
    assert r.returncode != 0
    assert "UnrecognizedClientException" in r.stdout, r.stdout
    assert_nothing_secret_in(r)


@needs_docker
def test_check_mode_writes_nothing(node, inventory_for):
    r = run_playbook("ate-image-credentials.yml", inventory_for("k3s_workers"), *stage(node, FAKE_PROVIDER),
                     env=KEYS, check=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert stat(node, DIR) is None
    assert_nothing_secret_in(r)


@needs_docker
def test_another_architecture_is_refused_before_any_write(node, inventory_for):
    extra = override(stage(node, FAKE_PROVIDER), "ate_cp_expected_arch", "sparc64")
    r = run_playbook("ate-image-credentials.yml", inventory_for("k3s_workers"), *extra, env=KEYS)
    assert r.returncode != 0
    assert stat(node, DIR) is None


def test_the_play_targets_the_workers_only():
    [play] = yaml.safe_load((ANSIBLE / "playbooks/ate-image-credentials.yml").read_text())
    assert play["hosts"] == "k3s_workers", "atelet runs only on the workers (it does not tolerate the control plane)"
    assert play["become"] is True
