"""playbooks/patch.yml shape. The drain/reboot path needs a real cluster; it is verified in check
mode during rollout. What can be pinned without one is pinned here."""
import yaml

from conftest import ANSIBLE

PATCH = ANSIBLE / "playbooks" / "patch.yml"


def test_patch_has_no_localhost_play():
    """Review Focus 1: --limit also drops localhost, so a `hosts: localhost` pre-flight would silently
    not run. The pre-flight is a delegated task inside each node play."""
    plays = yaml.safe_load(PATCH.read_text())
    assert [p["hosts"] for p in plays] == ["k3s_workers", "k3s_control"], "workers first, control last"
    assert all(p.get("serial") == 1 for p in plays), "one node at a time"


def test_every_node_play_starts_with_the_preflight():
    tasks = yaml.safe_load((ANSIBLE / "playbooks" / "tasks" / "patch-node.yml").read_text())
    assert tasks[0]["name"].startswith("Pre-flight")
    assert tasks[0]["delegate_to"] == "localhost"
