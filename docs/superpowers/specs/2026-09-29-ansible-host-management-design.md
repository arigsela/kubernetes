# Ansible host management for the hypervisor and k3s VMs

**Date:** 2026-09-29
**Status:** approved design, awaiting implementation plan
**Scope:** a new `ansible/` layer in this repo that manages the Ubuntu hypervisor and the three k3s VMs at the OS level, and replaces `scripts/install-authn-config.sh` with a playbook.

## 1. Why

The repo already holds two of the three infrastructure layers as top-level directories: `terraform/` (AWS IAM/KMS and the Argo CD bootstrap, applied by Atlantis) and `base-apps/` (everything in the cluster, synced by Argo CD). The host layer below them is managed by hand: ssh sessions, a recovery walkthrough, and a handful of scripts that loop over hard-coded node IPs.

The decision to keep terraform and gitops in one repo stands. The terraform layer is small and tightly coupled to `base-apps/` (the Argo CD OIDC config references `base-apps/dex` and lands in the same PRs), and a solo operator gains nothing from the cross-repo split that platform/app team boundaries motivate. Ansible joins as a third top-level directory, following the same monorepo-by-layer convention.

### Facts that shaped the design (probed 2026-09-29)

| Host | Role | OS | sudo | Notes |
|---|---|---|---|---|
| asela-k8s, 10.0.1.101 | hypervisor, libvirt 10.0 | Ubuntu 24.04.4 | password required | 68 pending packages, reboot pending, all 3 VMs autostart |
| k3s-control-01, 10.0.1.50 | k3s server (SQLite) | Ubuntu 24.04.3 | NOPASSWD | static netplan, 65 pending packages, reboot pending |
| k3s-worker-01, 10.0.1.5 | k3s agent | Ubuntu 24.04.3 | NOPASSWD | DHCP netplan, reboot pending |
| k3s-worker-02, 10.0.1.108 | k3s agent | Ubuntu 24.04.3 | NOPASSWD | DHCP netplan, reboot pending |

- unattended-upgrades is installed and enabled on every host, but nothing reboots: all four have carried reboot-required for over two weeks.
- The worker IPs are DHCP leases. `recovery/post-restart-restore.sh` still lists them as 10.0.1.51 and 10.0.1.52.
- k3s v1.36.4+k3s1 on all nodes. Its version is owned by system-upgrade-controller in `base-apps/`.
- The k3s agents' join token sits in their unit env files. It must never be copied into this repo.
- Docker on the workstation (colima) is required for the authn validation play.

### Decisions already made

| Question | Decision |
|---|---|
| Worker IP stability | Ansible pins the IPs they hold today via static netplan, matching the control node |
| Patching model | On-demand playbooks the operator triggers. VMs get drained serial reboots. The hypervisor reboot is a separate, explicitly confirmed playbook. unattended-upgrades stays on for security patches only, automatic reboot off |
| Hypervisor sudo | A one-time bootstrap installs a NOPASSWD sudoers drop-in for `asela`, matching the VMs |
| authn installer | Ported into the `k3s_node` layer now, preserving every guard in the script |
| Execution | From the operator's workstation. GitHub-hosted runners cannot reach 10.0.1.0/24 |
| Secrets | None in v1. The ssh key path is inventory config |

## 2. Layout

```
ansible/
├── ansible.cfg              # inventory path, roles_path, pipelining, host_key_checking
├── requirements.yml         # ansible.posix, community.general, community.docker, kubernetes.core
├── README.md                # install, first run, runbook per playbook
├── inventory/
│   ├── hosts.yml            # groups: hypervisors, k3s_control, k3s_workers, k3s_nodes (children)
│   ├── group_vars/
│   │   ├── all.yml          # ansible_user: asela, ansible_ssh_private_key_file, interpreter
│   │   ├── k3s_control.yml  # k3s_unit: k3s
│   │   └── k3s_workers.yml  # k3s_unit: k3s-agent; drain timeouts live in all.yml
│   └── host_vars/
│       ├── asela-k8s.yml    # vm_names in shutdown order
│       ├── k3s-control-01.yml
│       ├── k3s-worker-01.yml  # static_ip, mac, vm_name
│       └── k3s-worker-02.yml
├── playbooks/
│   ├── bootstrap.yml        # hypervisor sudoers; run once with --ask-become-pass
│   ├── site.yml             # baseline for every host; idempotent
│   ├── patch.yml            # VM patching with drained serial reboots
│   ├── patch-hypervisor.yml # confirmed host reboot with ordered VM shutdown
│   └── k3s-authn.yml        # replaces scripts/install-authn-config.sh
└── roles/
    ├── common/
    ├── hypervisor/
    └── k3s_node/
        └── files/authn/     # authn-config.yaml, config.yaml.d/10-authn.yaml (moved from node-config/)
```

Removed: `node-config/` and `scripts/install-authn-config.sh`. Nothing else in the repo moves. `base-apps/` keeps its name because the Argo CD master-app and the ApplicationSet reference it.

## 3. Baseline roles (`site.yml`)

`site.yml` applies `common` to all hosts, `hypervisor` to `hypervisors`, `k3s_node` to `k3s_nodes`. It encodes what the hosts already do, so the first real run should report few changes.

### common
- `/etc/sudoers.d/asela` with `asela ALL=(ALL) NOPASSWD:ALL`, validated with `visudo -cf`.
- sshd: `PasswordAuthentication no`, `PermitRootLogin prohibit-password`, via a drop-in under `sshd_config.d/`, validated with `sshd -t` before the handler reloads.
- unattended-upgrades: security origin only, `Automatic-Reboot "false"`, `Remove-Unused-Dependencies "true"`.
- `qemu-guest-agent` installed and enabled on VMs (skipped on the hypervisor).
- Timezone.

### hypervisor
- Packages present: `qemu-kvm`, `libvirt-daemon-system`, `libvirt-clients`, `smartmontools`.
- `asela` in the `libvirt` and `kvm` groups.
- Every VM in `vm_names` set to autostart.
- Netplan is **not** managed. A wrong bridge definition takes down the whole lab and the current file is unreadable without sudo.

### k3s_node
- Workers only: a static netplan file with the IP and MAC from host_vars, written to a temp path, checked with `netplan generate`, then moved into place and applied. A `wait_for_connection` follows so a broken apply fails the play instead of silently orphaning the host. The control node's existing static file is left untouched.
- No sysctl, no k3s version, no k3s install tasks. system-upgrade-controller owns the version.
- The authn files live in this role's `files/authn/` so the role is the single source for control-plane node config, but they are installed only by `k3s-authn.yml`, never by `site.yml`, because installation restarts the API server.

## 4. `patch.yml`: VM patching

Order: `k3s_workers` first, then `k3s_control`, `serial: 1` in both plays.

**Pre-flight play** (localhost, kubernetes.core): fail unless every node is Ready and none is cordoned. This stops a second run from draining while a previous one left a node cordoned.

**Per host:**
1. `apt update`, `apt full-upgrade`, `apt autoremove`.
2. If `/var/run/reboot-required` exists:
   - cordon and drain from localhost with `kubernetes.core.k8s_drain`: ignore daemonsets, delete emptydir data, 300 s timeout;
   - `ansible.builtin.reboot` with a 600 s timeout;
   - wait for the k3s unit (`k3s` or `k3s-agent` by group) to be active;
   - uncordon from localhost; wait until the node reports Ready.
3. Otherwise report "no reboot required" and move on.

**Control node caveats.** It is a single SQLite server, so the API is down while it reboots. Every localhost task that touches the API after the reboot retries until the API answers. Pods pinned to the control node by `node.kubernetes.io/workload: infrastructure` (Argo CD among them) cannot be rescheduled during the drain; they sit Pending and return on uncordon. That is an accepted window, not a failure.

## 5. `patch-hypervisor.yml`: host reboot

- Refuses to run unless `-e confirm=yes` is passed. The refusal message states that this stops every VM.
- Optional `-e velero_backup=yes` runs `recovery/pre-shutdown-backup.sh` from localhost first. Off by default.
- `apt update`, `apt full-upgrade`, `apt autoremove` on the hypervisor.
- If reboot is required:
  1. `virsh shutdown` each VM in `vm_names` order (workers, then control). Wait until every VM is `shut off`, 300 s timeout. On timeout the play **fails**; it never `virsh destroy`s.
  2. Reboot the host, wait for ssh.
  3. Wait until libvirt autostart reports every VM `running`.
  4. From localhost: wait for the API, then for every node Ready, 600 s.
- No drain: the whole cluster is going down anyway, and ACPI shutdown stops the k3s units cleanly.

## 6. `k3s-authn.yml`: the port

Replaces `scripts/install-authn-config.sh` one guard for one guard. Source files: `roles/k3s_node/files/authn/`.

**Play 1, localhost: validate before anything touches the node.**
1. Static guards: `authn-config.yaml` is an `apiserver.config.k8s.io/v1 AuthenticationConfiguration`, carries `anonymous: {enabled: false}` (k3s drops its own `--anonymous-auth=false` when handed a config file, and the upstream default is enabled), and `jwt` is a list. The drop-in sets no `--oidc-*` flag outside comments. A failure here is a refusal with the same wording the script used.
2. Throwaway k3s: `community.docker.docker_container` starts `rancher/k3s:<cluster version>` privileged with the candidate files mounted read-only, traefik/metrics-server/local-storage/servicelb disabled, port 6443 published on loopback. Poll `docker exec ... kubectl get --raw /readyz` up to 120 s. Confirm the `apiserver_authentication_config_controller_last_config_info` metric is present. Confirm an anonymous `GET /version` answers 401. Remove the container and its anonymous volumes in an `always` block.
3. The image tag is a variable pinned to the cluster's k3s version, with a comment to bump it alongside upgrades.

**Play 2, `k3s_control`.**
1. Preflight: read `config.yaml`, `config.yaml.d/*.yaml`, and `systemctl cat k3s`; refuse if any non-comment line sets `oidc-`.
2. Checksums of both target files vs. the sources decide `changed_authn` and `changed_dropin`. Both unchanged: report "already installed" and end the play.
3. If only the config changed, read the `automatic_reloads_total{status=success|failure}` counters from localhost **before** installing (the reload can complete in under a second).
4. Back up both current files, or a marker for a missing one, to `/etc/rancher/k3s/authn-backup/<UTC timestamp>/`.
5. Install each changed file by writing `<name>.tmp` at 0600 and renaming into place.
6. Drop-in changed: inside a `block`, `systemctl restart k3s` and poll `/readyz` from localhost for up to 240 s. `rescue`: restore from the backup directory (delete files that the marker says did not exist), restart k3s again, poll `/readyz`, and fail with either "rolled back" or "rollback did not restore the API; fix on the node by hand" naming the backup path.
7. Only the config changed: poll the counters from localhost for up to 90 s. Failure counter advanced: fail "the API server rejected the new file on reload". Success counter advanced: done. Neither: fail "no reload observed".
8. Post-check from localhost: anonymous `GET https://10.0.1.50:6443/api` answers 401.

`--check` maps to the script's `--dry-run`: play 1 runs in full, play 2 runs its read-only preflight and reports what steps 4 to 8 would do.

## 7. Tests

`tests/node-config/test_install_authn_config.py` is replaced by `tests/ansible/test_k3s_authn.py`, keeping the same eight guarantees, now expressed against the playbook:

| Script test | Playbook equivalent |
|---|---|
| syntax and help | `ansible-playbook --syntax-check` passes |
| config without the anonymous block is refused | play 1 fails on the static guard, no container is started |
| an oidc flag in the drop-in is refused, a comment is not | same |
| shipped files pass the static guards | play 1 static tasks pass on the repo files |
| a file the API server rejects never reaches the node | play 1 fails at the container stage; play 2 never runs (needs Docker) |
| dry run validates then only reads the node | `--check` against a container target records only reads |
| an oidc flag already on the node is refused | play 2 preflight fails against a target whose config sets `oidc-` |
| an instant hot reload is seen | counters are read before install; a target whose counters are already advanced is reported as reloaded |

The "node" for play 2 tests is a Docker container with sshd and a fake `/etc/rancher/k3s`, so the tests never touch 10.0.1.50. Docker-dependent cases keep the `needs_docker` skip.

A new `tests/ansible/test_lint.py` runs `ansible-lint` and `--syntax-check` on every playbook so the CI job and local pytest run the same thing.

## 8. CI, tooling, docs

- `.github/workflows/validate.yaml`: new `ansible-validate` job, gated on `ansible/**` and `tests/ansible/**` changes, that installs `ansible-core` and `ansible-lint`, installs `requirements.yml`, and runs `tests/ansible/`. The `node-config-validate` job is removed.
- Workstation install: `uv tool install ansible-core` plus `ansible-galaxy collection install -r ansible/requirements.yml`. Documented in `ansible/README.md`.
- References updated: `CLAUDE.md`, `index.md`, `SPEC.md`, `docs/plans/k8s-136-features-implementation-plan.md`, `docs/troubleshooting/kubectl-oidc.md`, `base-apps/dex/docs.md`, `base-apps/dex/docs/index.md`, `base-apps/cluster-rbac/oidc-admins.yaml`.
- `recovery/post-restart-restore.sh` and `recovery/CLUSTER-RECOVERY.md`: worker IPs corrected to 10.0.1.5 and 10.0.1.108.

## 9. Rollout

All from the workstation, in this order. Each step is its own verification gate.

1. `bootstrap.yml --ask-become-pass` against the hypervisor. Verify `sudo -n true` succeeds.
2. `site.yml --check --diff`, then `site.yml`. Expect the worker netplan change and small drift only. Hypervisor last via `--limit`. Before the first run on the workers, reserve (or exclude) 10.0.1.5 and 10.0.1.108 in the router's DHCP server: once pinned, the workers stop renewing and the pool could hand those addresses to another device.
3. `k3s-authn.yml`. Expect play 1 to pass; play 2 re-installs both files (only their comments changed in the 2026-09-29 move) and restarts k3s once (about 20 s of API blip); `--check` shows `DRY: would restart k3s` first. Requires colima running.
4. `patch.yml --limit k3s-worker-02`, then the full run.
5. `patch-hypervisor.yml -e confirm=yes` when a full lab restart is acceptable.

## 10. Out of scope for v1

- Porting `recovery/post-restart-restore.sh` and the Velero flows.
- In-cluster execution (an Argo Workflows CronWorkflow). The node being drained cannot be the one running the workflow, so this needs its own design.
- Vault-backed secrets via `community.hashi_vault`. Nothing in v1 needs a secret.
- Hypervisor netplan.
- VM creation and resizing (libvirt Terraform provider).
- Renaming the repo. `kubernetes` is now a misnomer, but Argo CD, the ApplicationSet, and Atlantis reference the name.
