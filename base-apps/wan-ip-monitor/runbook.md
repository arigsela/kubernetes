---
type: "Kubernetes App Runbook"
title: "wan-ip-monitor — Runbook"
description: "Operational runbook for wan-ip-monitor: failure modes, checks, and fixes."
app: wan-ip-monitor
catalog_entity: wan-ip-monitor
kind: runbook
namespace: wan-ip-monitor
last_reviewed: 2026-08-12
status: current
tags: [automation, dns, route53, istio, github-actions]
sources:
  - base-apps/wan-ip-monitor/configmap-reconcile.yaml
  - base-apps/wan-ip-monitor/cronjob.yaml
  - base-apps/wan-ip-monitor/external-secret.yaml
  - base-apps/wan-ip-monitor/secret-store.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
---

# wan-ip-monitor — runbook

## Health check
```bash
kubectl -n wan-ip-monitor get cronjob wan-ip-monitor
kubectl -n wan-ip-monitor get jobs --sort-by=.metadata.creationTimestamp
kubectl -n wan-ip-monitor logs -l app=wan-ip-monitor --tail=50
kubectl -n wan-ip-monitor get externalsecret wan-ip-monitor
```
A healthy steady-state run's log is exactly two lines:
```
detected=<ip> declared=<ip> dry_run=<True|False>
in sync, nothing to do
```

## Failure modes

### PR opened but nothing merged
- **Symptom:** one or more of the protected hosts
  (`argocd.arigsela.com`, `coroot.arigsela.com`, etc.) started returning `403`
  after a WAN IP rotation, even though DNS resolves fine.
- **Check:** `gh pr list --repo arigsela/kubernetes --head
  automation/wan-ip-<new-ip>` — an open PR titled `istio-ingress: follow the
  WAN IP rotation <old> -> <new>` is expected here.
- **This is expected state, not a bug.** Route 53 is fixed the moment the job
  detects the rotation (no PR needed for that half), but the Istio allow-list
  only updates once the PR merges. Every host in
  `authorizationpolicy.yaml` resolves correctly and answers `403` in this
  window — until a human reviews and merges the PR, at which point Argo CD
  syncs the new allow-list and access is restored. **Merging the PR is the
  fix; nothing else needs to happen.**

### Job failing on AWS auth
- **Symptom:** job logs show `route53 call failed: ...` (an
  `AccessDenied`/`InvalidClientTokenId`/similar boto3 error), or the
  Route 53 calls never happen because the container fails on missing env vars.
- **Check:**
  ```bash
  kubectl -n wan-ip-monitor get externalsecret wan-ip-monitor -o yaml
  ```
  Look for `status.conditions` reporting `SecretSynced`. If it instead reports
  a sync error, the Vault role or KV path referenced by
  `secret-store.yaml`/`external-secret.yaml` is missing or wrong.
- **Fix:** confirm the Vault prerequisite (Task 0, human/out-of-band) exists:
  a policy and Kubernetes-auth role both named `wan-ip-monitor` bound to
  `bound_service_account_names=default` /
  `bound_service_account_namespaces=wan-ip-monitor`, and a KV v2 secret at
  `k8s-secrets/wan-ip-monitor` with `aws-access-key-id` /
  `aws-secret-access-key` populated. If the AWS key itself has expired or been
  rotated in IAM, update the Vault secret value — the ExternalSecret picks it
  up on its next `refreshInterval` (1h) without a redeploy.

### Job failing on GitHub auth
- **Symptom:** job logs show an `HTTPError` (401/403) from a `github(...)`
  call, typically during `open_allowlist_pr`.
- **Check:** the `GITHUB_TOKEN` value in Vault
  (`k8s-secrets/wan-ip-monitor`, property `github-token`) — GitHub PATs expire
  on a schedule and this token has no auto-rotation.
- **Fix:** re-mint a token scoped to **only** `contents:write` +
  `pull_requests:write` on `arigsela/kubernetes` — no `administration` scope,
  in particular. Note that `contents:write` + `pull_requests:write` is not
  what stops this job from merging its own PR (that pair is exactly what a
  merge call needs); what stops it is the `main` branch ruleset requiring an
  approval the pusher cannot supply, which `administration` could bypass and
  everything else cannot — see docs.md, "Why Route 53 is automatic but the
  allow-list is a PR", for the full explanation. Then
  `vault kv put k8s-secrets/wan-ip-monitor github-token=<new token> ...`
  (carrying forward the existing AWS keys in the same `kv put`, since KV v2
  `put` replaces the whole secret version).

### PyPI unreachable
- **Symptom:** job fails fast with a `pip` error (connection timeout / could
  not find a version) instead of ever reaching `reconcile.py`.
- **Cause:** `cronjob.yaml`'s `command` runs `pip install --quiet
  boto3==1.35.99 pyyaml==6.0.2` at container start rather than baking a custom
  image (see docs.md, "Container image choice") — so this job now depends on
  PyPI being reachable from the cluster on **every single run**, not just at
  deploy time. A PyPI outage, or an egress/DNS problem specific to this
  namespace, fails the run even though nothing about the reconciler's own
  logic is wrong.
- **Fix:** this self-corrects once PyPI is reachable again (next run, 5
  minutes later); no action needed for a transient PyPI blip. If it persists,
  check cluster egress/DNS generally (other CronJobs hitting the internet
  would show the same symptom) before suspecting this job specifically.

### CreateContainerConfigError right after first deploy
- **Symptom:** `kubectl -n wan-ip-monitor get pods` shows
  `CreateContainerConfigError` shortly after this app first syncs.
- **Cause:** the CronJob's `envFrom` references the `wan-ip-monitor` Secret
  that External Secrets Operator creates from the `ExternalSecret`
  (`external-secret.yaml`). If a run fires before that Secret exists yet
  (ExternalSecret's first sync hasn't completed), the pod can't start.
- **Fix:** none needed — this self-corrects within one schedule interval (5
  minutes) once the ExternalSecret finishes its first sync. Confirm with
  `kubectl -n wan-ip-monitor get externalsecret wan-ip-monitor` if it doesn't
  clear within ~10 minutes (see "Job failing on AWS auth" above).

### Repeated PRs for the same address
- **Symptom:** more than one open PR targeting the same rotation (same
  `old -> new` addresses), or a burst of PRs in a short window.
- **Likely cause:** branch deletion racing the job. `open_allowlist_pr` dedups
  by checking for an existing open PR on branch `automation/wan-ip-<new_ip>`
  (`branch_name`) before creating anything; if that branch was deleted (e.g.
  during PR cleanup) while the CronJob still runs every 5 minutes, the next
  run finds no matching branch/PR and opens a new one.
- **Check:** confirm `branch_name(new_ip)` is still deterministic for a given
  IP (`tests/wan_ip/test_logic.py::test_branch_name_is_deterministic_per_address`)
  — if that guarantee ever regresses, dedup breaks silently.
- **Fix:** close/merge the duplicate PRs, and avoid deleting a
  `automation/wan-ip-*` branch until its PR is merged or the rotation is
  confirmed superseded (i.e. the WAN address moved again before this PR
  merged, in which case the branch is stale and safe to delete).

### Detected address flapping
- **Symptom:** the job alternates between two (or more) different `detected=`
  values across consecutive runs, each triggering the full rotation path
  (Route 53 update + PR open) repeatedly.
- **Check:** compare what `https://checkip.amazonaws.com` and
  `https://ifconfig.me/ip` each report right now — `detect_wan_ip` trusts
  whichever source answers first, so if the two sources disagree the answer
  depends on network timing, which is what flapping looks like.
- **Root cause:** this usually means the connection is CGNAT'd or
  double-NAT'd — the "one home, one public IP" premise this whole reconciler
  is built on doesn't hold. There may be more than one visible public address
  depending on which upstream NAT hop happens to serve a given outbound
  request.
- **Fix:** this is an ISP/network-topology problem, not a bug in the
  reconciler. Confirm with the ISP whether CGNAT is in play. There is no code
  fix here — the job would need a fundamentally different detection strategy
  (e.g. a fixed egress) if the network stops presenting a single stable
  public address. In the meantime, consider suspending the job (below) rather
  than letting it thrash the allow-list PR queue.

### Disabling it
Two options, in order of reversibility:
- **Soft disable (keep observing, stop acting):** set `DRY_RUN` to `"true"` on
  the CronJob (`cronjob.yaml`) and let Argo CD sync it. The job keeps running
  every 5 minutes and logging `detected=... declared=... dry_run=True`, but
  never touches Route 53 or opens a PR.
- **Hard disable (stop running entirely):**
  ```bash
  kubectl -n wan-ip-monitor patch cronjob wan-ip-monitor \
    -p '{"spec":{"suspend":true}}'
  ```
  This is a live cluster mutation, not a git change — Argo CD's `selfHeal`
  will **revert it** on the next sync unless `suspend: true` is also committed
  to `cronjob.yaml`. Use the live patch only for an immediate, temporary stop;
  commit the change to git for anything longer than one sync interval.

## How-to

### Deploy / update
GitOps only — never `kubectl apply`. Edit the manifests under
`base-apps/wan-ip-monitor/` (or the script inside
`configmap-reconcile.yaml`), commit, push, and let Argo CD sync. To change the
reconciler logic itself, also update `tests/wan_ip/` and confirm
`python3 -m pytest tests/wan_ip/ -q` passes first — the ConfigMap is the
script's only home, so a change there is a change to production.

### Run a one-off dry-run job
```bash
kubectl -n wan-ip-monitor create job --from=cronjob/wan-ip-monitor manual-check
kubectl -n wan-ip-monitor logs job/manual-check
```

### Arm it (flip DRY_RUN off)
Set `DRY_RUN` to `"false"` in `cronjob.yaml` and push as its own commit,
separate from any other change — arming the automation to make live Route 53
and GitHub PR changes is deliberately a standalone, reviewable diff.

### Rotate/replace credentials
Update the Vault KV secret at `k8s-secrets/wan-ip-monitor` (see "Job failing
on AWS auth" / "Job failing on GitHub auth" above); the ExternalSecret picks
up the new value within its `refreshInterval` (1h) with no redeploy needed.
