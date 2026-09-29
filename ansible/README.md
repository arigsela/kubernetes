# ansible/ — the host layer

Manages the Ubuntu hypervisor (`asela-k8s`, 10.0.1.101) and the three k3s VMs at the OS level.
Terraform (`terraform/`) owns AWS and the Argo CD bootstrap; Argo CD (`base-apps/`) owns everything
in the cluster; system-upgrade-controller owns the k3s version. This tree owns what is left:
packages, sudo, sshd, unattended-upgrades, the workers' static addresses, reboots, and the control
plane's kube-apiserver authentication config.

Design: `docs/superpowers/specs/2026-09-29-ansible-host-management-design.md`.

## Install (workstation)

    uv tool install "ansible-core==2.21.4" --with "kubernetes==36.0.3" --with requests
    uv tool install "ansible-lint==26.9.0"
    cd ansible && ansible-galaxy collection install -r requirements.yml

Run every command from this directory (ansible.cfg lives here). `k3s-authn.yml` needs Docker
(`colima start`); `patch*.yml` use your kubeconfig for the cluster.

## Layout

| path | what |
|---|---|
| `inventory/hosts.yml` | `hypervisors` (asela-k8s); `k3s_nodes` = `k3s_control` (k3s-control-01) + `k3s_workers` (01, 02). Node names match Kubernetes node names. |
| `inventory/host_vars/` | per-VM static address + MAC (workers), `vm_shutdown_order` (hypervisor) |
| `roles/common` | sudoers drop-in, sshd hardening drop-in, unattended-upgrades (security only, no auto-reboot), qemu-guest-agent on VMs |
| `roles/hypervisor` | libvirt packages, groups, VM autostart |
| `roles/k3s_node` | static netplan for hosts with `k3s_node_static_ip`; `files/authn/` is the control plane's kube-apiserver authn config (installed only by `k3s-authn.yml`) |

## Runbook

| do | command |
|---|---|
| First run on a host that still asks for a sudo password | `ansible-playbook playbooks/bootstrap.yml --ask-become-pass` |
| Preview / apply the baseline | `ansible-playbook playbooks/site.yml --check --diff` then without `--check` |
| Patch the VMs (apt full-upgrade, drained serial reboot, workers first) | `ansible-playbook playbooks/patch.yml` (`--limit k3s-worker-02` for one) |
| Patch and reboot the hypervisor (all VMs shut down in order, never forced) | `ansible-playbook playbooks/patch-hypervisor.yml -e confirm=yes` (`-e velero_backup=yes` to back up first) |
| Change the API server's authn config | edit `roles/k3s_node/files/authn/`, then `ansible-playbook playbooks/k3s-authn.yml --check`, then without `--check` |

If `patch.yml` stops mid-way, that node is left cordoned and the next run's pre-flight refuses:
`kubectl uncordon <node>` once you have looked, then re-run.

## Tests

`python -m pytest tests/ansible/ -v` — lint, syntax, and the playbooks against a throwaway Ubuntu
container (`tests/ansible/Dockerfile.node`); `k3s-authn.yml`'s validation runs a real k3s in
Docker. The same command runs in CI (`ansible-validate` in `.github/workflows/validate.yaml`).

## Not here (yet)

Recovery after a hypervisor power loss (`recovery/`), in-cluster scheduling of patch runs,
Vault-backed secrets, hypervisor netplan, VM creation.
