# IDP Migration Guide — Onboarding Existing Apps via Backstage + Crossplane

**Status:** v1.5 as-shipped (2026-05-12)
**Audience:** Operators onboarding existing `base-apps/<name>` apps into the IDP
**Scope:** Decision framework + step-by-step migration + worked example using `chores-tracker-backend`

---

## TL;DR — The 5-step migration

For an app that **fits** the IDP shape (see §1 to check fit):

1. **Pre-flight:** Verify `crossplane` CLI + `yq` installed; check `kubectl get composition application` is live; note the app's existing image tag, host, port, and any DB/S3 needs.
2. **Backstage form:** Open Backstage → "Application (Crossplane)" template → fill name, image, host, port, `dbNeeded`, `s3Needed`, etc. Submit.
3. **Merge the scaffolder PR.** ArgoCD picks up the new XR within ~30s.
4. **Verify Vault role exists** (critical — `vault:setup` action is currently unreliable; see §6.1). If missing, create it manually via the snippet in §5.
5. **Decommission the old `base-apps/<name>`** via Backstage "Decommission Application" template — opens a teardown PR that deletes the old manifests. Merge it after verifying the new IDP-managed app is healthy.

If the app **doesn't fit** (see §1): keep it on the v1.0 base-apps pattern. The IDP is opt-in.

---

## 1. Decision framework — does this app belong in the IDP?

The v1.x IDP emits a fixed resource shape per XR. An app fits cleanly when ALL of these are true:

| Requirement | What IDP emits | Notes |
|---|---|---|
| **Workload** | Single Deployment with one container | Multi-container pods + StatefulSets not yet supported |
| **Service** | ClusterIP Service on a single port | Multi-port services not yet supported |
| **Ingress** | Standard `networking.k8s.io/v1` Ingress with nginx + cert-manager | **Istio VirtualService NOT emitted** — apps using Istio gateways need v1.6+ |
| **Database** | Optional CNPG Postgres cluster (`dbNeeded:true`) | RDS via Crossplane provider NOT yet via XR spec — would need raw manifests in scaffolder |
| **S3 bucket** | Optional dedicated bucket + IAM user + access key (`s3Needed:true`) | Per-app dedicated IAM (not shared); credentials in Vault |
| **Config** | Env vars from the synced Secret (via ESO ExternalSecret) | **ConfigMaps NOT emitted** — env config goes via secrets only |
| **Naming** | Namespace name MUST equal app name | Existing apps where namespace ≠ name need a manual reconciliation |
| **Image registry** | Public Docker Hub or your ECR (`852893458518.dkr.ecr.us-east-2.amazonaws.com`) | ECR pulls require `ecr-auth` imagePullSecret (already cluster-wide) |
| **Canary deploys** | NOT supported | Apps using Istio primary/canary patterns need v1.6+ |

### Decision matrix

| If the app uses... | Action |
|---|---|
| Standard Ingress + maybe DB + maybe S3 + secrets via Vault | ✅ Migrate now |
| Istio VirtualService | ⏸️ Wait for v1.6+ or hand-craft Istio resources alongside |
| StatefulSet / multi-container Pod | ⏸️ Wait for v1.6+ (`replicas` is the only multi-pod knob today) |
| Separate ConfigMaps (not just Secret env) | ⚠️ Migrate Deployment-side, keep ConfigMap separately (see §6.4) |
| RDS / external DB (not CNPG) | ⚠️ Migrate Deployment + secrets; keep RDS manifests separately (see §6.5) |
| Canary deployment infrastructure | ⏸️ Wait for v1.6+ |
| No DB, no S3, just a container + secret | ✅ Migrate now (simplest case) |

**Bias toward "wait" when in doubt.** The IDP shape is intentionally narrow at v1.5 — fighting it costs more than maintaining the v1.0 base-apps pattern for atypical apps.

---

## 2. Architecture overview (read once, then skim)

```
Backstage (UI form)
   │
   │  scaffolder template: arigsela/backstage:examples/templates/application/template.yaml
   │  invokes scaffolder actions: aws:ecr:create, vault:setup, publish:github:pull-request
   │
   ▼
GitHub PR opens against arigsela/kubernetes with 2-3 new files
   │
   │  base-apps/<name>.yaml                    — ArgoCD Application (master-app picks this up)
   │  base-apps/<name>/application-xr.yaml     — XApplication XR (Crossplane reconciles this)
   │  base-apps/<name>/aws-resources.yaml      — cluster-scoped AWS resources (only if s3Needed:true)
   │
   ▼
User merges PR
   │
   ▼
ArgoCD master-app reconciles → creates the per-app Application
   │
   ▼
Per-app Application syncs base-apps/<name>/ → applies XR + AWS resources
   │
   ▼
Crossplane Composition (base-apps/crossplane-compositions/composition-application.yaml)
   │   function-python reads the XR spec and emits:
   │     - Deployment (image, port, replicas, healthCheckPath, envFrom secret)
   │     - Service (ClusterIP, port → port)
   │     - Ingress (host, TLS via cert-manager letsencrypt-prod)
   │     - SecretStore (namespace = vault role name)
   │     - PushSecret (db) + ExternalSecret (db)        IF dbNeeded:true
   │     - PushSecret (aws-creds) + ExternalSecret (aws-creds)  IF s3Needed:true
   │     - CNPG Cluster + storage                       IF dbNeeded:true
   │   AWS cluster-scoped resources (only if s3Needed:true):
   │     - Bucket + BucketPublicAccessBlock + BucketVersioning + BucketLifecycleConfiguration + BucketCorsConfiguration
   │     - IAM User + Policy + UserPolicyAttachment + AccessKey
   │
   ▼
TeraSky kubernetes-ingestor sees the annotations on emitted resources
   │   → auto-registers the app + all children in the Backstage catalog
   ▼
The app appears in Backstage under "Crossplane Claims" with a working entity page
```

The **teardown flow** reverses this: user submits Backstage "Decommission" template → custom action opens a teardown PR via Octokit → user merges → ArgoCD finalizer cascade-deletes everything → operator runs documented `vault kv delete` commands → operator runs `kubectl delete namespace <name>`.

### Where everything lives

| Repo | Path | Purpose |
|---|---|---|
| `arigsela/kubernetes` | `base-apps/<name>.yaml` | ArgoCD Application for the app (scaffolder writes this) |
| `arigsela/kubernetes` | `base-apps/<name>/application-xr.yaml` | The XApplication XR (scaffolder writes this) |
| `arigsela/kubernetes` | `base-apps/<name>/aws-resources.yaml` | Cluster-scoped AWS (only if s3Needed) |
| `arigsela/kubernetes` | `base-apps/crossplane-compositions/composition-application.yaml` | Composition Python — single source of truth for what XApplication emits |
| `arigsela/kubernetes` | `base-apps/crossplane-compositions/xrd-application.yaml` | XRD — defines the XApplication API |
| `arigsela/kubernetes` | `tests/composition/` | Render-test harness + goldens |
| `arigsela/backstage` | `examples/templates/application/template.yaml` | Scaffolder template (the form) |
| `arigsela/backstage` | `examples/templates/decommission/template.yaml` | Decommission template |
| `arigsela/backstage` | `packages/backend/src/modules/scaffolder/` | Custom scaffolder actions (ecr-create, vault-setup, decommission-pr) |

---

## 3. The XApplication XR spec — what every field means

```yaml
apiVersion: platform.arigsela.com/v1alpha1
kind: XApplication
metadata:
  name: <app-name>            # MUST match namespace name
  namespace: <app-name>       # Auto-created with CreateNamespace=true
  annotations:                # TeraSky catalog metadata — flows to all composed children
    terasky.backstage.io/add-to-catalog: "true"
    terasky.backstage.io/owner: group:default/platform-engineering
    terasky.backstage.io/component-type: service
    terasky.backstage.io/lifecycle: experimental   # or production
    terasky.backstage.io/system: platform
    backstage.io/source-location: url:https://github.com/arigsela/kubernetes/tree/main/base-apps/<name>
spec:
  # Workload
  image: <full-image-ref>     # e.g., 852893458518.dkr.ecr.us-east-2.amazonaws.com/myapp:1.2.0
  host: <fqdn>                # Public hostname for the Ingress (cert-manager auto-provisions TLS)
  port: 8080                  # Container port + Service targetPort
  replicas: 1                 # Deployment replicas
  healthCheckPath: /          # Liveness + readiness probe path (defaults to /)

  # Database (optional — adds CNPG Postgres cluster + DB creds in Vault)
  dbNeeded: false             # If true, emits: CNPG Cluster, ExternalSecret (db), PushSecret (db), and ENV vars in the Deployment
  dbStorage: 1Gi              # CNPG PVC size

  # AWS S3 (optional — adds dedicated bucket + IAM user + creds in Vault)
  s3Needed: false             # If true, emits: ExternalSecret (aws-creds), PushSecret (aws-creds), BUCKET_NAME env var
  s3Versioning: false         # Enable bucket versioning
  s3LifecycleDays: 0          # If >0, lifecycle rule expires non-current versions after N days (only if versioning=true)
  s3CorsEnabled: false        # Add a permissive default CORS rule
```

### What the Composition emits per XR

| Always | If `dbNeeded:true` | If `s3Needed:true` |
|---|---|---|
| Deployment | + CNPG Cluster | + (AWS resources scaffolded as raw manifests in `aws-resources.yaml`, not by Composition) |
| Service (ClusterIP) | + ExternalSecret (db) | + ExternalSecret (aws-creds) |
| Ingress (nginx + letsencrypt-prod) | + PushSecret (db) | + PushSecret (aws-creds) |
| SecretStore (vault-backend) | + Deployment env vars: `DATABASE_URL`, plus all DB_* keys | + Deployment env vars: `AWS_REGION`, `BUCKET_NAME`, plus AWS_* from `<name>-aws` secret |

### Vault paths used

| When emitted | Vault KV path | Purpose |
|---|---|---|
| `dbNeeded:true` | `k8s-secrets/<namespace>/db` | CNPG-generated DB credentials (PushSecret writes; ExternalSecret reads) |
| `s3Needed:true` | `k8s-secrets/<namespace>/aws-creds` | IAM AccessKey (id + secret) (PushSecret writes; ExternalSecret reads) |

---

## 4. Step-by-step migration

### Phase 0 — Pre-flight assessment

Before touching Backstage, answer these for the app you're migrating:

1. **Fit check** (§1 matrix) — does it actually fit? If not, STOP.
2. **Inventory the existing app:**
   - Image ref + tag
   - Host (public DNS)
   - Port (container + Service)
   - Current secrets — what env vars does it need? Where do they come from?
   - DB or S3 needs?
   - Namespace — does it match the desired app name? If not, you have a renaming question to settle.
3. **Vault data audit:** If the app currently reads from a specific Vault path (e.g., `chores-tracker-backend`), note all the keys. After migration, you'll either need to (a) migrate those keys to the new IDP-managed paths or (b) keep the legacy ExternalSecret alongside the new IDP-managed one.
4. **DNS plan:** The IDP-managed app gets a fresh Ingress. If the old app uses the same hostname, you'll have a brief cutover window. Either:
   - Use a temporary hostname for the IDP-managed app first; cut DNS over after verification
   - Or take a brief outage and let the new Ingress claim the hostname

### Phase 1 — Submit the Backstage form

Navigate to Backstage → Software Catalog → Create → "Application (Crossplane)" template.

**Required fields:**
- **Name:** the app's new IDP name (must match namespace)
- **Image:** full image ref including tag
- **Host:** public hostname
- **Port:** container port (also used as Service targetPort)
- **dbNeeded / s3Needed:** flip as needed

**Optional fields (bucket sub-configs):**
- `s3Versioning`, `s3LifecycleDays`, `s3CorsEnabled` — set per your needs

**Submit.** The scaffolder:
1. Creates an ECR repo (if needed) via `aws:ecr:create`
2. Creates Vault policy + K8s auth role + placeholder secrets via `vault:setup` — **see §6.1 about reliability**
3. Opens a GitHub PR against `arigsela/kubernetes` with the new manifest files

### Phase 2 — Verify the PR before merging

The PR should contain 2 or 3 new files (3 if `s3Needed:true`):

```
base-apps/<name>.yaml                 — ArgoCD Application with finalizer + sync-wave 10
base-apps/<name>/application-xr.yaml  — XApplication XR
base-apps/<name>/aws-resources.yaml   — cluster-scoped AWS (if s3Needed)
```

**Skim each file for:**
- App name + namespace match
- Image ref correct
- Host + port match what you intend
- The XR has the right `dbNeeded` / `s3Needed` flags
- AWS resources reference the right account ID (`852893458518`) and region (`us-east-2`)

If anything's wrong, edit the PR or close it and re-submit the form.

### Phase 3 — Merge + verify deploy

After merging the PR:

```bash
# Force master-app to reconcile immediately (otherwise wait ~30s)
kubectl -n argo-cd annotate application master-app argocd.argoproj.io/refresh=hard --overwrite

# Watch the per-app Application come up
kubectl wait --for=condition=Healthy application/<name> -n argo-cd --timeout=300s
```

If it doesn't reach Healthy in 5 minutes, see §6 for common pitfalls.

**Verification commands:**

```bash
# All composed resources exist
kubectl get xapplication <name> -n <name> -o jsonpath='{.spec.resourceRefs[*].kind}' | tr ' ' '\n'

# XR Synced + Ready
kubectl get xapplication <name> -n <name> -o jsonpath='Synced={.status.conditions[?(@.type=="Synced")].status} Ready={.status.conditions[?(@.type=="Ready")].status}{"\n"}'

# ESO health
kubectl get pushsecret,secretstore,externalsecret -n <name>

# Vault entries (only if dbNeeded or s3Needed)
VAULT_TOKEN=$(kubectl get secret -n vault vault-unseal-keys -o jsonpath='{.data.root-token}' | base64 -d)
kubectl exec -n vault vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault kv list k8s-secrets/<name>

# AWS resources (only if s3Needed)
kubectl get bucket.s3.aws.upbound.io,user.iam.aws.upbound.io,accesskey.iam.aws.upbound.io | grep <name>
```

### Phase 4 — Migrate config + secrets

This is the part the IDP doesn't automate. If your old app had additional secret keys beyond what the IDP emits, you have options:

**Option A — Migrate everything into the IDP-managed flow.** Push extra keys into Vault at a path the IDP doesn't manage (e.g., `k8s-secrets/<name>/app-config`) and write a SEPARATE ExternalSecret alongside the IDP-managed one. Keep that ExternalSecret in a `base-apps/<name>-extras/` directory with its own ArgoCD Application.

**Option B — Keep config + extras alongside.** The IDP-managed app has its own Deployment that reads from `<name>-db` and `<name>-aws` secrets. If your app needs more, mount additional Secrets/ConfigMaps via either:
   - A patch-style overlay in a separate manifest (Kustomize patch on the Deployment — but the IDP-managed Deployment will fight you on selfHeal)
   - **Recommended:** add a sidecar manifest in `base-apps/<name>-extras/` that's a SEPARATE ArgoCD Application managing only the extra ConfigMap/Secret; the IDP-managed Deployment is unaware of them

**Option C — Defer to v1.6+.** If your config story is complex, keep the app on v1.0 base-apps pattern for now. v1.6 should add ConfigMap emission to the XR spec.

### Phase 5 — DNS cutover + decommission the old app

Once the IDP-managed app is verified healthy:

1. Update DNS (Route 53) to point the public hostname to the new Ingress — or, if you're using the SAME hostname, the new Ingress should claim it automatically once the old one is gone.
2. Submit Backstage "Decommission Application" template with the OLD app's name. This opens a teardown PR that deletes `base-apps/<old-name>.yaml` and `base-apps/<old-name>/`.
3. Merge the teardown PR. ArgoCD's finalizer cascade-deletes the old app's resources in ~60s.
4. Run `kubectl delete namespace <old-namespace>` to clean up the remaining namespace.
5. Run the `vault kv delete` commands from the teardown PR body to remove orphaned Vault entries.

---

## 5. Worked example — migrating `chores-tracker-backend`

`chores-tracker-backend` is a non-trivial case because it doesn't fit the v1.x IDP shape cleanly. This example shows what fits, what doesn't, and how to handle the gaps.

### 5.1 Current state inventory

| Aspect | Current | IDP fit? |
|---|---|---|
| App name | `chores-tracker-backend` | ✅ Use as-is |
| Namespace | `chores-tracker` | ❌ Must rename to `chores-tracker-backend` OR keep v1.0 pattern |
| Image | `852893458518.dkr.ecr.us-east-2.amazonaws.com/chores-tracker:7.1.0` | ✅ Standard ECR ref |
| Workload | Deployment | ✅ |
| Service | ClusterIP | ✅ |
| Ingress / routing | **Istio VirtualService** with primary/canary hosts | ❌ IDP emits nginx Ingress only |
| Database | External (RDS or similar via `crossplane_resources.yaml`) | ⚠️ Not CNPG — IDP `dbNeeded` would conflict |
| ConfigMap | `chores-tracker-backend-config` (separate from secret) | ⚠️ IDP doesn't emit ConfigMaps |
| Secret | `chores-tracker-backend-secrets` via ExternalSecret reading Vault `chores-tracker-backend` (keys: `DATABASE_URL`, `SECRET_KEY`, `DB_PASSWORD`) | ⚠️ IDP would create a different secret structure |

**Verdict:** This app has **3 blockers** for a clean v1.5 migration:
1. Istio VirtualService (not Ingress)
2. Separate ConfigMap
3. External DB (not CNPG)

Plus 1 cosmetic concern:
4. Namespace ≠ app name (would need rename)

**Honest recommendation: keep `chores-tracker-backend` on the v1.0 base-apps pattern for now.** v1.6+ should add: ConfigMap emission, Istio VirtualService support, and "bring-your-own-DB" mode (no CNPG; just secrets-only).

### 5.2 What the migration would look like if it DID fit

For pedagogical purposes, here's the path you'd take for a hypothetical chores-tracker-backend variant that uses standard Ingress + has no ConfigMap + uses CNPG.

**Backstage form values:**
- Name: `chores-tracker-backend`
- Image: `852893458518.dkr.ecr.us-east-2.amazonaws.com/chores-tracker:7.1.0`
- Host: whatever the public hostname is (e.g., `chores.arigsela.com`)
- Port: container port (need to check `deployments.yaml` — probably 8000 for the FastAPI default)
- Replicas: match existing
- dbNeeded: `true` (would replace the external DB with CNPG — significant change)
- s3Needed: `false`
- healthCheckPath: `/health` (FastAPI convention — verify against existing readiness probe)

After submit + merge:
- New namespace `chores-tracker-backend` created
- CNPG cluster spins up with a fresh empty Postgres DB
- App pod tries to start, fails initially because DB schema isn't there
- **You'd need to run migrations + dump+restore data from the old RDS database** — the IDP doesn't help with this

This is where the "all-or-nothing" gap of v1.x bites: you can't say "use the existing external DB but otherwise IDP-manage the workload." That's the v1.6 "bring-your-own-DB mode" candidate.

### 5.3 What you CAN do today — partial adoption

For an app like chores-tracker-backend, the realistic incremental path is:

1. **Keep the existing `base-apps/chores-tracker-backend/` directory exactly as-is** (v1.0 pattern).
2. **Add catalog auto-ingestion annotations to the existing manifests** so chores-tracker-backend shows up in Backstage even without the IDP scaffold. The TeraSky kubernetes-ingestor reads these annotations on any K8s resource:
   ```yaml
   metadata:
     annotations:
       terasky.backstage.io/add-to-catalog: "true"
       terasky.backstage.io/owner: group:default/platform-engineering
       terasky.backstage.io/component-type: service
       terasky.backstage.io/lifecycle: production
       terasky.backstage.io/system: platform
       backstage.io/source-location: url:https://github.com/arigsela/kubernetes/tree/main/base-apps/chores-tracker-backend
   ```
3. **Stay on the v1.0 pattern until v1.6 ships Istio support + ConfigMap support + bring-your-own-DB.**

This gives chores-tracker-backend the Backstage catalog presence (deep links, owner info, dependency graph) without forcing a workload migration it isn't ready for.

---

## 6. Common pitfalls

### 6.1 `vault:setup` action silently skips (known operational gap, v1.5)

**Symptom:** After scaffolder PR merges, SecretStore reports `InvalidProviderConfig: invalid role name "<name>"`. The XR shows `Unsynced resources: pushsecret, awspushsecret`.

**Root cause:** The Backstage `vault:setup` scaffolder action is supposed to create the Vault K8s auth role + KV policy + placeholder secrets when you submit the application template. Currently this action doesn't always fire — `smoke-v15` exposed this. Why is still under investigation (queued as v1.6 follow-up).

**Workaround:** Create the Vault role manually using the root token from the `vault-unseal-keys` secret.

```bash
VAULT_TOKEN=$(kubectl get secret -n vault vault-unseal-keys -o jsonpath='{.data.root-token}' | base64 -d)

# Replace <name> with your app name
kubectl exec -n vault vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" \
  vault write auth/kubernetes/role/<name> \
    bound_service_account_names=default \
    bound_service_account_namespaces=<name> \
    policies=app-namespace-rw,default \
    ttl=1h \
    alias_name_source=serviceaccount_uid

# Force SecretStore reconcile (it's in backoff)
kubectl annotate secretstore vault-backend -n <name> "force-sync=$(date +%s)" --overwrite

# Force ExternalSecrets reconcile too
kubectl annotate externalsecret -n <name> --all "force-sync=$(date +%s)" --overwrite
```

The `app-namespace-rw` policy is shared across all apps and templated — it grants `k8s-secrets/<namespace>/*` access based on the service account's namespace, so you don't need to create a per-app policy.

**Always verify the role exists immediately after scaffolder PR merge:**
```bash
kubectl exec -n vault vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" \
  vault list auth/kubernetes/role | grep <name>
```

### 6.2 `deletionPolicy` enum surprise

**History:** v1.5 initially shipped with `PushSecret.spec.deletionPolicy: "Retain"` based on a docs assumption. ESO v0.11.0's PushSecret CRD enum is actually `["Delete", "None"]` — `Retain` doesn't exist. Hotfixed in v1.5.1.

**If you see** `cannot apply composed resource "pushsecret": spec.deletionPolicy: Unsupported value` — verify the Composition Python at `base-apps/crossplane-compositions/composition-application.yaml` has `"deletionPolicy": "None"` (not `"Retain"`). Should be 2 occurrences (`make_push_secret` line 102, `make_aws_push_secret` line 172).

### 6.3 ECR image pull secret missing warning

**Symptom:** Pod events show `Unable to retrieve some image pull secrets (ecr-auth)`.

**This is benign IF** the image is public Docker Hub. The IDP-emitted Deployment always references `ecr-auth` as an imagePullSecret because the typical app uses ECR. Public images skip the secret lookup; the pod pulls successfully.

**If your image IS on ECR** and you get `ErrImagePull`: confirm `ecr-auth` exists in the namespace. The cluster has automation that copies `ecr-auth` into every namespace; if it didn't run for yours, manually:
```bash
kubectl get secret ecr-auth -n some-working-namespace -o yaml | \
  sed "s/namespace: some-working-namespace/namespace: <name>/" | \
  kubectl apply -f -
```

### 6.4 ConfigMaps — IDP doesn't emit them (yet)

**Symptom:** Your app needs more env config than the IDP's emitted `<name>-db` / `<name>-aws` secrets provide.

**Workaround:** Side-load a ConfigMap via a separate ArgoCD Application managing only the extra config. Create `base-apps/<name>-extras.yaml`:
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: <name>-extras
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "20"   # AFTER the main IDP app (wave 10)
spec:
  source:
    repoURL: https://github.com/arigsela/kubernetes
    path: base-apps/<name>-extras
  destination:
    namespace: <name>
    server: https://kubernetes.default.svc
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

And `base-apps/<name>-extras/configmap.yaml` with your extra config.

**Then patch the IDP-emitted Deployment to mount it.** Crossplane's selfHeal will fight you here — you'd need a Kustomize-style overlay or a Composition customization. This is genuinely awkward at v1.5; v1.6 should add native `configValues` field to the XApplication spec.

### 6.5 External DB instead of CNPG

**Symptom:** Your app uses RDS (or similar) and you don't want CNPG.

**Workaround:** Don't set `dbNeeded:true`. Instead:
1. Provision the RDS instance separately (manifests in `base-apps/<name>-rds/` with crossplane provider-aws-rds resources)
2. Push its credentials into Vault at any path you choose (e.g., `k8s-secrets/<name>/external-db`)
3. Add a separate ExternalSecret in `base-apps/<name>-extras/` that creates `<name>-db` secret in the namespace with the env vars your app expects
4. The IDP-managed Deployment will see `<name>-db` and mount it via `envFrom`

This works because the IDP-emitted Deployment only references `<name>-db` by name — it doesn't care who creates it.

### 6.6 Istio VirtualService instead of standard Ingress

**Symptom:** Your app needs Istio traffic management (canary, header-based routing, etc.).

**Workaround:** v1.5 only emits standard `networking.k8s.io/v1` Ingress with nginx + cert-manager. To add Istio:
1. Set the IDP XR's `host` to a "dummy" hostname (or use the public hostname and accept the unused Ingress)
2. Create a VirtualService in `base-apps/<name>-istio/` as a separate Application
3. Have Istio's IngressGateway route the real public traffic to the Service that the IDP emitted

Or: keep this app on the v1.0 base-apps pattern.

### 6.7 Sync-wave 10 vs immediate

The IDP-emitted per-app Application uses `argocd.argoproj.io/sync-wave: "10"`. This ensures it deploys AFTER the XRD/Composition (wave 3). When teardown happens, ArgoCD prunes in REVERSE wave order — your app (wave 10) deletes before the Composition (wave 3) — which is what you want.

**If you're adding sidecar Applications** (e.g., the `-extras` pattern in §6.4), use wave **20+** so they deploy after the IDP app and tear down before it.

### 6.8 Namespace ≠ app name

**Symptom:** The existing app uses a different namespace than the desired app name.

**The IDP enforces** `namespace == name`. The SecretStore's Vault role binding uses `bound_service_account_namespaces=<name>`, which only works if the namespace matches.

**Workarounds:**
1. **Rename the app** to match the namespace (e.g., if namespace is `chores-tracker`, name the IDP app `chores-tracker` even though the existing manifest is `chores-tracker-backend`). Update all references.
2. **Migrate to a fresh namespace** matching the new IDP app name. Coordinate DNS cutover + data migration.

This is one of the most painful migration costs for existing apps. v1.6+ could let you specify `namespace` explicitly in the XR spec, but for v1.5 it's hardcoded.

---

## 7. Rollback plan

If the IDP-managed deployment doesn't work and you need to revert to the v1.0 base-apps pattern for an app:

1. **Submit Backstage Decommission template** with the IDP app name. Merge the teardown PR. This cascade-deletes the per-app Application + XR + all composed resources.
2. **Run `kubectl delete namespace <name>`** to finish cleanup.
3. **Run the documented `vault kv delete` commands** to remove orphaned Vault entries.
4. **Re-deploy the original app via the v1.0 base-apps pattern** (either revert the original teardown PR or re-create the `base-apps/<name>/` manifests by hand).

The teardown is reliable (v1.5 race fix shipped); the only data risk is in DB state — if you used `dbNeeded:true` and CNPG spun up an empty DB, that DB is gone after teardown. Always export DB state before destroying an app that holds data.

---

## 8. Quick reference

### Verify a v1.5 deployment is healthy

```bash
NAME=<your-app-name>

# 1. Composition is live with deletionPolicy: None (v1.5 fix verification)
kubectl get composition application -o yaml | grep -c '"deletionPolicy": "None"'   # should be 2

# 2. XR Synced (Ready can be False if pod is crashing — that's a runtime issue, not IDP)
kubectl get xapplication $NAME -n $NAME -o jsonpath='Synced={.status.conditions[?(@.type=="Synced")].status}{"\n"}'

# 3. ESO healthy
kubectl get pushsecret,secretstore,externalsecret -n $NAME

# 4. PushSecrets have deletionPolicy: None (proves v1.5 fix is live)
kubectl get pushsecret -n $NAME -o jsonpath='{range .items[*]}{.metadata.name}: deletionPolicy={.spec.deletionPolicy}{"\n"}{end}'

# 5. Vault entries (if applicable)
VAULT_TOKEN=$(kubectl get secret -n vault vault-unseal-keys -o jsonpath='{.data.root-token}' | base64 -d)
kubectl exec -n vault vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault kv list k8s-secrets/$NAME

# 6. AWS resources (if s3Needed)
kubectl get bucket.s3.aws.upbound.io,user.iam.aws.upbound.io,accesskey.iam.aws.upbound.io,policy.iam.aws.upbound.io,userpolicyattachment.iam.aws.upbound.io,bucketversioning.s3.aws.upbound.io,bucketlifecycleconfiguration.s3.aws.upbound.io,bucketcorsconfiguration.s3.aws.upbound.io,bucketpublicaccessblock.s3.aws.upbound.io | grep $NAME
```

### Verify Composition changes in the cluster

```bash
# What's the current Composition?
kubectl get composition application -o yaml | head -50

# Force re-sync if you suspect ArgoCD hasn't picked up a recent Composition change
kubectl -n argo-cd annotate application crossplane-compositions argocd.argoproj.io/refresh=hard --overwrite
```

### Force ArgoCD to reconcile

```bash
# Master app (picks up new per-app Applications)
kubectl -n argo-cd annotate application master-app argocd.argoproj.io/refresh=hard --overwrite

# A specific app
kubectl -n argo-cd annotate application <name> argocd.argoproj.io/refresh=hard --overwrite
```

### Run a render-test locally (for Composition changes)

```bash
# Requires crossplane CLI + yq + Docker (colima users: export DOCKER_HOST=unix://$HOME/.colima/default/docker.sock)
cd /Users/arisela/git/kubernetes
./tests/composition/render.sh xr-with-db
./tests/composition/render.sh xr-with-s3
./tests/composition/render.sh xr-minimal
```

---

## 9. Known limitations as of v1.5 (queued for v1.6+)

| Limitation | v1.6+ candidate | Workaround today |
|---|---|---|
| `vault:setup` unreliable | Yes — investigation queued | Manual `vault write` (§6.1) |
| No CRD schema validation in render-test | Yes — `kubectl apply --dry-run=server` in harness | Visual review + smoke deploy |
| No ConfigMap emission | Yes — add `configValues` to XR | Sidecar `<name>-extras` Application (§6.4) |
| No Istio VirtualService emission | Yes (likely v1.7+) | Keep on v1.0 base-apps pattern |
| No bring-your-own-DB mode | Yes — flag `dbExternal:true` | Don't set `dbNeeded`; add `-extras` ExternalSecret (§6.5) |
| Namespace must equal app name | Yes — make namespace configurable | Rename app or migrate namespace |
| Source-code repo creation not in scaffolder | Yes — biggest remaining v2-original-candidate | Create the repo manually + reference its image |
| Only S3 in AWS resources | Yes — SQS/SNS/RDS via Option B pattern | Add raw manifests in `<name>-aws-extras/` |
| Single-port Service only | Yes (lower priority) | Add a separate Service manifest if you need multiple ports |

---

## 10. Where to look when things break

| Symptom | First place to look |
|---|---|
| Backstage scaffolder error | Backstage Pod logs: `kubectl logs -n backstage deployment/backstage \| grep -iE "scaffolder\|error"` |
| PR didn't open | GitHub token expiry; check `GITHUB_TOKEN` env in the Backstage Pod |
| ArgoCD app stuck | `kubectl describe application <name> -n argo-cd` |
| XR not synced | `kubectl describe xapplication <name> -n <name>` — look at Events at the bottom |
| SecretStore InvalidProviderConfig | §6.1 — Vault role missing |
| PushSecret stuck | `kubectl describe pushsecret <name>-db-push -n <name>` — usually a SecretStore issue upstream |
| Pod CrashLoopBackOff | `kubectl logs -n <name> deploy/<name>` — that's a runtime issue, not an IDP issue |
| Composition not picking up changes | Force-sync the `crossplane-compositions` Application (§8) |
| Teardown stuck | Look for `could not get SecretStore` in events — if present, v1.5 fix isn't deployed; verify Composition has `deletionPolicy: None` |

---

## Related docs

- **Design specs:** `docs/superpowers/specs/2026-05-*-idp-v*-design.md` (one per iteration)
- **Implementation plans:** `docs/superpowers/plans/2026-05-*-idp-v*.md` (one per iteration, includes run notes)
- **Composition source:** `base-apps/crossplane-compositions/composition-application.yaml`
- **XRD source:** `base-apps/crossplane-compositions/xrd-application.yaml`
- **Render-test harness:** `tests/composition/render.sh`
- **Backstage templates:** `arigsela/backstage:examples/templates/application/template.yaml` + `examples/templates/decommission/template.yaml`
