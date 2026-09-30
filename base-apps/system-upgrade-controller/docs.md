---
type: "Kubernetes App Guide"
title: "system-upgrade-controller"
description: "Rancher system-upgrade-controller and the k3s-agent Plan: upgrades the two k3s workers one at a time (cordon, no drain); the control plane is upgraded by hand. Home of the k3s upgrade procedure"
app: system-upgrade-controller
catalog_entity: system-upgrade-controller
kind: docs
namespace: system-upgrade
last_reviewed: 2026-09-30
status: current
tags: [k3s, upgrades, lifecycle]
sources:
  - base-apps/system-upgrade-controller.yaml
  - base-apps/system-upgrade-controller/controller.yaml
  - base-apps/system-upgrade-controller/crd.yaml
  - base-apps/system-upgrade-controller/plan-agent.yaml
  - tests/k3s-upgrade/test_suc_plan.py
  - scripts/hop-verify.sh
  - scripts/argo-sync-window.sh
  - scripts/k3s-backup.sh
  - scripts/k3s-restore.sh
  - docs/plans/k3s-1.36-upgrade-plan.md
  - SPEC.md
---

# system-upgrade-controller

## What it is
Rancher's system-upgrade-controller (SUC, `rancher/system-upgrade-controller:v0.20.1`) plus a single `Plan`, `k3s-agent`, which upgrades k3s on the two workers (`k3s-worker-01`, `k3s-worker-02`). The control plane, `k3s-control-01`, is upgraded **by hand**. It is the only server, and it runs on SQLite with no embedded etcd.

This page and the runbook are the living home of the k3s upgrade procedure. The procedure itself, step by step, is in `runbook.md`. `docs/plans/k3s-1.36-upgrade-plan.md` records the last hop (`v1.35.6+k3s1` → `v1.36.4+k3s1`, done 2026-09-24, SPEC §T.21). The invariants behind each step are in `SPEC.md` §V, and the next hop (1.36 → 1.37) is §T.89. This page does not state the running version, because it would go stale. The version is whatever `version:` in `plan-agent.yaml` says, and `kubectl get nodes` confirms it.

## Architecture & data flow
- **Application** `base-apps/system-upgrade-controller.yaml` syncs this directory into namespace `system-upgrade`, with `prune: true`. Unlike the PVC-carrying apps, everything here is disposable controller machinery (§V.19). It ignores `.spec.conversion` on the CRD, a permanent API-server default (see `docs/plans/argocd-drift-diagnosis.md`).
- **Sync waves:** the CRD (`crd.yaml`) is wave -2, the namespace, RBAC, ConfigMap and Deployment (`controller.yaml`) are wave -1, and the Plan is the default wave 0. Otherwise the first sync fails on an unknown kind. `tests/k3s-upgrade/test_suc_plan.py` enforces the order.
- **Controller:** a `Recreate` Deployment pinned to the control-plane node (nodeAffinity `node-role.kubernetes.io/control-plane: Exists` plus control-plane tolerations), running as UID 65534. Its runtime settings are in ConfigMap `default-controller-env`:
  - job active deadline 900 s, backoff limit 99, TTL 900 s after finish;
  - privileged Jobs, Plan polling every 15 min, leader election on;
  - `SYSTEM_UPGRADE_JOB_KUBECTL_IMAGE: rancher/kubectl:v1.30.3`.
- **Plan `k3s-agent`** (`plan-agent.yaml`):
  - `version` is pinned, never a `channel` (§V.3);
  - `concurrency: 1` and `cordon: true`, with `drain` **absent**;
  - `serviceAccountName: system-upgrade` and `upgrade.image: rancher/k3s-upgrade`;
  - `nodeSelector` requires `k3s-upgrade In ["true"]` **and** `node-role.kubernetes.io/control-plane DoesNotExist`.
- **What a worker hop looks like:** a merged `version` bump syncs the Plan. SUC picks one eligible node and runs a privileged Job on it: it cordons the node, replaces the k3s binary, restarts the agent, and uncordons when the Job completes (§T.16). On the 1.36 hop each worker took about 75–80 s. SUC chose worker-02 first. Vault, CNPG and the pgvector Postgres did **not** restart (§T.21), because pods survive a k3s agent restart: the containerd shims outlive it (§T.18).
- **Node labels** are cluster state, not git. Both workers carry `k3s-upgrade=true`. `k3s-control-01` must never carry it (§V.17, §T.15). The Plan's own `control-plane DoesNotExist` term is the backstop.

## Why the control plane is manual
SUC runs an upgrade as a privileged Job that the API server schedules. On a single-server cluster that Job would restart the API server that owns it. The controller itself also runs on the control plane. So §V.17 forbids pointing SUC at `k3s-control-01`, and `plan-server.yaml` must not exist (the test enforces this). The control-plane hop is a console session on the node running the k3s installer (runbook step 3).

The installer **regenerates** `/etc/systemd/system/k3s.service` from `INSTALL_K3S_EXEC` and ignores the old `ExecStart`, so pass the server flags every time. It leaves `/etc/rancher/k3s/` alone. That directory holds `config.yaml` (`disable: traefik`) and `config.yaml.d/10-authn.yaml`, the structured-authentication drop-in installed by `ansible/playbooks/k3s-authn.yml` (§V.81).

## Where config lives
- **Target worker version:** `plan-agent.yaml` `version:`.
- **Controller, RBAC and env:** `controller.yaml`. **CRD:** `crd.yaml`. Both are the upstream v0.20.1 release manifests plus sync-wave annotations (§T.14).
- **Guards:** `tests/k3s-upgrade/test_suc_plan.py` enforces:
  - no `drain`;
  - every Plan excludes the control plane;
  - no `plan-server.yaml`;
  - `version` pinned, not `channel`;
  - `concurrency: 1`;
  - the sync-wave order.
- **Hop tooling:**
  - `scripts/hop-verify.sh` (`gate` / `watch`);
  - `scripts/argo-sync-window.sh` (pause / resume auto-sync);
  - `scripts/k3s-backup.sh` / `scripts/k3s-restore.sh`, plus `scripts/pg-backup.sh` and `scripts/vault-backup.sh`.

  `tests/k3s-upgrade/` drills the backup and restore scripts against real k3s, Postgres and Vault in Docker. CI runs it as job `k3s-upgrade-scripts`.
- **Pins that must track the cluster version** (§T.89): `K3S_IMAGE` in `tests/admission-policies/conftest.py`, `k3s_image` in `ansible/playbooks/k3s-authn.yml`, and `TARGETS` in `tests/k3s-upgrade/test_api_scan.py` (the removed-API scan).
- **Rules:** `SPEC.md` §C and §V, chiefly:
  - V.3: one minor at a time.
  - V.4: control plane before workers.
  - V.5: post-hop gate.
  - V.9: ≤15 min window per control-plane hop.
  - V.15 / V.16: quiesced backup, proven restore.
  - V.17: SUC scope is agents only.
  - V.18: auto-sync paused for PVC apps.
  - V.50 / V.51: istio-cni and IPAM.

## Gotchas & tribal knowledge
- **`drain` is intentionally unset.** `local-path` is the only StorageClass and its PVs carry hard node affinity, so a drained pod with a volume can't reschedule. It would sit `Pending` until the node returns (§C, §V.42). Per the CRD, an absent `drain` means no drain. Adding `drain: {}` looks harmless, and it would strand Vault, Postgres and every other stateful pod. The test fails if you try.
- **The Plan doesn't finish the job (§V.50).** A k3s restart extracts into a new `/var/lib/rancher/k3s/data/<hash>/` and repoints `data/current`. The running istio-cni pod resolved its hostPath when it was created, so it keeps reinstalling into the old directory. Then containerd fails every **new** pod sandbox on the node, CoreDNS included. That takes cluster DNS down and cascades, which is how the July control-plane hop took 27 min (§B.7). Existing pods keep running and the DaemonSet reads Ready throughout, so it's easy to miss.
  - §T.46 moved `cniBinDir` to the stable `data/cni` directory. That was proven clean on all three nodes on the 1.36 hop.
  - The per-node istio-cni restart is still required until §V.50 is amended (§T.86 proposes it).
  - The ambient dataplane is kept with zero enrolled namespaces (§T.84), so the risk stays on the hop path.
- **Sandbox failures leak IPs (§V.51).** flannel assigns the address before the istio-cni step fails, so every failed attempt leaks a host-local reservation. 18 min of retries once leaked 219 of 254 addresses, and control-01 couldn't create any pod for 14 h (§B.8). After any sandbox-failure incident, audit the node (runbook).
- **Merging the Plan bump starts the worker hop.** `argo-sync-window.sh` pauses only `master-app` and PVC-bearing apps. This app has no PVC, so it keeps syncing during the window, and the worker hop depends on that. Don't merge the bump until the control-plane hop has passed its gate.
- **The controller is down during the control-plane hop** (it runs there). That's harmless, since the Plan bump comes after.
- **The datastore is SQLite** (no `--cluster-init`), so there is no `k3s etcd-snapshot`. Each minor migrates the schema one way and k3s doesn't support downgrade, so the `k3s-backup.sh` artifact is the only rollback (§C, §V.1). Kine runs SQLite in WAL mode: never archive a live `state.db` without its `-wal`/`-shm` siblings (§B.1). The script handles it.
- **`k3s-backup.sh --mode cold` stops k3s and never starts it again.** The API stays down until you `systemctl start k3s` or run the installer.
- **`k3s-restore.sh` moves the entire `/var/lib/rancher/k3s` aside**, not just `server/`. The artifact contains only `server/`. `agent/` (containerd state) and `storage/` (control-01's `local-path` volumes, e.g. Atlantis's PVC) end up in `/var/lib/rancher/k3s.pre-restore.<timestamp>/`, and must be moved back before those pods start. The CI drills run with `--skip-service` in Docker and don't exercise this path. The script also re-runs the installer without `INSTALL_K3S_EXEC` unless you export it.
- **Admission after an API-server restart.** Admission is native ValidatingAdmissionPolicy / MutatingAdmissionPolicy, and Kyverno only reports (§T.80). Parameterised policies with `failurePolicy: Fail` deny for a few seconds after every API-server restart, until the param informer syncs (§V.78). That's expected. `hop-verify.sh` rides it out, and Argo retries.
- **The next hop is the first with structured authn** (kubectl login via Dex, §T.92, 2026-09-27). Validate the authn file against the target k3s version before the hop, and prove OIDC login and anonymous → 401 after it (§V.79, runbook).
- **SUC's kubectl image is `rancher/kubectl:v1.30.3`**, far outside kubectl's ±1 skew. It worked against the 1.36 API on §T.21. If a cordon step fails after a future hop, suspect it.
