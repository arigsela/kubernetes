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
    assert r.returncode != 0 and "missing" in r.stdout, r.stdout + r.stderr
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


@needs_docker
def test_the_shipped_files_validate(tmp_path):
    r = run_playbook("k3s-authn.yml", no_node_inventory(tmp_path), "--tags", "validate")
    assert r.returncode == 0 and "anonymous → 401" in r.stdout, r.stdout + r.stderr
