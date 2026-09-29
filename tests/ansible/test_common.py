"""roles/common through playbooks/site.yml --tags common, against the test container."""
import re

from conftest import needs_docker, node_exec, node_read, run_playbook


@needs_docker
def test_common_role_applies_and_is_idempotent(node, inventory_for):
    inv = inventory_for("k3s_control")
    r = run_playbook("site.yml", inv, "--tags", "common", "-e", "common_admin_user=asela")
    assert r.returncode == 0, r.stdout + r.stderr
    assert node_read(node, "/etc/sudoers.d/asela") == "asela ALL=(ALL) NOPASSWD:ALL\n"
    hardening = node_read(node, "/etc/ssh/sshd_config.d/10-hardening.conf")
    assert "PasswordAuthentication no" in hardening and "PermitRootLogin prohibit-password" in hardening
    assert node_exec(node, "sshd -t").returncode == 0, "the drop-in must leave sshd's config valid"
    assert "reload ssh" in node_read(node, "/var/log/systemctl.log"), "sshd must be reloaded after the drop-in"
    policy = node_read(node, "/etc/apt/apt.conf.d/52unattended-upgrades-local")
    assert 'Automatic-Reboot "false"' in policy and 'Remove-Unused-Dependencies "true"' in policy
    assert 'Unattended-Upgrade "1"' in node_read(node, "/etc/apt/apt.conf.d/20auto-upgrades")

    again = run_playbook("site.yml", inv, "--tags", "common", "-e", "common_admin_user=asela")
    assert again.returncode == 0, again.stdout + again.stderr
    assert re.search(r"changed=0\s", again.stdout), "second run must change nothing:\n" + again.stdout


@needs_docker
def test_bootstrap_installs_only_the_sudoers_dropin(node, inventory_for):
    r = run_playbook("bootstrap.yml", inventory_for("hypervisors"), "-e", "common_admin_user=asela")
    assert r.returncode == 0, r.stdout + r.stderr
    assert node_read(node, "/etc/sudoers.d/asela") == "asela ALL=(ALL) NOPASSWD:ALL\n"
    assert node_read(node, "/etc/ssh/sshd_config.d/10-hardening.conf") == "", "bootstrap must not harden sshd"
