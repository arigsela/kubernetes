---
type: "Kubernetes App Runbook"
title: "system-upgrade-controller — Runbook"
description: "The k3s upgrade procedure (backup, sync window, manual control-plane hop, Plan bump, per-node istio-cni restart, verify, resume) and SUC failure modes: stuck pods, idle Plan, failing Job, rollback."
app: system-upgrade-controller
catalog_entity: system-upgrade-controller
kind: runbook
namespace: system-upgrade
last_reviewed: 2026-09-30
status: current
tags: [k3s, upgrades, lifecycle]
sources:
  - base-apps/system-upgrade-controller/plan-agent.yaml
  - base-apps/system-upgrade-controller/controller.yaml
  - scripts/hop-verify.sh
  - scripts/argo-sync-window.sh
  - scripts/k3s-backup.sh
  - scripts/k3s-restore.sh
  - scripts/pg-backup.sh
  - scripts/vault-backup.sh
  - tests/k3s-upgrade/test_api_scan.py
  - ansible/playbooks/k3s-authn.yml
  - docs/plans/k3s-1.36-upgrade-plan.md
  - recovery/CLUSTER-RECOVERY.md
  - SPEC.md
---

# system-upgrade-controller — Runbook

The k3s hop procedure, then SUC's failure modes. `<target>` below is a k3s release such as `v1.37.x+k3s1`. `docs/plans/k3s-1.36-upgrade-plan.md` is the worked example of the last hop, with timings.

## How-to: the next k3s hop (exactly one minor)

### 0. Preconditions: is this hop allowed yet?
- **The target is on the k3s `stable` channel, and it is one minor up** (§V.3; minors can't be skipped). For 1.36 → 1.37, the gates are listed in §T.89.
- **Every platform component states support for the target minor**, or you have explicitly accepted the risk, as with Kyverno on 1.36 (§V.2, §T.72). Record the matrix check in `SPEC.md` §R. Known blocker: CNPG 1.31 drops in-tree `barmanObjectStore`, so §T.79 (the Barman Cloud Plugin) comes before any CNPG 1.31 bump.
- **Removed APIs:** add the target to `TARGETS` in `tests/k3s-upgrade/test_api_scan.py` and run it. pluto only knows built-in APIs, so also check CRD version removals by hand. That blind spot was the ESO `v1beta1` trap (§R.3, §V.26).
- **Bump the k3s pins to the target and validate the authn config on it before the hop:**
  - `K3S_IMAGE` in `tests/admission-policies/conftest.py`;
  - `k3s_image` in `ansible/playbooks/k3s-authn.yml`.

  Then run `cd ansible && ansible-playbook playbooks/k3s-authn.yml --check`. It boots a throwaway k3s of that image, which must load the config and answer anonymous with 401.
- **Announce the window, and have console access to `k3s-control-01`.** The control-plane hop takes `kubectl` and Argo away. Have diagnostics in hand first.
- **Who is affected** (placement at the last hop; re-check with `kubectl get pods -A -o wide --field-selector spec.nodeName=<node>`):
  - `k3s-control-01` (manual): API, Argo, `kubectl`, and Atlantis (its PVC lives there). Running apps keep serving.
  - `k3s-worker-01`: Vault, CNPG (donetick), the pgvector `postgresql` (n8n, kagent, homelab-agent, agent-audit), Prometheus, Coroot, Ollama, oncall-agent.
  - `k3s-worker-02`: n8n, Grafana, Jupyter, qwen, Coroot ClickHouse.

### 1. Back up (§V.1, §V.6, §V.28, §V.21)
All three artifacts must be off-cluster, checksummed and less than 24 h old. The gate reads them from `~/k3s-upgrade-artifacts` on the workstation (override with `--artifacts DIR`).

```bash
# workstation
scripts/pg-backup.sh    --dest ~/k3s-upgrade-artifacts --all
scripts/argo-sync-window.sh pause    # the Vault backup needs master-app suspended too (§V.33, §B.4)
scripts/vault-backup.sh --dest ~/k3s-upgrade-artifacts --argo-app vault   # cold: Vault is scaled to 0 for the copy
scripts/argo-sync-window.sh resume   # close this window before the gate; step 2 opens the hop's own
# on k3s-control-01
scripts/k3s-backup.sh   --mode cold --dest ~/k3s-upgrade-artifacts
```

- **Why the Vault backup has its own pause/resume:** `--argo-app vault` suspends only the `vault` app, and `master-app` restores that app's auto-sync from git within minutes, so Argo could rescale Vault mid-copy and the script would still mark the artifact consistent. The 1.36 plan's order skipped this. Resume before step 2: a second `pause` while a window is open records every app as already suspended, and the hop's final `resume` would then leave them all suspended.
- **`--mode cold` stops k3s and does not start it again.** Either `sudo systemctl start k3s` before the gate, or take the cold artifact as the first action of step 3, right before the installer, which starts k3s at the new version. The 1.36 hop did the latter: k3s stopped at 20:05:36, artifact stamped 20:05:40Z (§T.21). The gate still wants a k3s artifact less than 24 h old, and `--mode online` is valid for that.
- **Copy the k3s artifact and its `.sha256` off the node:** to the workstation's `~/k3s-upgrade-artifacts` and to S3 (`s3://mysql-backups-asela-cluster/k3s/`, §I). The 1.36 hop kept node, laptop and S3 copies.

### 2. Gate, then pause auto-sync
```bash
scripts/hop-verify.sh gate           # non-zero = do not hop
scripts/argo-sync-window.sh scope    # what would be paused; changes nothing
scripts/argo-sync-window.sh pause    # master-app first, then every PVC-bearing app
```
- **What the gate checks:** the artifacts, nodes, Argo drift (every app must be Synced+Healthy, §V.5/§V.47), CNPG, Vault, ESO, istio-cni/ztunnel, live CNI and IPAM failures, the ingress Gateway, and the admission path.
- **If the run ends without a `GATE PASSED` or `GATE FAILED` line, treat it as failed.** That silent abort happened once (§B.14).
- **Pause state is recorded in `~/.k3s-hop-argo-state.json`.** This app has no PVC, so it keeps syncing, and step 4 depends on that.

### 3. Control plane, by hand (§V.4, §V.17)
```bash
# console on k3s-control-01
date +%s                                   # epoch for `watch --since`
sudo cp /etc/systemd/system/k3s.service ~/k3s.service.pre-hop
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=<target> \
  INSTALL_K3S_EXEC="server --write-kubeconfig-mode=644" sh -
diff ~/k3s.service.pre-hop /etc/systemd/system/k3s.service   # expect no change
```
The installer regenerates the unit from `INSTALL_K3S_EXEC`, so always pass it. `/etc/rancher/k3s/` (`config.yaml` with `disable: traefik`, `config.yaml.d/10-authn.yaml`) is left untouched. The moment the API answers, do **step 5 on `k3s-control-01`**, then step 6.

After the control-plane hop, the gate may fail **only** on `master-app` and the apps you paused. That's the open §T.87, and operator judgement waved it through on 2026-09-24. Any other failure means stop.

### 4. Workers: bump the Plan
Open a one-line PR: `version:` in `base-apps/system-upgrade-controller/plan-agent.yaml` → `<target>`. Merge it only after step 3's gate. Argo syncs it despite the pause, and SUC hops one worker at a time (its choice of order).
```bash
kubectl -n system-upgrade get plans,jobs -w
kubectl get nodes -w          # VERSION flips per node; SchedulingDisabled while it hops
```
On **each** worker as its version flips: step 5, then step 6.

### 5. The moment k3s is back on a node, before anything else (§V.50)
```bash
# on the node: evidence for §T.46 (-L follows the link; it should be a real ~55MB binary)
sudo ls -lL /var/lib/rancher/k3s/data/cni/istio-cni
# the remedy, whatever the evidence said
kubectl delete pod -n istio-system -l k8s-app=istio-cni-node --field-selector spec.nodeName=<node>
```
- Don't verify by exec'ing into the istio-cni pod, and don't trust DaemonSet readiness. Both read healthy through the whole §B.7 outage.
- Record the `ls` result per node in the SPEC task for this hop. If it resolves on all three nodes again, that feeds §T.86, the proposal to retire the restart.
- **If any sandbox creation failed on the node** (`FailedCreatePodSandBox`, `failed to find plugin`), audit its IPAM (§V.51):
  ```bash
  sudo ls /var/lib/cni/networks/cbr0/ | grep -c '^10\.'    # compare with pods actually on the node
  ```
  1. Tar the directory first.
  2. Keep a file if its IP is a live pod IP **or** its container id appears in `sudo k3s crictl pods -q` / `sudo k3s crictl ps -aq`. Delete only what matches neither.
  3. Never wipe the directory: live pods hold reservations there.
  4. If the classifier says nothing is live, distrust the classifier. Under `sudo sh -c`, `k3s` isn't on `PATH`, and that once nearly deleted 14 live reservations (§B.8).

### 6. After each hop
```bash
scripts/hop-verify.sh watch --since <epoch k3s stopped>   # §V.9 window; control plane ≤ 15 min
scripts/hop-verify.sh gate
```
Every node must be `Ready`, every app Synced+Healthy, Vault unsealed and CNPG healthy before the next hop starts. For a worker, use the epoch when its Job started.

### 7. After the last hop
1. **Before resuming, confirm each paused app's `status.sync.revision` equals the merged commit** (§B.6, §V.45). Then `scripts/argo-sync-window.sh resume`, which restores exactly the recorded policy, children first and `master-app` last.
2. `scripts/hop-verify.sh gate` should be green apart from artifact age.
3. CNPG archives a **new** WAL segment after the restart (`pg_stat_archiver.last_archived_time` later than the hop), donetick answers, and `kubectl get certificates -A` is all Ready.
4. Authn still holds: `kubectl --context homelab-oidc auth whoami` returns `oidc:arigsela`, and `curl -sk -o /dev/null -w '%{http_code}\n' https://10.0.1.50:6443/api` returns `401` (§V.79, `docs/troubleshooting/kubectl-oidc.md`).
5. Record the hop in `SPEC.md` via the spec skill: the §T entry, each §V.9 window, and the §T.46 evidence per node.

## Failure modes
### Symptom: new pods on a node stuck `ContainerCreating` after a hop (existing pods fine)
- **Check:** `kubectl get events -A | grep -i 'failed to find plugin\|FailedCreatePodSandBox\|no IP addresses available'`, then `kubectl get pods -A -o wide | grep ContainerCreating` to find the node. `hop-verify.sh gate` flags both of these (`§V.50`, `§V.51`). If CoreDNS is among the stuck pods, expect cascading failures: cluster DNS, then Argo's repo-server, then every app going `Unknown` (§B.7).
- **Fix:** restart the `istio-cni-node` pod on that node (step 5), then run the IPAM audit (step 5). If the node reports `no IP addresses available`, the pod CIDR is exhausted by leaked reservations. Prune as described, never wipe.

### Symptom: after the Plan bump, nothing happens (no Job, node versions unchanged)
- **Check:**
  - Argo synced it: `kubectl -n argo-cd get app system-upgrade-controller` shows Synced at the merged revision.
  - The Plan and its status: `kubectl -n system-upgrade get plan k3s-agent -o yaml`.
  - The controller is running: `kubectl -n system-upgrade get pods`, then `kubectl -n system-upgrade logs deploy/system-upgrade-controller`.
  - Node labels: `kubectl get nodes -L k3s-upgrade`.
- **Fix:**
  - **A worker isn't labelled:** `kubectl label node <worker> k3s-upgrade=true`. This is cluster state, not git, and it has been done by hand before (§T.15, §T.18). **Never label `k3s-control-01`.** The Plan's `control-plane DoesNotExist` term would refuse it anyway, and that refusal is a safety net, not permission.
  - **A node already at `<target>` gets no Job.** That's expected.
  - **The controller pod is `Pending`:** it must schedule on the control plane (nodeAffinity plus tolerations in `controller.yaml`).

### Symptom: the upgrade Job fails or loops
- **Check:** `kubectl -n system-upgrade get jobs,pods -o wide`, `kubectl -n system-upgrade logs job/<job>` (all containers), and `kubectl -n system-upgrade describe pod <pod>`. Limits come from `default-controller-env`: 900 s active deadline, backoff limit 99, Jobs kept 900 s after finishing.
- **Likely causes:**
  - **The upgrade image tag doesn't exist.** `rancher/k3s-upgrade:<target with + as ->` fails with `ErrImagePull` / `ImagePullBackOff`. Check that `<target>` is a real k3s release.
  - **The cordon step fails.** The kubectl image is the old `rancher/kubectl:v1.30.3`.
  - **The privileged pod is refused.** The namespace carries `pod-security.kubernetes.io/enforce: privileged`, and it needs to keep it.
- **Also:** uncordon happens only when the Job completes (§T.16), so a failed Job leaves the node `SchedulingDisabled`.
- **Fix:** correct the Plan in git, or revert the bump, and let SUC retry. Once the node is healthy and at a consistent version, `kubectl uncordon <node>` if it's still cordoned. A worker one minor behind the server is within skew, so a stuck worker hop isn't an emergency.

### Symptom: the control-plane hop left the API broken. Roll back.
- **Check:** `sudo systemctl status k3s` and `sudo journalctl -u k3s -e` on the node, then `k3s kubectl version`. Roll back only if the new version can't be made healthy. The datastore migrated one way, so the backup is the only way back (§C).
- **Fix:** restore the step-1 artifact, following `recovery/CLUSTER-RECOVERY.md` for the surrounding recovery:
  ```bash
  # on k3s-control-01, as root
  export INSTALL_K3S_VERSION=<previous version> INSTALL_K3S_EXEC="server --write-kubeconfig-mode=644"
  scripts/k3s-restore.sh --artifact <k3s-backup-...tar.gz> --expect-version <previous version>
  ```
  - **What the script does:** it checks the sha256, moves the **whole** `/var/lib/rancher/k3s` aside to `/var/lib/rancher/k3s.pre-restore.<ts>`, extracts `server/`, promotes an online snapshot if present, reinstalls and starts the pinned version, and refuses to report success unless the API answers at `--expect-version`.
  - **Move `storage/` back from the `.pre-restore.<ts>` directory before those pods start.** `storage/` holds control-01's `local-path` volumes, and the artifact doesn't contain it.
  - **Export `INSTALL_K3S_EXEC` yourself.** The script doesn't pass it, and without it the regenerated unit loses `--write-kubeconfig-mode=644`.
  - **The script restores only the control plane.** Workers carry no datastore. Roll back before the Plan bump (step 4). Rolling the server back after workers have moved puts kubelets ahead of the API server, and the repo has no tested procedure for that.
  - **Data-layer rollbacks are separate:** the `pg_dumpall` and Vault artifacts (see `base-apps/vault/runbook.md` for the Vault restore), plus CNPG barman in S3.

### Symptom: write requests denied for a few seconds right after an API-server restart
- **Check:** errors mention `paramKind ... not yet synced`.
- **Fix:** none needed. Parameterised admission policies with `failurePolicy: Fail` deny until the API server's param informer syncs (§V.78). Argo retries, and the gate's admission check waits for it.

## How-to
### Upgrade the controller itself
Replace `controller.yaml` and `crd.yaml` with the new upstream release manifests, then re-add:
- the `argocd.argoproj.io/sync-wave` annotations (CRD `-2`, everything in `controller.yaml` `-1`);
- the namespace's `pod-security.kubernetes.io/enforce: privileged` label.

Keep `drain` absent and `concurrency: 1` in `plan-agent.yaml`. Run `python -m pytest tests/k3s-upgrade/test_suc_plan.py -q`. Don't do this in the middle of a hop.

### Take a worker out of the automated hop
`kubectl label node <worker> k3s-upgrade-` removes it from the Plan's selector. Add the label back before the next hop, or that node stays behind.
