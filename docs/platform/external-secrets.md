# External Secrets Operator

**Status:** living. Reviewed 2026-09-30 against `base-apps/external-secrets.yaml` and every `external-secrets.io` object under `base-apps/`.

## What it is
The External Secrets Operator (ESO) turns Vault KV entries into Kubernetes `Secret`s. Almost every app's credentials reach it this way: 30 `SecretStore`s and 33 `ExternalSecret`s across 17 namespaces (repo count, 2026-09-30). There is no `ClusterSecretStore`. ESO is a **Helm-only** Argo CD Application, `base-apps/external-secrets.yaml`, with no `base-apps/external-secrets/` directory. The SecretStores and ExternalSecrets live in each consumer's own directory.

## Architecture & data flow
```
Vault (vault.vault.svc:8200, KV v2 mount k8s-secrets, Kubernetes auth at auth/kubernetes)
  ^  login with a ServiceAccount token -> Vault role -> policy
SecretStore (per namespace; names the Vault role + ServiceAccount)
  ^
ExternalSecret (remoteRef.key/property, refreshInterval) --> Secret (creationPolicy: Owner)
```
- **Chart:** `external-secrets` **2.8.0** from `https://charts.external-secrets.io`, release `external-secrets`, namespace `external-secrets`. `prune` and `selfHeal` are on.
- **Scheduling:** the controller, webhook and cert-controller all use `nodeSelector: node.kubernetes.io/workload: infrastructure` plus the control-plane toleration, which puts them on `k3s-control-01`.
- **API version:** every manifest in `base-apps/` is `external-secrets.io/v1`.
  - The `v1beta1` → `v1` migration (55 files) and the storage migration were done during the six-stage ESO walk, `0.11.0` → `0.16.2` → `0.17.0` → `0.20.4` → `2.8.0` (SPEC §T.6, §T.31–§T.33, 2026-08-03).
  - `v1beta1` is no longer creatable. The `0.20.4` chart reintroduced a `v1beta1` CRD entry with `served: false`, which is benign, and `status.storedVersions` stayed `["v1"]`.
- **Refresh:** 32 of the 33 ExternalSecrets use `refreshInterval: 1h`. `oncall-agent-secrets` uses `15s`.

### Store patterns in use
1. **Per-namespace `vault-backend`** (18 ExternalSecrets use one). The store is named `vault-backend` and authenticates as the namespace's `default` ServiceAccount against a Vault role named after the namespace. Example: `base-apps/cert-manager/secret-store.yaml` uses role `cert-manager`. One exception: `base-apps/ecr-auth/secret-store.yaml` sits in `kube-system` with role and ServiceAccount `ecr-credentials-sync`. Paths are keys relative to the mount, such as `cert-manager/route53`, never `k8s-secrets/data/...`.
2. **Per-consumer, path-scoped stores** for agent and database credentials: `vault-<consumer>` with a dedicated `eso-<consumer>` ServiceAccount and a Vault role that reads only that consumer's path.
   - **In `kagent`**, this pattern is mandatory. The broad store is retired. `scripts/validate-agent-identity.py` enforces it in CI, and admission denies it in-cluster via `base-apps/admission-policies/agent-identity.yaml`: an ExternalSecret in `kagent` may not use `vault-backend` or read the monolithic `kagent` key.
   - **Also used** in `postgresql` (`vault-kagent-db`, `vault-donetick-db`, `vault-homelab-agent-db`, `vault-agent-audit-web-db`, `vault-kagent-audit-ro`), `donetick` and `agent-audit`.
   - **Templates:** `templates/agent-identity/` (README, `serviceaccount.yaml`, `secretstore.yaml`).
3. **PushSecret:** none in git today. The only emitter is the dormant Crossplane `XApplication` composition (`base-apps/crossplane-compositions/composition-application.yaml`). It pushes CNPG and AWS credentials into Vault as `external-secrets.io/v1alpha1` PushSecrets with `deletionPolicy: None`. §T.31 left those at `v1alpha1` because PushSecret is a different CRD.

Vault-side roles and policies are **not in git**. They are written inside Vault by hand or by the per-app scripts (`scripts/provision-homepage-vault.sh`, `scripts/provision-donetick-vault.sh`, `scripts/provision-homelab-agent-vault.sh`).

## Where config lives
- **Chart version, values and sync options:** `base-apps/external-secrets.yaml`.
- **Stores and ExternalSecrets:** `base-apps/<app>/secret-store*.yaml` and `external-secret*.yaml`.
- **Vault server, KV mount and seal:** `base-apps/vault/` (see `base-apps/vault/docs.md`).
- **Upgrade gates:** SPEC §R.1–§R.7, §V.23–§V.26.

## Gotchas & tribal knowledge
- **`ServerSideApply=true` is required on the ESO Application.** The SecretStore and ClusterSecretStore CRDs are about 1.1 MB each. Client-side apply's `last-applied-configuration` annotation reached 255 KB against the 262144-byte limit, and syncs were rejected (`metadata.annotations: Too long`). SSA doesn't write that annotation. Don't remove it. (It's the opposite of `istio-ingress`, where SSA was removed. Neither is a general rule.)
- **What the Application comment says about Kubernetes 1.36:** "2.7 is the newest version with a documented k8s matrix, and it caps at 1.35 - this cluster's version. NO ESO release supports 1.36 yet, so ESO remains a 1.36 blocker (R18) even fully current." **That comment is stale.** SPEC §R.39 (2026-09-24) records ESO `2.8.0` as supporting 1.35–1.36, `docs/plans/k3s-1.36-upgrade-plan.md` lists it as "yes (1.35-1.36)", and the cluster has run 1.36 since 2026-09-24 (§T.21). The same comment's "All 30 SecretStores here use the vault provider" still matches the repo count.
- **Some templates still emit `v1beta1`, which the API no longer accepts:**
  - `secret-store.yaml` and `external-secret.yaml` under `templates/new-app/skeleton-secrets/base-apps/${{ values.name }}/` (the Backstage New App template);
  - `templates/agent-identity/secretstore.yaml`.

  An app scaffolded from them fails to sync. Change `v1beta1` → `v1` when copying (the fields are otherwise identical), and fix the templates.
- **The crossplane-system SecretStore is never applied.** `base-apps/crossplane-system/secret-store.yaml` sits at the root of an umbrella Helm chart, and Helm renders only `templates/`. Nothing in git consumes it either. See `docs/platform/crossplane.md`.
- **The ESO webhook defaults fields that git doesn't spell out** (`conversionStrategy`, `decodingStrategy`, `metadataPolicy`, `deletionPolicy: Retain`, `mergePolicy: Replace`). `base-apps/kagent-secrets.yaml` carries the `ignoreDifferences` for this, with the rationale. Copy that pattern only for these webhook-defaulted paths.
- **`creationPolicy: Owner`** (used throughout) means the target Secret is owned by, and garbage-collected with, its ExternalSecret.
- **When ESO or Vault is down, refresh stops but existing Secrets persist.** That's why ESO could run far out of matrix for weeks without an outage (§V.48). A new ExternalSecret, or a pod needing a Secret that doesn't exist yet, is what breaks.
- **A control-plane k3s hop pauses ESO**, because it runs on `k3s-control-01`. `hop-verify.sh` gates on it (`check_eso`: ≤3 failing, the known pre-existing failures from §T.7).
- **Rotated values reach env-var consumers only after a restart.** ESO updates the Secret within `refreshInterval`, or immediately with `kubectl -n <ns> annotate externalsecret <name> force-sync=$(date +%s) --overwrite`. Pods reading the Secret as env still need `kubectl rollout restart`.

## Runbook
### Symptom: an ExternalSecret shows `SecretSyncedError` / not Ready
- **Check:** `kubectl -n <ns> describe externalsecret <name>` (the event carries the provider error), and `kubectl -n <ns> get secretstore`.
- **Fix, by error:**
  - **"could not get secret data from provider"**, or a missing key/property: the Vault path or property doesn't exist. `remoteRef.key` is relative to `k8s-secrets`; don't add `data/`. The store must say `version: "v2"`. Write the value to Vault. That was the cause of the three long-standing failures (`langflow-ide`, `oncall-crewai`, `whoami-test`, §T.7).
  - **`permission denied` / 403:** the role's Vault policy doesn't grant that path. Scoped stores read only their own path, so an ExternalSecret pointed at another consumer's key through a scoped store fails by design.
  - **Kubernetes-auth errors** (role not found, service account not authorized): the store's `role` or `serviceAccountRef` doesn't match the Vault role's `bound_service_account_names` / `bound_service_account_namespaces`. Check with `vault read auth/kubernetes/role/<role>`, and fix the Vault role or the store.
  - **In `kagent`, the object was rejected at apply time:** that's the agent-identity admission policy. Use a scoped store (`templates/agent-identity/README.md`).

### Symptom: a SecretStore isn't Ready, or many namespaces fail at once
- **Check:** `kubectl get secretstore -A` (look at `STATUS` / `READY`), then `kubectl -n vault exec vault-0 -- vault status`.
- **Fix:** if Vault is sealed, down or `Pending`, follow `base-apps/vault/runbook.md`. Stores recover on their own once Vault answers. If only one store is failing, its role or ServiceAccount is wrong (previous symptom). If every ExternalSecret is stale and Vault is healthy, check ESO itself: `kubectl -n external-secrets get pods`, then the controller logs.

### Symptom: applying a SecretStore/ExternalSecret fails with `no matches for kind ... in version external-secrets.io/v1beta1`
- **Fix:** change the manifest to `external-secrets.io/v1`. `v1beta1` is gone (see Gotchas for where it still hides).

### Upgrading ESO
1. Read the release notes for every major you cross. `1.0.0` documented no breaking changes. `2.0.0` removed only the Alibaba and Device42 providers, and everything here uses `vault`. Check the Kubernetes support matrix for the cluster's minor (SPEC §R.4 / §R.39 record past checks).
2. Bump `targetRevision` in `base-apps/external-secrets.yaml`. One change per PR (§V.8). Keep `ServerSideApply=true`.
3. **If a release removes a CRD version, migrate stored objects first (§V.26, §T.32):**
   - Rewrite every object so it is re-stored at the new version.
   - Then patch `status.storedVersions` by hand. The API server never prunes it on its own.
   - Check all four CRDs, including `clusterexternalsecrets`. §V.26 originally named only three.
   - Git being on the new version doesn't mean etcd is.
4. **After sync:**
   - `kubectl get externalsecret -A` should show the same count SecretSynced as before, and `refreshTime` should keep advancing.
   - Run `scripts/hop-verify.sh gate`'s ESO check, or count by hand.
   - If Argo CD reports `field not declared in schema` on apps with ESO kinds, that's the stale-schema issue in `base-apps/argo-cd/runbook.md`: restart the application controller, after asking.
