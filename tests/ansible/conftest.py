"""Shared fixtures: a throwaway Ubuntu container that stands in for a host (see Dockerfile.node)."""
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ANSIBLE = REPO / "ansible"
HERE = Path(__file__).parent
IMAGE = "ansible-test-node:local"


def docker_available():
    return shutil.which("docker") is not None and \
        subprocess.run(["docker", "info"], capture_output=True).returncode == 0


needs_docker = pytest.mark.skipif(not docker_available(), reason="needs Docker")


@pytest.fixture(scope="session")
def node_image():
    r = subprocess.run(["docker", "build", "-q", "-t", IMAGE, "-f", str(HERE / "Dockerfile.node"), str(HERE)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return IMAGE


@pytest.fixture
def node(node_image):
    name = f"ansible-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "run", "-d", "--name", name, node_image], check=True, capture_output=True)
    yield name
    subprocess.run(["docker", "rm", "-fv", name], capture_output=True)


def node_exec(name, cmd, stdin=None):
    return subprocess.run(["docker", "exec", "-i", name, "sh", "-c", cmd], input=stdin,
                          capture_output=True, text=True)


def node_write(name, path, content):
    r = node_exec(name, f"mkdir -p $(dirname {path}) && cat > {path}", stdin=content)
    assert r.returncode == 0, r.stderr


def node_read(name, path):
    r = node_exec(name, f"cat {path}")
    return r.stdout if r.returncode == 0 else ""


@pytest.fixture
def inventory_for(node, tmp_path):
    """Inventory placing the container in one group; the other groups exist but are empty."""
    def make(group):
        hosts = {g: {} for g in ("hypervisors", "k3s_control", "k3s_workers")}
        hosts[group] = {node: {"ansible_host": node}}
        inv = tmp_path / f"inventory-{group}.yml"
        inv.write_text(json.dumps({"all": {  # JSON is valid YAML; avoids hand-indenting nested groups
            "children": {
                "hypervisors": {"hosts": hosts["hypervisors"]},
                "k3s_nodes": {"children": {
                    "k3s_control": {"hosts": hosts["k3s_control"]},
                    "k3s_workers": {"hosts": hosts["k3s_workers"]},
                }},
            },
            "vars": {
                "ansible_connection": "community.docker.docker",
                "ansible_python_interpreter": "/usr/bin/python3",
                "ansible_user": "root",
                "k3s_unit": "k3s",
                "k3s_node_gateway": "10.0.1.1",
                "k3s_node_nameservers": ["8.8.8.8", "1.1.1.1"],
            },
        }}, indent=2))
        return inv
    return make


def run_playbook(playbook, inventory, *args, env=None, check=False):
    cmd = ["ansible-playbook", "-i", str(inventory), str(ANSIBLE / "playbooks" / playbook), *args]
    if check:
        cmd.append("--check")
    full_env = {**os.environ, "ANSIBLE_FORCE_COLOR": "0", "ANSIBLE_NOCOLOR": "1", **(env or {})}
    return subprocess.run(cmd, cwd=ANSIBLE, capture_output=True, text=True, env=full_env, timeout=900)
