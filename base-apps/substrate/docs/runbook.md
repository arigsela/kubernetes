---
type: "Kubernetes App Runbook"
title: "Agent Substrate — Runbook"
description: "Operational runbook for Agent Substrate: install order, CA rotation, snapshot store, reboots, and failure modes."
app: substrate
catalog_entity: substrate
kind: runbook
namespace: ate-system
last_reviewed: 2026-10-11
status: draft
tags: [substrate, kagent, gvisor, actors, sandbox]
sources:
  - base-apps/substrate.yaml
  - base-apps/substrate-config.yaml
  - base-apps/substrate/external-secret-ca-pools.yaml
  - base-apps/substrate/ca-expiry-cronjob.yaml
  - base-apps/substrate/rustfs-pvc-placement.yaml
  - base-apps/postgresql/init-substrate-db.yaml
  - scripts/provision-kagent-1-0-vault.sh
  - scripts/provision-docs-checker-pull.sh
  - ansible/playbooks/ate-image-credentials.yml
  - terraform/roots/asela-cluster/docs-checker-pull-iam.tf
  - ansible/playbooks/tasks/patch-node.yml
  - ansible/playbooks/tasks/cpu-flags-preflight.yml
---

# Agent Substrate — Runbook

## Failure modes

### Symptom: ExternalSecrets in ate-system / podcertificate-controller-system show `SecretSyncedError`
- **Check:** `kubectl get externalsecret -n ate-system; kubectl get externalsecret -n podcertificate-controller-system`
  and `kubectl describe externalsecret -n ate-system actor-id-ca-pool`. "permission denied" = role
  `substrate` missing or not bound to `eso-substrate` in that namespace; "secret not found" =
  `k8s-secrets/substrate-cas` or `substrate-db` was never written.
- **Fix:** run `scripts/provision-kagent-1-0-vault.sh` (see How-to: Install), then force a refresh:
  `kubectl annotate externalsecret -n <ns> <name> force-sync=$(date +%s) --overwrite`.

### Symptom: podcertificate-controller or Substrate pods stuck in `ContainerCreating`
- **Check:** `kubectl describe pod -n podcertificate-controller-system -l app=podcertificate-controller`
  (missing `*-ca-pool` Secret), `kubectl describe pod -n ate-system -l app=ate-api-server`
  (missing `actor-id-*` / `substrate-postgres-*` Secret, or a pod certificate not issued:
  `kubectl get podcertificaterequests -A`).
- **Fix:** the Secrets come from ESO (previous symptom). Pod certificates need the
  podcertificate-controller running first; it starts once its three CA pool Secrets exist.

### Symptom: ate-api-server CrashLoopBackOff
- **Check:** `kubectl logs -n ate-system deploy/ate-api-server`. `password authentication failed`
  or `permission denied for schema substrate` = the database was not prepared:
  `kubectl get jobs -n postgresql` and the `postgresql` app's last sync. Authentication-config
  errors = `ate-api-authentication`'s issuer no longer matches
  `kubectl get --raw /.well-known/openid-configuration`.
- **Fix:** sync the `postgresql` app once (its init Jobs are Sync hooks; a change to them alone does
  not trigger auto-sync); fix the issuer in `configmap-ate-api-authentication.yaml`.

### Symptom: every model call from an agent returns 403
- **Check:** `kubectl get cm -n ate-system k8s-credential-provider-namespace-policy -o yaml` must
  list atespace `kagent` with allowedNamespaces `[kagent]`.
- **Fix:** `credentialProvider.namespacePolicies` in `base-apps/substrate.yaml` (the Substrate chart,
  not the kagent chart).

### Symptom: actors RUNNING but unreachable ("No route to host") after a node reboot
- **Check:** `kubectl get pods -n kagent -l ate.dev/worker-pool -o wide`: a worker whose IP changed
  while keeping its name (substrate#1837).
- **Fix:** delete those worker pods; the WorkerPool's Deployment re-creates them.
  `ansible/playbooks/patch.yml` does this after every reboot it performs; an unplanned reboot needs
  it by hand. Affected sessions may need a new turn.

### Symptom: a session fails to resume after moving node
- **Check:** atelet logs on the target node (`kubectl logs -n ate-system -l app=atelet`) for a
  gVisor CPU feature mismatch (substrate#1657), or a resume deadline (substrate#1761).
- **Fix:** compare `/proc/cpuinfo` flags across the k3s VMs (the `patch.yml` pre-flight does);
  a VM with a different CPU model must be fixed at the hypervisor. Start a new session meanwhile.

### Symptom: every new agent session fails with `-32004 KAGENT_SEND_NOT_ACCEPTED`, the controller logs nothing
- **Check:** `kubectl -n ate-system logs deploy/atenet-router --since=10m | grep ResumeActor`. `received
  fatal alert: CertificateExpired` means a Substrate pod certificate ran out: they are issued at pod
  start for 24 h (`kubectl get podcertificaterequests -A` shows `notAfter`); the kubelet refreshes
  them at 23.5 h but the agentgateway in atenet-router/egress never reloads the file
  (agentgateway#3751, substrate#2329; SPEC T114). `cert-restart-cronjob.yaml` restarts both twice
  a day; if this fires anyway, check `kubectl -n ate-system get jobs -l app=substrate-cert-restart`.
  A restarted `kagent-controller` alone does not help.
- **Fix:** `kubectl -n ate-system rollout restart deploy/atenet-router deploy/atenet-egress
  deploy/ate-api-server deploy/ate-controller deploy/k8s-credential-provider ds/atelet` and
  `kubectl -n kagent rollout restart deploy/kagent-default` (the same as a node drain; sessions
  resume from snapshots). Then `kubectl -n kagent get agents` all Ready and one `scripts/ask-hk`.

### Symptom: atelet stuck in `ContainerCreating` on one worker
- **Check:** `kubectl describe pod -n ate-system -l app=atelet --field-selector spec.nodeName=<node>`.
  `hostPath type check failed: /opt/ate-image-credential-provider is not a directory` means the worker
  never got the pull-credentials directory. That happens on a new or rebuilt worker, or when the
  §T.129 values merged before provisioning.
- **Fix:** `scripts/provision-docs-checker-pull.sh --limit <node>` (needs Vault and the AWS CLI; see
  How-to: The docs-checker pull key). The kubelet retries the mount by itself.

### Symptom: atelet CrashLoopBackOff, log `Failed to configure image pull credentials`
- **Check:** `kubectl logs -n ate-system -l app=atelet --previous | grep -i credential`. atelet parses
  `atelet.imageCredentialProviderConfigYAML` strictly at startup, and any error kills it on **every**
  node. An unknown field, `valueFrom` in `env`, an unquoted duration, or a wrong `apiVersion`/`kind`
  all do this.
- **Fix:** revert the values change in `base-apps/substrate.yaml`, or correct it so that
  `python -m pytest tests/substrate/ -q` passes, then merge. Once Argo has synced, run
  `kubectl -n ate-system rollout restart ds/atelet`. An edit to the config alone does not roll the
  DaemonSet, and a subPath mount does not pick up a changed ConfigMap.

### Symptom: an actor using the docs-checker image never starts (pull `401`/`403`/`denied`)
- **Check:** `kubectl logs -n ate-system -l app=atelet --since=30m --prefix | grep -E 'Resolved image credentials|Image credential provider failed'`.
  - `Image credential provider failed`: its stderr follows. `failed to get shared config profile` = the
    credentials file is missing or wrong. `UnrecognizedClientException`/`InvalidClientTokenId` = the key
    was deleted or deactivated. `AccessDenied` = the IAM policy. `no such file` = the binary is missing.
  - No line at all: the image did not match `matchImages`. Only `…/docs-checker*` is covered, and every
    other image is pulled anonymously.
  - `cached=true` and still denied: a token from a retired key, cached for up to 6 h.
- **Fix:** `scripts/provision-docs-checker-pull.sh` re-places what Vault holds and checks it from each
  worker. A deleted key needs `--rotate` (then `--retire-old`). A stale cached token needs
  `kubectl -n ate-system rollout restart ds/atelet`.

### Symptom: Grafana alert "Substrate CA expires in under 30 days"
- **Check:** `kubectl logs -n ate-system -l app.kubernetes.io/name=substrate-ca-expiry --tail=10`
  lists each CA's `not_after`. No data at all = the check stopped:
  `kubectl get cronjob,jobs -n ate-system -l app.kubernetes.io/name=substrate-ca-expiry`.
- **Fix:** How-to: Rotate the Substrate CAs.

### Symptom: the substrate app fails to sync on `rustfs-bucket-init` ("field is immutable")
- **Check:** a chart bump changed the bucket-init Job's pod template; Jobs are immutable and the
  chart renders it as a plain Job.
- **Fix:** `kubectl delete job -n ate-system rustfs-bucket-init`, then sync. It only creates the
  bucket if missing.

### Symptom: rustfs volume on the wrong node
- **Check:** `kubectl get pv -o custom-columns=NAME:.metadata.name,CLAIM:.spec.claimRef.name,NODE:.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values[0] | grep rustfs`.
  It should say k3s-worker-02. If the placement policy lost the race on the first install, it may not.
- **Fix:** the store is disposable (suspended sessions are lost, nothing else). With
  `kubectl get mutatingadmissionpolicy substrate-rustfs-pvc-placement` present:
  `kubectl -n ate-system scale deploy rustfs --replicas=0`, `kubectl -n ate-system delete pvc rustfs-data`,
  then sync the `substrate` app (it re-creates the claim, the policy pins it), and delete the
  `rustfs-bucket-init` Job so the next sync re-creates the bucket.

## How-to

### Install (plan Phase 4)
1. Vault first, from a workstation (needs openssl 3, python3, vault CLI):
   `scripts/provision-kagent-1-0-vault.sh --dry-run`, then with `kubectl port-forward -n vault svc/vault 8200:8200`,
   `VAULT_ADDR=http://127.0.0.1:8200` and an admin `VAULT_TOKEN`: `scripts/provision-kagent-1-0-vault.sh`.
2. Confirm ports 8085 and 9090 are free on the worker nodes (`ss -ltn`).
3. Merge. Then check, in order: `kubectl get crd | grep ate.dev`; ExternalSecrets `SecretSynced` in
   ate-system, podcertificate-controller-system and postgresql; the init Jobs completed (sync
   `postgresql` once if they did not run); `kubectl get clustertrustbundles`;
   `kubectl get pods -n podcertificate-controller-system,ate-system`; atelet Running on both workers;
   the rustfs PV on k3s-worker-02; `kubectl create job -n ate-system --from=cronjob/substrate-ca-expiry ca-check-now`
   and its log line.

### Rotate the Substrate CAs
The five CAs are replaced together (the JWT pool is a bare key with no expiry and is kept).
Pod certificates and actor identities issued by the old CAs stop verifying, so the restart order
matters (substrate#1931):
1. `scripts/provision-kagent-1-0-vault.sh --rotate-cas` (same prerequisites as Install).
2. Force ESO: `for ns in ate-system podcertificate-controller-system; do kubectl annotate externalsecret -n $ns --all force-sync=$(date +%s) --overwrite; done`,
   and confirm the Secrets changed (`kubectl get secret -n ate-system actor-id-ca-pool -o jsonpath='{.metadata.resourceVersion}'`).
3. `kubectl rollout restart -n podcertificate-controller-system deploy/podcertificate-controller`;
   wait until `kubectl get clustertrustbundles` shows new bundles.
4. `kubectl rollout restart -n ate-system deploy/ate-api-server deploy/ate-controller deploy/atenet-router deploy/atenet-egress deploy/k8s-credential-provider ds/atelet`.
5. `kubectl rollout restart -n kagent deploy/kagent-controller` and the worker Deployment
   (`kubectl get deploy -n kagent -l ate.dev/worker-pool`). Suspended sessions may not resume; start new ones.
6. Run the expiry check by hand (Install step 3) and confirm `min_days_left` is ~365.

### Rotate the database passwords
Write a new 64-hex value over the property (hex only: the connection strings do not escape it),
e.g. `openssl rand -hex 32 | tr -d '\n' > /tmp/p && vault kv patch -mount=k8s-secrets substrate-db owner-password=@/tmp/p && rm /tmp/p`
(kagent: `kagent-db` `v1-password`). Force the ExternalSecrets in `postgresql` and the consumer
namespace, sync the `postgresql` app (its init Jobs set the new password), then restart
ate-api-server (Substrate) or kagent-controller (kagent reads its connection string once, at startup).

### The docs-checker pull key (private sandbox images, §T.129)
The key of IAM user `system/docs-checker-pull` lives in Vault `k8s-secrets/docs-checker-pull` and in
`/opt/ate-image-credential-provider/aws-credentials` (root 0600) on both workers, next to the pinned
`ecr-credential-provider`. `scripts/provision-docs-checker-pull.sh` is the only writer. It needs
`VAULT_ADDR` and a Vault login, the AWS CLI as an IAM admin, and `ansible-playbook` with SSH to the
workers (`ansible/README.md`). Every run that places the key also checks it from each worker.
- **First install:** open the PR, then let Atlantis apply the IAM user. Next, run
  `scripts/provision-docs-checker-pull.sh --check`, then the script with no options. Merge only
  after that (atelet needs the directory). The acceptance checks are in
  `docs/superpowers/specs/2026-10-18-substrate-ecr-pulls.md`.
- **New or rebuilt worker:** `scripts/provision-docs-checker-pull.sh --limit <node>`, before atelet
  is scheduled there.
- **Rotate:**
  1. `--rotate`. This adds a second key, stores it, places it and checks it on every worker. The
     old key stays active.
  2. `--retire-old`. This deletes every key except Vault's.
  3. `kubectl -n ate-system rollout restart ds/atelet`. This drops tokens cached from the old key.
  4. `aws iam get-access-key-last-used --access-key-id <new id>` should show `ecr` after the next pull.
- **Bump the provider binary:** in `ansible/playbooks/ate-image-credentials.yml`, set
  `ate_cp_version`, and `ate_cp_sha256` from `<ate_cp_url>.sha256`. Then run the script with no options.

### Check the component image digests
The four Substrate component images run by tag. Compare what the tag resolves to today with the
digests recorded in the header of `base-apps/substrate.yaml`:
`docker buildx imagetools inspect ghcr.io/kagent-dev/substrate/ateapi:v0.5.0-alpha2 --format '{{json .Manifest.Digest}}'`
(likewise atecontroller, atelet, kubernetes-secrets), and what the nodes run:
`kubectl get pods -n ate-system -o jsonpath='{range .items[*]}{.status.containerStatuses[*].imageID}{"\n"}{end}'`.

### Upgrade
Only together with kagent, on the version kagent's release is built against, through the
`kagent-substrate` Renovate group (Dependency Dashboard approval). Treat every alpha bump as a
clean-database migration, re-pin the digests in the values, and re-render with
`helm template --kube-version` before merging.
