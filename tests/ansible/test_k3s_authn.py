"""playbooks/k3s-authn.yml. It changes the control plane's kube-apiserver config, so these pin
what must NEVER happen: a file the API server rejects reaching the node, a config without the
`anonymous` block (k3s would silently ENABLE anonymous auth), an --oidc-* flag slipping past a
refusal guard. Validation uses a real k3s in Docker; the "node" is the test container.
"""
import os
import stat

import pytest

from conftest import ANSIBLE, needs_docker, node_exec, node_read, node_write, run_playbook

SRC = ANSIBLE / "roles" / "k3s_node" / "files" / "authn"
SHIPPED_AUTHN = (SRC / "authn-config.yaml").read_text()
SHIPPED_DROPIN = (SRC / "config.yaml.d" / "10-authn.yaml").read_text()


def source_dir(tmp_path, authn=SHIPPED_AUTHN, dropin=SHIPPED_DROPIN):
    src = tmp_path / "authn"
    (src / "config.yaml.d").mkdir(parents=True)
    if authn is not None:
        (src / "authn-config.yaml").write_text(authn)
    if dropin is not None:
        (src / "config.yaml.d" / "10-authn.yaml").write_text(dropin)
    return src


def no_node_inventory(tmp_path):
    inv = tmp_path / "inventory-empty.yml"
    inv.write_text("all:\n  children:\n    k3s_control:\n      hosts: {}\n")
    return inv


def stub_on_path(tmp_path, name, body):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return {"PATH": f"{d}:{os.environ['PATH']}"}


# ---- static guards (no Docker) --------------------------------------------------------------

def test_config_without_the_anonymous_block_is_refused(tmp_path):
    """Without it, k3s drops --anonymous-auth=false and anonymous becomes ENABLED."""
    src = source_dir(tmp_path, authn="apiVersion: apiserver.config.k8s.io/v1\nkind: AuthenticationConfiguration\njwt: []\n")
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "static", "-e", f"authn_src={src}")
    assert r.returncode != 0 and "anonymous" in r.stdout, r.stdout + r.stderr


def test_an_oidc_flag_in_the_dropin_is_refused_but_a_comment_is_not(tmp_path):
    src = source_dir(tmp_path, dropin="kube-apiserver-arg+:\n  - oidc-issuer-url=https://x\n")
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "static", "-e", f"authn_src={src}")
    assert r.returncode != 0 and "oidc" in r.stdout, r.stdout + r.stderr
    src = source_dir(tmp_path / "ok", dropin="# no --oidc-* flags here, by design\nkube-apiserver-arg+:\n  - authentication-config=/etc/rancher/k3s/authn-config.yaml\n")
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "static", "-e", f"authn_src={src}")
    assert r.returncode == 0, r.stdout + r.stderr


def test_shipped_files_pass_the_static_guards(tmp_path):
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "static")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_missing_source_file_is_a_clear_error(tmp_path):
    """Review Focus 2."""
    src = source_dir(tmp_path, dropin=None)
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "static", "-e", f"authn_src={src}")
    assert r.returncode != 0 and "missing config.yaml.d/10-authn.yaml under" in r.stdout, r.stdout + r.stderr
    assert "Traceback" not in r.stderr


def test_docker_down_is_a_clear_error(tmp_path):
    """Review Focus 5: a stub `docker` whose `info` fails stands in for a stopped colima."""
    env = stub_on_path(tmp_path, "docker", "#!/bin/sh\n[ \"$1\" = info ] && exit 1\nexit 0\n")
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "validate", env=env)
    assert r.returncode != 0 and "Docker is not running" in r.stdout, r.stdout + r.stderr


# ---- the throwaway k3s (Docker) -------------------------------------------------------------

@needs_docker
def test_a_file_the_api_server_rejects_fails_validation(tmp_path):
    broken = ("apiVersion: apiserver.config.k8s.io/v1\nkind: AuthenticationConfiguration\n"
              "anonymous:\n  enabled: false\njwt:\n  - issuer:\n      url: http://not-https.example\n"
              "      audiences: [kubernetes]\n    claimMappings:\n      username:\n        claim: sub\n")
    src = source_dir(tmp_path, authn=broken)
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "validate", "-e", f"authn_src={src}")
    assert r.returncode != 0 and "VALIDATION FAILED" in r.stdout, r.stdout + r.stderr
    # the rescue fires for ANY failure in the block; pin the cause to k3s rejecting the file
    assert "invalid authentication configuration" in r.stdout and "must be https" in r.stdout, r.stdout + r.stderr


@needs_docker
def test_the_shipped_files_validate(tmp_path):
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "validate")
    assert r.returncode == 0 and "anonymous → 401" in r.stdout, r.stdout + r.stderr


# ---- play 2: the node ------------------------------------------------------------------------
# kubectl is replaced by a stub on PATH: /readyz answers ok once $READYZ_OK_AFTER calls were made;
# /metrics reports a success counter of 5, plus 1 once the NEW authn-config.yaml is on the node
# (an "instant" reload, the regression this suite pins). Anonymous checks go to a local HTTP
# server that answers 401 to everything.
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

KUBECTL_STUB = r"""#!/usr/bin/env bash
case "$*" in
  *"get --raw /readyz"*)
    n=$(( $(cat "$CALLS" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$CALLS"
    if [ "$n" -ge "${READYZ_OK_AFTER:-1}" ]; then echo ok; else echo "[+]ping failed"; exit 1; fi ;;
  *"get --raw /metrics"*)
    cur=$(docker exec "$NODE" sha256sum /etc/rancher/k3s/authn-config.yaml 2>/dev/null | cut -d' ' -f1)
    if [ "$cur" = "$NEW_SHA" ]; then c=6; else c=5; fi
    echo "apiserver_authentication_config_controller_automatic_reloads_total{apiserver_id_hash=\"x\",status=\"success\"} $c"
    echo "apiserver_authentication_config_controller_automatic_reloads_total{apiserver_id_hash=\"x\",status=\"failure\"} 0" ;;
esac
"""


@pytest.fixture
def api_401():
    class Deny(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(401)
            self.end_headers()

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Deny)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def node_env(tmp_path, node):
    env = stub_on_path(tmp_path, "kubectl", KUBECTL_STUB)
    env.update({"CALLS": str(tmp_path / "calls"), "NODE": node,
                "NEW_SHA": hashlib.sha256(SHIPPED_AUTHN.encode()).hexdigest()})
    return env


def only_read_k3s_unit(node):
    """The preflight runs `systemctl cat k3s` (read-only, to find --oidc-* flags), which the stub logs.
    Nothing else may have been called: no restart, no stop, no daemon-reload."""
    calls = node_read(node, "/var/log/systemctl.log").splitlines()
    return all(c.strip() == "cat k3s" for c in calls)


def install_args(api_401, **extra):
    args = ["--skip-tags", "validate", "-e", f"k3s_api_url={api_401}"]
    for k, v in extra.items():
        args += ["-e", f"{k}={v}"]
    return args


@needs_docker
def test_a_file_the_api_server_rejects_never_reaches_the_node(tmp_path, node, inventory_for, node_env):
    broken = ("apiVersion: apiserver.config.k8s.io/v1\nkind: AuthenticationConfiguration\n"
              "anonymous:\n  enabled: false\njwt:\n  - issuer:\n      url: http://not-https.example\n"
              "      audiences: [kubernetes]\n    claimMappings:\n      username:\n        claim: sub\n")
    src = source_dir(tmp_path, authn=broken)
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), "-e", f"authn_src={src}", env=node_env)
    assert r.returncode != 0 and "VALIDATION FAILED" in r.stdout, r.stdout + r.stderr
    assert node_read(node, "/etc/rancher/k3s/authn-config.yaml") == "", "the broken file reached the node"
    assert node_read(node, "/var/log/systemctl.log") == "", "k3s was touched"


@needs_docker
def test_dry_run_validates_then_only_reads_the_node(tmp_path, node, inventory_for, node_env, api_401):
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), "-e", f"k3s_api_url={api_401}",
                     env=node_env, check=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "anonymous → 401" in r.stdout and "DRY:" in r.stdout and "restart k3s" in r.stdout
    assert node_read(node, "/etc/rancher/k3s/authn-config.yaml") == ""
    assert only_read_k3s_unit(node), "k3s was touched: " + node_read(node, "/var/log/systemctl.log")
    assert node_exec(node, "test -e /etc/rancher/k3s/authn-backup").returncode != 0, "a backup dir was created"
    assert node_exec(node, "ls /etc/rancher/k3s/*.tmp /etc/rancher/k3s/config.yaml.d/*.tmp 2>/dev/null").stdout == ""


@needs_docker
def test_an_oidc_flag_already_on_the_node_is_refused(tmp_path, node, inventory_for, node_env, api_401):
    """Regression (B21): the guard once passed exactly when it should fire. The match sits after
    200 comment lines and before 8000 non-comment lines, the shape that hid the old bug."""
    cfg = "".join(f"# line {i}\n" for i in range(200)) + \
        "kube-apiserver-arg:\n  - oidc-issuer-url=https://dex.example\n" + \
        "".join(f"extra-setting-{i:06d}: padding padding padding\n" for i in range(8000))
    node_write(node, "/etc/rancher/k3s/config.yaml", cfg)
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), *install_args(api_401), env=node_env)
    assert r.returncode != 0 and "already sets an --oidc-*" in r.stdout, r.stdout + r.stderr
    assert node_read(node, "/etc/rancher/k3s/authn-config.yaml") == ""


@needs_docker
def test_already_installed_does_nothing(tmp_path, node, inventory_for, node_env, api_401):
    node_write(node, "/etc/rancher/k3s/authn-config.yaml", SHIPPED_AUTHN)
    node_write(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml", SHIPPED_DROPIN)
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), *install_args(api_401), env=node_env)
    assert r.returncode == 0 and "already installed" in r.stdout, r.stdout + r.stderr
    assert only_read_k3s_unit(node), "k3s was touched: " + node_read(node, "/var/log/systemctl.log")
    assert node_exec(node, "test -e /etc/rancher/k3s/authn-backup").returncode != 0, "a backup dir was created"
    assert node_exec(node, "ls /etc/rancher/k3s/*.tmp /etc/rancher/k3s/config.yaml.d/*.tmp 2>/dev/null").stdout == ""


@needs_docker
def test_an_instant_hot_reload_is_seen(tmp_path, node, inventory_for, node_env, api_401):
    """Regression: a config with no issuers reloads in under a second, before an installer that
    read its baseline AFTER writing the file could look. The baseline must come first."""
    node_write(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml", SHIPPED_DROPIN)   # drop-in unchanged
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), *install_args(api_401), env=node_env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reloaded (success counter 6)" in r.stdout, r.stdout
    assert node_read(node, "/etc/rancher/k3s/authn-config.yaml") == SHIPPED_AUTHN
    assert "restart" not in node_read(node, "/var/log/systemctl.log"), "a config-only change must not restart k3s"
    assert "anonymous still denied" in r.stdout


@needs_docker
def test_a_dropin_change_restarts_and_the_api_returns(tmp_path, node, inventory_for, node_env, api_401):
    node_write(node, "/etc/rancher/k3s/authn-config.yaml", SHIPPED_AUTHN)
    node_write(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml", "# old drop-in\n")
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), *install_args(api_401), env=node_env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert node_read(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml") == SHIPPED_DROPIN
    assert node_read(node, "/var/log/systemctl.log").count("restart k3s") == 1
    backups = node_exec(node, "ls /etc/rancher/k3s/authn-backup").stdout.split()
    assert len(backups) == 1
    assert node_read(node, f"/etc/rancher/k3s/authn-backup/{backups[0]}/config.yaml.d/10-authn.yaml") == "# old drop-in\n"
    assert node_exec(node, "stat -c %a /etc/rancher/k3s/config.yaml.d/10-authn.yaml").stdout.strip() == "600"


@needs_docker
def test_a_dropin_change_rolls_back_when_the_api_does_not_return(tmp_path, node, inventory_for, node_env, api_401):
    node_write(node, "/etc/rancher/k3s/authn-config.yaml", SHIPPED_AUTHN)
    node_write(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml", "# old drop-in\n")
    node_env["READYZ_OK_AFTER"] = "999"
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), *install_args(api_401, ready_timeout=3), env=node_env)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "ROLLBACK DID NOT RESTORE THE API" in r.stdout, r.stdout
    assert node_read(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml") == "# old drop-in\n", "rollback must restore the old drop-in"
    assert node_read(node, "/var/log/systemctl.log").count("restart k3s") == 2, "install restart + rollback restart"


@needs_docker
def test_rollback_reports_when_the_api_comes_back(tmp_path, node, inventory_for, node_env, api_401):
    """ready_timeout=3 → retries 1 → 2 /readyz attempts per wait. The first wait's 2 attempts fail,
    the rollback's first attempt (call 3) succeeds."""
    node_write(node, "/etc/rancher/k3s/authn-config.yaml", SHIPPED_AUTHN)
    node_write(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml", "# old drop-in\n")
    node_env["READYZ_OK_AFTER"] = "3"
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), *install_args(api_401, ready_timeout=3), env=node_env)
    assert r.returncode != 0 and "rolled back; the API is back on the previous config" in r.stdout, r.stdout + r.stderr
    assert node_read(node, "/etc/rancher/k3s/config.yaml.d/10-authn.yaml") == "# old drop-in\n"


@needs_docker
def test_install_alone_still_refuses_a_config_without_the_anonymous_block(tmp_path, node, inventory_for, node_env, api_401):
    """--skip-tags validate skips play 1; play 2 must not install (and hot-reload) a file that would
    turn anonymous auth ON."""
    src = source_dir(tmp_path, authn="apiVersion: apiserver.config.k8s.io/v1\nkind: AuthenticationConfiguration\njwt: []\n")
    r = run_playbook("k3s-authn.yml", inventory_for("k3s_control"), *install_args(api_401), "-e", f"authn_src={src}", env=node_env)
    assert r.returncode != 0 and "anonymous" in r.stdout, r.stdout + r.stderr
    assert node_read(node, "/etc/rancher/k3s/authn-config.yaml") == ""
    assert only_read_k3s_unit(node), "k3s was touched: " + node_read(node, "/var/log/systemctl.log")
    assert node_exec(node, "test -e /etc/rancher/k3s/authn-backup").returncode != 0
