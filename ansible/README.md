# ansible/ — the host layer

Manages the Ubuntu hypervisor (`asela-k8s`, 10.0.1.101) and the three k3s VMs at the OS level.
Terraform (`terraform/`) owns AWS and the Argo CD bootstrap; Argo CD (`base-apps/`) owns everything
in the cluster; system-upgrade-controller owns the k3s version. This tree owns what is left:
packages, sudo, sshd, unattended-upgrades, the workers' static addresses, reboots, and the control
plane's kube-apiserver authentication config.

## Install (workstation)

    uv tool install "ansible-core==2.21.4" --with "kubernetes==36.0.3" --with requests
    uv tool install "ansible-lint==26.9.0"
    cd ansible && ansible-galaxy collection install -r requirements.yml

Run every command from this directory (ansible.cfg lives here). Playbooks arrive in later tasks;
see the "Runbook" section once they exist.
