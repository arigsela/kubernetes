"""Lint and syntax gates for the ansible/ tree. The ansible-validate CI job runs exactly this."""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ANSIBLE = REPO / "ansible"
PLAYBOOKS = sorted((ANSIBLE / "playbooks").glob("*.yml")) if (ANSIBLE / "playbooks").is_dir() else []


def run(*args):
    return subprocess.run(args, cwd=ANSIBLE, capture_output=True, text=True)


def test_inventory_has_the_four_hosts():
    r = run("ansible-inventory", "--graph")
    assert r.returncode == 0, r.stderr
    for host in ("asela-k8s", "k3s-control-01", "k3s-worker-01", "k3s-worker-02"):
        assert host in r.stdout, r.stdout
    assert "@k3s_nodes:" in r.stdout, "k3s_control and k3s_workers must be children of k3s_nodes"


def test_ansible_lint_is_clean():
    r = run("ansible-lint", "--offline")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("playbook", PLAYBOOKS, ids=lambda p: p.name)
def test_playbook_syntax(playbook):
    r = run("ansible-playbook", "--syntax-check", str(playbook))
    assert r.returncode == 0, r.stderr
