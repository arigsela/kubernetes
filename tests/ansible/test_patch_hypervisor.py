"""playbooks/patch-hypervisor.yml's gate. The shutdown/reboot path needs libvirt; verified by a
check-mode run during rollout."""
from conftest import run_playbook


def empty_inventory(tmp_path):
    inv = tmp_path / "inventory-empty.yml"
    inv.write_text("all:\n  children:\n    hypervisors:\n      hosts: {}\n    k3s_nodes:\n      hosts: {}\n")
    return inv


def test_refuses_without_confirm(tmp_path):
    r = run_playbook("patch-hypervisor.yml", empty_inventory(tmp_path), "--tags", "gate")
    assert r.returncode != 0 and "REFUSED" in r.stdout and "confirm=yes" in r.stdout, r.stdout + r.stderr


def test_confirm_accepts_true_and_yes(tmp_path):
    """Review Focus 3: -e confirm=true arrives as a boolean, -e confirm=yes as a string."""
    for value in ("yes", "true"):
        r = run_playbook("patch-hypervisor.yml", empty_inventory(tmp_path), "--tags", "gate", "-e", f"confirm={value}")
        assert r.returncode == 0, value + "\n" + r.stdout + r.stderr


def test_a_wrong_value_is_refused(tmp_path):
    r = run_playbook("patch-hypervisor.yml", empty_inventory(tmp_path), "--tags", "gate", "-e", "confirm=maybe")
    assert r.returncode != 0 and "REFUSED" in r.stdout
