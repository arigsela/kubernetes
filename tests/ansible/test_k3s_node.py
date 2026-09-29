"""roles/k3s_node through playbooks/site.yml --tags k3s_node, against the test container."""
from conftest import needs_docker, node_read, node_write, run_playbook

DHCP = "network:\n  version: 2\n  ethernets:\n    enp1s0:\n      dhcp4: true\n"
STATIC_VARS = ("-e", "k3s_node_static_ip=10.0.1.5/24", "-e", "k3s_node_mac=52:54:00:67:81:26",
               "-e", "k3s_node_netplan_apply=false")   # no networkd in a container


@needs_docker
def test_a_worker_gets_a_static_netplan_and_cloud_init_is_disabled(node, inventory_for):
    node_write(node, "/etc/netplan/50-cloud-init.yaml", DHCP)
    r = run_playbook("site.yml", inventory_for("k3s_workers"), "--tags", "k3s_node", *STATIC_VARS)
    assert r.returncode == 0, r.stdout + r.stderr
    rendered = node_read(node, "/etc/netplan/50-cloud-init.yaml")
    assert "- 10.0.1.5/24" in rendered and 'macaddress: "52:54:00:67:81:26"' in rendered
    assert "via: 10.0.1.1" in rendered and "- 8.8.8.8" in rendered and "dhcp4" not in rendered
    assert node_read(node, "/etc/cloud/cloud.cfg.d/99-disable-network-config.cfg") == "network: {config: disabled}\n"


@needs_docker
def test_a_host_without_a_static_ip_is_left_alone(node, inventory_for):
    node_write(node, "/etc/netplan/50-cloud-init.yaml", DHCP)
    r = run_playbook("site.yml", inventory_for("k3s_control"), "--tags", "k3s_node")
    assert r.returncode == 0, r.stdout + r.stderr
    assert node_read(node, "/etc/netplan/50-cloud-init.yaml") == DHCP
    assert node_read(node, "/etc/cloud/cloud.cfg.d/99-disable-network-config.cfg") == ""


@needs_docker
def test_a_rejected_netplan_config_is_restored(node, inventory_for):
    """Review Focus 4: an address without a prefix makes `netplan generate` fail; the previous file
    must come back and the play must fail loudly."""
    node_write(node, "/etc/netplan/50-cloud-init.yaml", DHCP)
    r = run_playbook("site.yml", inventory_for("k3s_workers"), "--tags", "k3s_node",
                     "-e", "k3s_node_static_ip=10.0.1.5", "-e", "k3s_node_mac=52:54:00:67:81:26",
                     "-e", "k3s_node_netplan_apply=false")
    assert r.returncode != 0
    assert "netplan rejected" in r.stdout, r.stdout
    assert node_read(node, "/etc/netplan/50-cloud-init.yaml") == DHCP


@needs_docker
def test_a_rejected_netplan_config_with_no_previous_file_is_removed(node, inventory_for):
    """No previous file means no backup to restore; the rejected file must not be left for next boot."""
    r = run_playbook("site.yml", inventory_for("k3s_workers"), "--tags", "k3s_node",
                     "-e", "k3s_node_static_ip=10.0.1.5", "-e", "k3s_node_mac=52:54:00:67:81:26",
                     "-e", "k3s_node_netplan_apply=false")
    assert r.returncode != 0
    assert "netplan rejected" in r.stdout, r.stdout
    assert node_read(node, "/etc/netplan/50-cloud-init.yaml") == ""
