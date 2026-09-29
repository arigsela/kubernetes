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
| `playbooks/site.yml` | Baseline for every host (roles common, hypervisor, k3s_node) |
| `playbooks/patch.yml` | VM patching with drained serial reboots |
| `playbooks/patch-hypervisor.yml` | Confirmed hypervisor reboot with ordered VM shutdown |
| `playbooks/k3s-authn.yml` | Validate and install the control plane's kube-apiserver authn config |
| `roles/` | `common`, `hypervisor`, `k3s_node` (holds `files/authn/`) |
