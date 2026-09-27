"""scripts/install-authn-config.sh (plan Phase 5, Task 5.2).

The installer changes the control plane's kube-apiserver config, so the tests pin what must
NEVER happen: a file the API server rejects reaching the node, a config without the
`anonymous` block (it would silently ENABLE anonymous auth under k3s), and an --oidc-* flag
slipping past the refusal guard. The real script runs against a stub `ssh` that records every
command; validation uses a real k3s in Docker, like tests/admission-policies.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "install-authn-config.sh"
SRC = REPO / "node-config" / "k3s-control-01"

SSH_STUB = """#!/usr/bin/env bash
# stub ssh: record the command; answer reads from $STUB_NODE_CFG; swallow stdin for writes
echo "$*" >> "$STUB_LOG"
case "$*" in
  *"sha256sum"*) ;;
  *"cat /etc/rancher/k3s/config.yaml"*) cat "$STUB_NODE_CFG" ;;
  *tee*) cat >/dev/null ;;
esac
"""
WRITES = ("tee ", "mv ", "mkdir", "restart", "cp -p")


def docker_available():
    return shutil.which("docker") is not None and \
        subprocess.run(["docker", "info"], capture_output=True).returncode == 0


needs_docker = pytest.mark.skipif(not docker_available(), reason="needs Docker for the k3s validation")


def run(tmp_path, *args, authn=None, dropin=None, node_cfg="disable:\n  - traefik\n"):
    src = tmp_path / "src"
    (src / "config.yaml.d").mkdir(parents=True)
    (src / "authn-config.yaml").write_text(authn if authn is not None else (SRC / "authn-config.yaml").read_text())
    (src / "config.yaml.d" / "10-authn.yaml").write_text(
        dropin if dropin is not None else (SRC / "config.yaml.d" / "10-authn.yaml").read_text())
    stub = tmp_path / "ssh"
    stub.write_text(SSH_STUB); stub.chmod(0o755)
    (tmp_path / "node.cfg").write_text(node_cfg)
    env = {"PATH": f"{Path(sys.executable).parent}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
           "HOME": str(Path.home()), "AUTHN_SRC": str(src), "NODE_SSH": str(stub),
           "STUB_LOG": str(tmp_path / "ssh.log"), "STUB_NODE_CFG": str(tmp_path / "node.cfg")}
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=300)
    log = (tmp_path / "ssh.log").read_text() if (tmp_path / "ssh.log").exists() else ""
    return r, log


def test_syntax_and_help():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    r = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--dry-run" in r.stdout


def test_config_without_the_anonymous_block_is_refused(tmp_path):
    """Without it, k3s drops --anonymous-auth=false and anonymous becomes ENABLED."""
    r, log = run(tmp_path, "--dry-run",
                 authn="apiVersion: apiserver.config.k8s.io/v1\nkind: AuthenticationConfiguration\njwt: []\n")
    assert r.returncode == 2 and "anonymous" in r.stderr, r.stderr
    assert log == "", "nothing may touch the node"


def test_an_oidc_flag_in_the_dropin_is_refused_but_a_comment_is_not(tmp_path):
    r, _ = run(tmp_path, "--dry-run", dropin="kube-apiserver-arg+:\n  - oidc-issuer-url=https://x\n")
    assert r.returncode == 2 and "oidc" in r.stderr, r.stderr


def test_shipped_files_pass_the_static_guards():
    import yaml
    d = yaml.safe_load((SRC / "authn-config.yaml").read_text())
    assert d["anonymous"] == {"enabled": False} and isinstance(d["jwt"], list)
    assert "authentication-config=/etc/rancher/k3s/authn-config.yaml" in \
        (SRC / "config.yaml.d" / "10-authn.yaml").read_text()


@needs_docker
def test_a_file_the_api_server_rejects_never_reaches_the_node(tmp_path):
    broken = ("apiVersion: apiserver.config.k8s.io/v1\nkind: AuthenticationConfiguration\n"
              "anonymous:\n  enabled: false\njwt:\n  - issuer:\n      url: http://not-https.example\n"
              "      audiences: [kubernetes]\n    claimMappings:\n      username:\n        claim: sub\n")
    r, log = run(tmp_path, authn=broken)
    assert r.returncode == 3 and "VALIDATION FAILED" in r.stderr, r.stdout + r.stderr
    assert not any(w in log for w in WRITES), f"a write reached the node: {log}"


@needs_docker
def test_dry_run_validates_then_only_reads_the_node(tmp_path):
    r, log = run(tmp_path, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "anonymous → 401" in r.stdout and "DRY: would restart k3s" in r.stdout
    assert not any(w in log for w in WRITES), f"dry run wrote to the node: {log}"


@needs_docker
def test_an_oidc_flag_already_on_the_node_is_refused(tmp_path):
    """Regression: with `grep -q` at the end of a pipeline, `set -o pipefail` turned a MATCH into
    a failure, so this guard passed exactly when it should fire. The text after the match must
    exceed the 64 KiB pipe buffer, and must NOT be comments (the guard filters those out before
    grep), or the writer finishes before grep exits and the bug hides. Two earlier versions of
    this test passed against the buggy code for exactly those two reasons."""
    cfg = "".join(f"# line {i}\n" for i in range(200)) + \
        "kube-apiserver-arg:\n  - oidc-issuer-url=https://dex.example\n" + \
        "".join(f"extra-setting-{i:06d}: padding padding padding\n" for i in range(8000))
    r, log = run(tmp_path, "--dry-run", node_cfg=cfg)
    assert r.returncode == 2 and "already sets an --oidc-*" in r.stderr, r.stdout + r.stderr


INSTANT_RELOAD_SSH = """#!/usr/bin/env bash
# stub ssh for a hot-reload install whose reload is INSTANT: writing the file bumps the counter.
echo "$*" >> "$STUB_LOG"
case "$*" in
  *"sha256sum /etc/rancher/k3s/config.yaml.d/10-authn.yaml"*) echo "$DROPIN_SHA" ;;
  *"sha256sum"*) ;;
  *"cat /etc/rancher/k3s/config.yaml"*) echo "disable: [traefik]" ;;
  *tee*) cat >/dev/null; echo $(( $(cat "$COUNTER") + 1 )) > "$COUNTER" ;;
esac
"""
KUBECTL_STUB = """#!/usr/bin/env bash
case "$*" in
  *"get --raw /metrics"*) echo "apiserver_authentication_config_controller_automatic_reloads_total{apiserver_id_hash=\\"x\\",status=\\"success\\"} $(cat "$COUNTER")" ;;
  *"get --raw /readyz"*) echo ok ;;
esac
"""
CURL_STUB = """#!/usr/bin/env bash
for a in "$@"; do case "$a" in https://stub-api*) printf 401; exit 0 ;; esac; done
exec /usr/bin/curl "$@"
"""


@needs_docker
def test_an_instant_hot_reload_is_seen(tmp_path):
    """Regression: the rollback rehearsal (jwt: []) reported "no reload observed" for a reload
    that had already happened. A config with no issuers reloads in under a second, before the
    installer read its baseline AFTER writing the file. The baseline must come first."""
    import hashlib
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    for name, body in (("ssh", INSTANT_RELOAD_SSH), ("kubectl", KUBECTL_STUB), ("curl", CURL_STUB)):
        (bin_dir / name).write_text(body); (bin_dir / name).chmod(0o755)
    (tmp_path / "counter").write_text("5\n")
    env = {"PATH": f"{bin_dir}:{Path(sys.executable).parent}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
           "HOME": str(Path.home()), "AUTHN_SRC": str(SRC), "NODE_SSH": str(bin_dir / "ssh"),
           "STUB_LOG": str(tmp_path / "ssh.log"), "COUNTER": str(tmp_path / "counter"),
           "DROPIN_SHA": hashlib.sha256((SRC / "config.yaml.d" / "10-authn.yaml").read_bytes()).hexdigest(),
           "API_URL": "https://stub-api.invalid"}
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reloaded (success counter 6)" in r.stdout, r.stdout
    assert "restart" not in (tmp_path / "ssh.log").read_text(), "a config-only change must not restart k3s"
