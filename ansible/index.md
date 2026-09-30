---
type: "Directory Index"
title: "Ansible"
description: "Directory listing for the Ansible tree: the host layer (hypervisor and k3s VMs) below the cluster."
tags: [ansible, infrastructure, hosts]
---

# ansible Index

| path | purpose |
|---|---|
| `inventory/` | Hosts, groups, per-host addresses and VM names |
| `playbooks/bootstrap.yml` | One-time: passwordless sudo for the admin user on hosts that still prompt (the hypervisor) |
| `playbooks/site.yml` | Baseline for every host (roles common, hypervisor, k3s_node) |
| `playbooks/patch.yml` | VM patching with drained serial reboots |
| `playbooks/patch-hypervisor.yml` | Confirmed hypervisor reboot with ordered VM shutdown |
| `playbooks/k3s-authn.yml` | Validate and install the control plane's kube-apiserver authn config |
| `playbooks/tasks/patch-node.yml` | Per-node patch steps included by `patch.yml` |
| `roles/` | `common`, `hypervisor`, `k3s_node` (holds `files/authn/`) |
| `requirements.yml` | Ansible Galaxy collections (`ansible-galaxy collection install -r requirements.yml`) |
| `ansible.cfg` | Inventory path and defaults |
