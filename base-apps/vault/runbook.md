---
type: "Kubernetes App Runbook"
title: "Vault — Runbook"
description: "Operational runbook for Vault: seal/KMS failures, vault-0 Pending, ESO auth, backup and restore."
app: vault
catalog_entity: vault
kind: runbook
namespace: vault
last_reviewed: 2026-09-30
status: current
tags: [secrets, stateful, kv-v2]
sources:
  - base-apps/vault/statefulsets.yaml
  - base-apps/vault/services.yaml
  - base-apps/vault/configmaps.yaml
  - base-apps/vault/httproute.yaml
  - base-apps/vault/certificate.yaml
  - terraform/roots/asela-cluster/vault-kms.tf
  - base-apps/dex/configmap.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/atlantis/external-secrets.yaml
  - scripts/vault-backup.sh
  - scripts/vault-restore.sh
  - recovery/CLUSTER-RECOVERY.md
---

# vault — Runbook

## Failure modes
### Symptom: many apps' ExternalSecrets stop syncing at once
- **Check:** `kubectl -n vault get pods` and seal status (`kubectl -n vault exec vault-0 -- vault status`). A sealed or down Vault breaks all ESO syncs. If the pod is `Pending`, see the next entry.
- **Fix:** Vault is configured for AWS KMS auto-unseal (`configmaps.yaml`'s `seal.awskms` stanza, region `us-east-2`), so after a normal restart it unseals itself within seconds and nobody has to enter keys. If it stays sealed, the KMS call is failing: check the `vault-kms-credentials` Secret exists and is valid (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION`/`VAULT_AWSKMS_SEAL_KEY_ID` env vars in `statefulsets.yaml`), and that the pod can reach AWS KMS in `us-east-2`. Only fall back to manual `vault operator unseal` with recovery keys if KMS itself is unrecoverable (see `docs/plans/vault-auto-unseal-implementation-plan.md` for background).

### Symptom: `vault-0` is Pending (e.g. during a drain of k3s-worker-01)
- **Check:** `kubectl -n vault describe pod vault-0` shows `FailedScheduling` (node unschedulable / didn't match node selector / volume node affinity conflict); `kubectl get node k3s-worker-01` shows it cordoned, NotReady or gone.
- **Why:** `vault-0` is pinned to `k3s-worker-01` (`nodeSelector` in `statefulsets.yaml`) and its local-path PV is bound to that node. It can't reschedule elsewhere. This is by design.
- **Fix:** bring worker-01 back (`kubectl uncordon k3s-worker-01` once maintenance is done) and the pod schedules and auto-unseals. Don't delete the PVC or relax the nodeSelector to "unstick" it: a new PVC on another node is an **empty** Vault. Moving Vault to another node is a planned migration (backup, restore into the new volume), not an incident fix. Keep drains of worker-01 short, since every ESO refresh stalls until Vault is back.

### Symptom: Vault pod won't start after being deleted/recreated (e.g. rebuilt cluster)
- **Check:** whether the `vault-kms-credentials` Secret exists in the `vault` namespace (`kubectl -n vault get secret vault-kms-credentials`). Pod events show `CreateContainerConfigError` / `secret "vault-kms-credentials" not found` if not.
- **Fix:** the Secret is Terraform-managed (`kubernetes_secret.vault_kms_credentials` in `terraform/roots/asela-cluster/vault-kms.tf`), not synced by ESO. Terraform state still lists it, so re-applying recreates it. Which way you apply depends on whether Atlantis can still run:
  - **Only this Secret is gone** (Atlantis's own Secrets still exist in the cluster, so it keeps working while Vault is down): open a PR that touches a `.tf` file, let Atlantis plan, apply on the open PR, then merge.
  - **Rebuilt cluster:** Atlantis can't help. It gets its GitHub, webhook and AWS credentials from Vault through ESO (`base-apps/atlantis/external-secrets.yaml`), and Vault can't start without this Secret. This is the one break-glass case for a laptop apply, an exception to the "never `apply` from a laptop" rule. Create the namespace if Argo hasn't yet (`kubectl create namespace vault`; Terraform doesn't create it), point the providers at the new cluster (`TF_VAR_host`, `TF_VAR_client_certificate`, `TF_VAR_client_key`, `TF_VAR_cluster_ca_certificate` from its kubeconfig, plus AWS credentials for the state bucket), then in `terraform/roots/asela-cluster` run `tofu init` and `tofu apply -target=kubernetes_secret.vault_kms_credentials`. Use `tofu` (OpenTofu 1.12.x wrote the state), not `terraform`. Once Atlantis is back, its next plan should show no changes for this resource. See "Rebuild from nothing" in `recovery/CLUSTER-RECOVERY.md`.
  - Prefer either path over hand-creating the Secret, so Terraform stays its only owner.

### Symptom: every SecretStore fails with `permission denied` while Vault is unsealed (after a rebuild or restore)
- **Check:** Vault is up (`vault status` shows `Sealed: false`) but ExternalSecrets in every namespace fail auth. Read Vault's Kubernetes auth config with a token that can: `vault read auth/kubernetes/config`. If it pins a `kubernetes_ca_cert` from the old cluster, Vault can't verify the new cluster's ServiceAccount tokens. A cluster rebuilt without the k3s artifact has a new CA.
- **Fix:** rewrite the config from inside the pod so it picks up the current CA, then make ESO retry. Open a shell with `kubectl -n vault exec -it vault-0 -- sh`, run `vault login` and paste an admin token at the prompt (so it stays out of shell history), then:
  `vault write auth/kubernetes/config kubernetes_host=https://kubernetes.default.svc:443 kubernetes_ca_cert=@/var/run/secrets/kubernetes.io/serviceaccount/ca.crt disable_local_ca_jwt=false`.
  Afterwards run `kubectl -n external-secrets rollout restart deploy`. This config lives only in Vault, not git. The step was needed after the January 2026 cluster restore (commit 13c53ae).

### Symptom: one namespace's ExternalSecrets fail but others work
- **Check:** that namespace's `SecretStore` role vs the Vault Kubernetes-auth role/policy (Vault's auth-delegator access is granted via `cluster_role_bindings.yaml`).
- **Fix:** align the Vault role name with the namespace and confirm the policy grants the `k8s-secrets` KV v2 path.

### Symptom: `vault.arigsela.com` UI unreachable, or OIDC login fails
- **Check:** from an allow-listed source? The host is IP-restricted in `base-apps/istio-ingress/authorizationpolicy.yaml` (403 = not on the list; after a WAN IP rotation, every host breaks at once). Certificate: `kubectl -n vault get certificate vault-arigsela-tls`. OIDC errors at Dex ("unregistered redirect URI") mean the callback URL isn't in the `vault` client's `redirectURIs` in `base-apps/dex/configmap.yaml`.
- **Fix:** allow-list/DNS issues are fixed in `istio-ingress` (see that app's runbook). The Vault `oidc` auth method and its roles live inside Vault, not git, so check them with `vault read auth/oidc/config` / `vault list auth/oidc/role`. For the break-glass path, port-forward (`kubectl -n vault port-forward svc/vault 8200`) and use a token.

## How-to
### Deploy / update
Edit manifests here and PR; Argo CD syncs on merge. The StatefulSet uses `updateStrategy: OnDelete`, so template changes (image, env, resources) only take effect after the pod is manually deleted (`kubectl -n vault delete pod vault-0`). Plan for the brief unseal/reconnect window this causes. CPU/memory can be resized in place without a restart (`resizePolicy: NotRequired`); keep git in step with any live resize.

### Restart safely
Deleting the Vault pod re-seals it momentarily; with AWS KMS configured it should auto-unseal on the new pod within seconds. Verify with `kubectl -n vault exec vault-0 -- vault status` (expect `Sealed: false`, `Seal Type: awskms`) before assuming recovery is complete.

### Back up Vault
Use `scripts/vault-backup.sh` (read its header first). Default is a **cold** copy: it suspends the Argo `vault` app, scales the StatefulSet to 0, tars `/vault/data` through a helper pod on the PVC's node, scales back to 1, checks that Vault unsealed, and writes a `.tar.gz` plus a `.sha256`:

```bash
scripts/vault-backup.sh --dest s3://<bucket>/vault/ --argo-app vault   # or an off-cluster local dir
```

- `--argo-app vault` is required, but it isn't enough on its own. Without it the script refuses, because Argo's selfHeal would rescale Vault in the middle of the copy. With it, the script suspends only the `vault` app, and `master-app` restores that app's auto-sync from git within minutes, so Vault can still be rescaled mid-copy (SPEC §V.33, §B.4). Pause the root first and resume afterwards:

  ```bash
  scripts/argo-sync-window.sh pause     # master-app first, then every PVC-bearing app, vault included
  scripts/vault-backup.sh --dest s3://<bucket>/vault/ --argo-app vault
  scripts/argo-sync-window.sh resume
  ```

  Never run `pause` twice without a `resume` in between. A second `pause` records every app as already suspended, so `resume` would then leave them all suspended.
- It refuses destinations inside cluster storage. `--mode online` needs `--allow-inconsistent` and brands the artifact `INCONSISTENT`.
- The artifact is **ciphertext**. It can only be used while the KMS key `alias/vault-auto-unseal` exists (`vault-kms.tf`, `prevent_destroy = true`). The Shamir recovery keys won't decrypt it.

### Restore Vault
`scripts/vault-restore.sh --artifact <file.tar.gz> --data-dir <dir> --verify-cmd '<cmd>'` checks the checksum, refuses `INCONSISTENT` artifacts unless you pass `--accept-inconsistent`, moves any existing data dir aside, extracts, and then runs the verification command, which must prove that Vault unseals and a known secret reads back. It restores into a **local directory**. It doesn't touch the cluster. Putting the data back into `vault-0`'s PVC is manual: run `scripts/argo-sync-window.sh pause` (it suspends `master-app` first, then every PVC-bearing app including `vault`; pausing only `vault` lets the root app re-enable it mid-restore, SPEC §B.4), scale the StatefulSet to 0, copy the extracted tree into the PVC with a helper pod (same pattern as the backup script), scale to 1, verify `vault status` and a secret read, then `scripts/argo-sync-window.sh resume`. The KMS key and a valid `vault-kms-credentials` Secret must exist first. See `recovery/CLUSTER-RECOVERY.md` for where this fits in a full cluster recovery.
