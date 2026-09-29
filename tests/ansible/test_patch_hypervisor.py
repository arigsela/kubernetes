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


def test_confirm_accepts_a_json_boolean(tmp_path):
    r = run_playbook("patch-hypervisor.yml", empty_inventory(tmp_path), "--tags", "gate", "-e", '{"confirm": true}')
    assert r.returncode == 0, r.stdout + r.stderr


def test_limit_to_the_hypervisor_still_refuses_without_confirm(tmp_path):
    """--limit drops localhost (and so the gate play); the hypervisor play must refuse on its own.
    Never pass confirm=yes here: with a local connection the apt task would run on this machine."""
    inv = tmp_path / "inventory-local-hv.yml"
    inv.write_text(
        "all:\n"
        "  children:\n"
        "    hypervisors:\n"
        "      hosts:\n"
        "        hv-local:\n"
        "          ansible_host: 127.0.0.1\n"
        "          ansible_connection: local\n"
        "          ansible_become: false\n"
        "          vm_shutdown_order: []\n"
        "    k3s_nodes:\n"
        "      hosts: {}\n"
    )
    r = run_playbook("patch-hypervisor.yml", inv, "--limit", "hv-local")
    assert r.returncode != 0 and "REFUSED" in r.stdout, r.stdout + r.stderr
    assert "Upgrade packages" not in r.stdout, r.stdout
