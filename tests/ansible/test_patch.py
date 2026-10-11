"""playbooks/patch.yml shape. The drain/reboot path needs a real cluster; it is verified in check
mode during rollout. What can be pinned without one is pinned here, plus a real run of the CPU-flag
pre-flight against throwaway containers."""
import json
import subprocess
import uuid

import pytest
import yaml

from conftest import ANSIBLE, needs_docker, node_write, run_playbook

PATCH = ANSIBLE / "playbooks" / "patch.yml"
TASKS = ANSIBLE / "playbooks" / "tasks"
PATCH_NODE = TASKS / "patch-node.yml"
CPU_PREFLIGHT = TASKS / "cpu-flags-preflight.yml"
GROUP_VARS = ANSIBLE / "inventory" / "group_vars" / "all.yml"


def _patch_node():
    return yaml.safe_load(PATCH_NODE.read_text())


def _reboot_block():
    return [t for t in _patch_node() if t.get("name") == "Drain, reboot, uncordon"][0]["block"]


def test_patch_has_no_localhost_play():
    """Review Focus 1: --limit also drops localhost, so a `hosts: localhost` pre-flight would silently
    not run. The pre-flight is a delegated task inside each node play."""
    plays = yaml.safe_load(PATCH.read_text())
    assert [p["hosts"] for p in plays] == ["k3s_workers", "k3s_control"], "workers first, control last"
    assert all(p.get("serial") == 1 for p in plays), "one node at a time"


def test_every_node_play_starts_with_the_preflight():
    tasks = _patch_node()
    assert tasks[0]["name"].startswith("Pre-flight")
    assert tasks[0]["delegate_to"] == "localhost"


def test_cpu_flag_preflight_runs_before_anything_changes():
    """substrate#1657: a gVisor snapshot restores only on a node with the same CPU features. The
    check must come before the upgrade, or a refused run has already changed the node."""
    names = [t["name"] for t in _patch_node()]
    imported = [i for i, t in enumerate(_patch_node())
                if t.get("ansible.builtin.import_tasks") == "cpu-flags-preflight.yml"]
    assert imported, "patch-node.yml must import cpu-flags-preflight.yml"
    assert imported[0] < names.index("Upgrade packages")


def test_cpu_flag_preflight_reads_every_k3s_node_even_under_limit():
    """--limit k3s-worker-02 must still compare against the other two nodes, so the read loops over
    the k3s_nodes group (not the play's hosts) and is delegated; it must also run under --check."""
    read = yaml.safe_load(CPU_PREFLIGHT.read_text())[0]
    assert read["loop"] == "{{ groups['k3s_nodes'] }}"
    assert read["delegate_to"] == "{{ item }}"
    assert read["check_mode"] is False
    assert read["changed_when"] is False


def test_substrate_workers_are_deleted_after_a_reboot():
    """substrate#1837: a worker that lives through a reboot comes back with a new IP its actors never
    learn. Only after the node is Ready again, only that node's workers, and a no-op when none."""
    block = _reboot_block()
    names = [t["name"] for t in block]
    ready = [i for i, n in enumerate(names) if n.startswith("Wait for Ready")][0]
    find = [i for i, n in enumerate(names) if n.startswith("Find Substrate worker pods")][0]
    delete = find + 1
    assert ready < find < delete
    finder = block[find]["kubernetes.core.k8s_info"]
    assert finder["label_selectors"] == ["ate.dev/worker-pool"]
    assert finder["field_selectors"] == ["spec.nodeName={{ inventory_hostname }}"]
    assert block[find]["delegate_to"] == "localhost"
    deleter = block[delete]
    assert deleter["kubernetes.core.k8s"]["state"] == "absent"
    assert deleter["loop"] == "{{ substrate_workers.resources }}", "loops over what was found"


def test_drain_timeout_stays_300():
    """Decided with Substrate: a longer drain does not save an agent turn in flight on an evicted
    worker (substrate#1526), it only slows patching."""
    assert yaml.safe_load(GROUP_VARS.read_text())["drain_timeout"] == 300


# --- the CPU-flag pre-flight, run for real against two throwaway "nodes" --------------------------

FLAGS = "fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge sse sse2 ssse3 sse4_1 sse4_2 popcnt avx"


def _cpuinfo(flags):
    return f"processor\t: 0\nmodel name\t: test\nflags\t\t: {flags}\nbugs\t\t: none\n"


@pytest.fixture
def two_nodes(node_image):
    names = [f"ansible-test-{uuid.uuid4().hex[:8]}" for _ in range(2)]
    for name in names:
        subprocess.run(["docker", "run", "-d", "--name", name, node_image], check=True, capture_output=True)
    yield names
    for name in names:
        subprocess.run(["docker", "rm", "-fv", name], capture_output=True)


def _run_preflight(tmp_path, nodes, *extra):
    inv = tmp_path / "inventory.yml"
    inv.write_text(json.dumps({"all": {
        "children": {"k3s_nodes": {"children": {
            "k3s_control": {"hosts": {}},
            "k3s_workers": {"hosts": {n: {"ansible_host": n} for n in nodes}},
        }}},
        "vars": {"ansible_connection": "community.docker.docker",
                 "ansible_python_interpreter": "/usr/bin/python3", "ansible_user": "root",
                 "cpu_flags_file": "/tmp/cpuinfo"},
    }}))
    play = tmp_path / "preflight.yml"
    play.write_text(yaml.safe_dump([{
        "name": "CPU-flag pre-flight only", "hosts": "k3s_workers", "gather_facts": False,
        "tasks": [{"ansible.builtin.import_tasks": str(CPU_PREFLIGHT)}],
    }]))
    # Limited to ONE node: the other must still be read (delegated), which is the point.
    return run_playbook(str(play), inv, "--limit", nodes[0], *extra)


@needs_docker
def test_preflight_passes_when_flags_match(two_nodes, tmp_path):
    for n in two_nodes:
        node_write(n, "/tmp/cpuinfo", _cpuinfo(FLAGS))
    r = _run_preflight(tmp_path, two_nodes)
    assert r.returncode == 0, r.stdout + r.stderr


@needs_docker
def test_preflight_refuses_and_names_the_flag_when_one_node_differs(two_nodes, tmp_path):
    node_write(two_nodes[0], "/tmp/cpuinfo", _cpuinfo(FLAGS))
    node_write(two_nodes[1], "/tmp/cpuinfo", _cpuinfo(FLAGS.replace(" avx", "")))
    r = _run_preflight(tmp_path, two_nodes)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "CPU flags differ" in r.stdout and "avx" in r.stdout, r.stdout
    assert "cpu_flags_preflight=false" in r.stdout


@needs_docker
def test_preflight_refuses_an_unreadable_node(two_nodes, tmp_path):
    node_write(two_nodes[0], "/tmp/cpuinfo", _cpuinfo(FLAGS))
    node_write(two_nodes[1], "/tmp/cpuinfo", "processor\t: 0\n")  # no flags line at all
    r = _run_preflight(tmp_path, two_nodes)
    assert r.returncode != 0, r.stdout + r.stderr


@needs_docker
def test_preflight_can_be_waived_for_a_known_kernel_difference(two_nodes, tmp_path):
    node_write(two_nodes[0], "/tmp/cpuinfo", _cpuinfo(FLAGS))
    node_write(two_nodes[1], "/tmp/cpuinfo", _cpuinfo(FLAGS + " pti"))
    r = _run_preflight(tmp_path, two_nodes, "-e", "cpu_flags_preflight=false")
    assert r.returncode == 0, r.stdout + r.stderr
