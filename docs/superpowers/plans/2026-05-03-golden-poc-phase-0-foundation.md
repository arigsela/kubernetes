# Golden POC — Phase 0: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the platform infrastructure for the Golden AI Platform POC: install agentregistry + agentgateway + Langfuse as base-apps, reconfigure kagent to route LLM traffic through agentgateway and emit OTLP to Langfuse, set up the GitHub App for the PR Review Agent, and resolve the two preflight unknowns from the design doc.

**Architecture:** Three new ArgoCD `Application` manifests (under `base-apps/`), each pointing at an upstream Helm chart with inline values. Vault holds platform secrets (Anthropic key, Langfuse keys, GitHub App private key, agentregistry admin token); ExternalSecret resources sync them per-namespace. kagent reconfig is a single PR to its existing `base-apps/kagent.yaml`.

**Tech Stack:** ArgoCD, Helm (kagent v0.8.6 already installed), Crossplane v2 (already installed), Vault + External Secrets (already running per-namespace), CNPG (already running), agentregistry v0.3.x (Solo.io, pre-1.0), agentgateway (Solo.io, donated to LF), Langfuse self-hosted.

**Reference design:** `docs/superpowers/specs/2026-05-02-golden-ai-platform-poc-design.md`

**Branching reminder:** This plan operates on a worktree branch. Each commit lands on the worktree branch; all changes ship via the PR opened at the end of plan execution (handled by the wrapping plan, not by this plan's individual tasks).

---

## Task 0.1: Preflight check — kagent ToolServer CRD shape

**Files:**
- Create: `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md`

The design doc has an open question (Section 11): does kagent's `ToolServer` CRD natively accept agentregistry OCI refs, or do we need a shim? Phase 1's Composition design depends on this answer.

- [ ] **Step 1: Clone or browse the kagent project to find the ToolServer CRD source**

Run: `gh repo clone kagent-dev/kagent /tmp/kagent && cd /tmp/kagent && grep -r "kind: ToolServer" --include="*.go" --include="*.yaml" | head -20`
Expected: paths to the CRD definition (likely under `api/v1alpha1/` or similar).

- [ ] **Step 2: Read the ToolServer CRD spec schema**

Open the source file from Step 1. Identify the fields under `spec.config` or equivalent. Specifically check for: `image:`, `oci:`, `chart:`, `url:`, `command:`, `transport:`, `stdio:`, `sse:`, `streamableHttp:`.

- [ ] **Step 3: Determine the OCI integration pattern**

Based on Step 2's findings, classify into one of three buckets:
- **Bucket A** — ToolServer accepts an OCI image directly (e.g., `spec.config.image: ghcr.io/...`). Phase 1 Composition just templates the URI. Easiest.
- **Bucket B** — ToolServer accepts an HTTP/SSE/streamable URL pointing to a running MCP server, but does NOT pull from OCI. We need a controller or sidecar that runs the MCP server image and exposes it. Phase 1 needs a per-skill Deployment template.
- **Bucket C** — ToolServer accepts something else (kagent-specific format, registry refs, etc.). Document the actual shape.

- [ ] **Step 4: Record findings**

Create file `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md` with:

```markdown
# Phase 0 Preflight Results

## Preflight 1 — kagent ToolServer CRD shape

**kagent version inspected:** v0.8.6 (matches `base-apps/kagent.yaml` `targetRevision: 0.8.6`)
**Source path:** [paste path from Step 1]
**Bucket determined:** [A | B | C]
**Schema excerpt:**

\```yaml
[paste relevant schema portion from CRD]
\```

**Phase 1 Composition implication:**
[1-2 sentences describing what the Composition will render based on the bucket]

---

## Preflight 2 — agentregistry HTTP API

[filled in by Task 0.2]
```

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md
git commit -m "docs(golden-poc): record Phase 0 preflight 1 — kagent ToolServer shape"
```

---

## Task 0.2: Preflight check — agentregistry HTTP API

**Files:**
- Modify: `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md`

Phase 3's Backstage embedded skill catalog plugin queries agentregistry's HTTP API. We need the endpoint paths to write the plugin.

- [ ] **Step 1: Clone agentregistry source**

Run: `gh repo clone agentregistry-dev/agentregistry /tmp/agentregistry && cd /tmp/agentregistry`

- [ ] **Step 2: Find the HTTP API surface**

Run: `grep -r "func.*Handler\|http.HandleFunc\|router.HandleFunc\|app.Get\|app.Post" --include="*.go" cmd/ pkg/ internal/ 2>/dev/null | head -40`

Or look for OpenAPI/Swagger generation: `find . -name "*.yaml" -path "*/api/*" -o -name "openapi*.json" 2>/dev/null`.

- [ ] **Step 3: Identify the four key endpoints we need for the Backstage plugin**

For the Skill catalog plugin (design Section 5) we need:
1. **List artifacts** — paginated list of all artifacts, filterable by type and tag.
2. **Get artifact details** — single artifact by name + version.
3. **List artifact versions** — version history for an artifact.
4. **List tags** — distinct tag values across artifacts (for filter UI).

Document the actual paths, request/response shapes, and auth requirements. If only some endpoints exist, document workarounds (e.g., "no `/tags` endpoint; client computes tag set from `/artifacts` response").

- [ ] **Step 4: Record findings**

Append to `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md` under the "Preflight 2" header:

```markdown
## Preflight 2 — agentregistry HTTP API

**agentregistry version inspected:** [version from go.mod or git tag]
**Source path:** [paste path]
**Base URL pattern:** `https://agentregistry.<base-domain>/api/v1` (or actual)

### Endpoints

| Need | Method + Path | Response shape (relevant fields) | Auth |
|---|---|---|---|
| List artifacts | [paste] | [paste] | [paste] |
| Get artifact | [paste] | [paste] | [paste] |
| List versions | [paste] | [paste] | [paste] |
| List tags | [paste or "compute client-side"] | n/a | n/a |

### Phase 3 plugin implication

[1-2 sentences describing what the Backstage plugin will fetch and how]
```

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md
git commit -m "docs(golden-poc): record Phase 0 preflight 2 — agentregistry HTTP API"
```

---

## Task 0.3: Set up GitHub App for PR Review Agent

**Files:**
- Modify: `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md` (record App ID + install ID)

The PR Review Agent (Phase 2) needs a GitHub App with PR-read + PR-comment-write permissions. This is one-time admin work in the GitHub UI; capture results so Phase 2 can wire them up.

- [ ] **Step 1: Create the GitHub App**

Go to https://github.com/settings/apps/new (personal) or https://github.com/organizations/<org>/settings/apps/new (org).

Fill in:
- **GitHub App name:** `golden-poc-pr-reviewer`
- **Homepage URL:** `https://github.com/arigsela/kubernetes`
- **Webhook URL:** `https://github-webhook.<base-domain>/webhook` (placeholder; the adapter is built in Phase 2 — webhook deliveries will fail until then, that's fine)
- **Webhook secret:** generate with `openssl rand -hex 32`. Save the secret to a scratchpad — going into Vault in Step 4.
- **Repository permissions:**
  - Contents: Read-only
  - Pull requests: Read and write
  - Metadata: Read-only (mandatory)
- **Subscribe to events:** Pull request
- **Where can this GitHub App be installed:** Only on this account

Click "Create GitHub App". Note the **App ID** (top of the App's settings page after creation).

- [ ] **Step 2: Generate a private key**

In the App settings, scroll to "Private keys" → "Generate a private key". A `.pem` file downloads. Open it; you'll need its contents in Step 4.

- [ ] **Step 3: Install the App on `arigsela/kubernetes`**

In the App settings, click "Install App" in the left sidebar. Choose the account, select "Only select repositories" → check `arigsela/kubernetes`. Install.

After install, the URL contains the **Installation ID** (the number after `/installations/`). Record it.

- [ ] **Step 4: Store credentials in Vault**

Run (from a host with `vault` CLI authenticated):

```bash
vault kv put k8s-secrets/github-webhook-adapter \
  app_id=<App ID from Step 1> \
  installation_id=<Installation ID from Step 3> \
  webhook_secret=<webhook secret from Step 1> \
  private_key=@/path/to/downloaded/pem-file.pem
```

- [ ] **Step 5: Record App identifiers in preflight-results doc**

Append to `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md`:

```markdown
## GitHub App: golden-poc-pr-reviewer

- **App ID:** [number]
- **Installation ID:** [number]
- **Vault path:** `k8s-secrets/github-webhook-adapter`
- **Repo scope:** arigsela/kubernetes
- **Permissions:** Contents:read, Pull requests:read+write, Metadata:read
- **Subscribed events:** Pull request
- **Webhook URL (Phase 2 will wire this up):** `https://github-webhook.<base-domain>/webhook`
- **Webhook secret:** stored in Vault (NOT recorded here)
- **Private key:** stored in Vault (NOT recorded here)
```

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md
git commit -m "docs(golden-poc): record GitHub App identifiers (creds in Vault)"
```

---

## Task 0.4: Stage Anthropic API key for agentgateway in Vault

**Files:** None (Vault-only operation; no repo changes)

kagent already has its own Anthropic key at the path it uses (`kagent-anthropic` Secret in `kagent` namespace). agentgateway will hold the platform-wide key under its own Vault path so kagent can be reconfigured to route through it (Task 0.10).

- [ ] **Step 1: Verify the key exists in Vault**

Run:
```bash
vault kv get k8s-secrets/agentgateway 2>/dev/null || echo "Path does not exist yet"
```

- [ ] **Step 2: If missing, write the Anthropic API key**

If the path does not exist or lacks the key:
```bash
vault kv put k8s-secrets/agentgateway ANTHROPIC_API_KEY=<your-key>
```

If you need a new key, create one at https://console.anthropic.com/settings/keys.

- [ ] **Step 3: Verify**

Run: `vault kv get k8s-secrets/agentgateway`
Expected: shows the key path with `ANTHROPIC_API_KEY` listed (value masked).

No commit — this task only stages a secret in Vault. The downstream task (0.9) creates the ExternalSecret manifest that pulls it.

---

## Task 0.5: Install Langfuse — namespace + database secret + ArgoCD app

**Files:**
- Create: `base-apps/langfuse.yaml`
- Create: `base-apps/langfuse/namespace.yaml`
- Create: `base-apps/langfuse/secret-store.yaml`
- Create: `base-apps/langfuse/external-secret-db.yaml`
- Create: `base-apps/langfuse/cnpg-cluster.yaml`

Langfuse uses Postgres. Reuse the existing CNPG pattern so the database lives in the cluster like other CNPG-backed apps.

- [ ] **Step 1: Create the namespace manifest**

Create `base-apps/langfuse/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: langfuse
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/warn: restricted
```

- [ ] **Step 2: Create the namespace's SecretStore**

Create `base-apps/langfuse/secret-store.yaml` mirroring the per-namespace pattern in `base-apps/n8n/secret-store.yaml`:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: langfuse
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "langfuse"
          serviceAccountRef:
            name: "default"
```

- [ ] **Step 3: Add the Vault role for the langfuse namespace**

This is a Vault-side operation, run from a host with `vault` CLI:

```bash
vault write auth/kubernetes/role/langfuse \
  bound_service_account_names=default \
  bound_service_account_namespaces=langfuse \
  policies=k8s-secrets-read \
  ttl=24h
```

- [ ] **Step 4: Create the CNPG Cluster manifest for Langfuse's Postgres**

Create `base-apps/langfuse/cnpg-cluster.yaml`:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: langfuse-db
  namespace: langfuse
spec:
  instances: 1
  imageName: ghcr.io/cloudnative-pg/postgresql:16
  bootstrap:
    initdb:
      database: langfuse
      owner: langfuse
  storage:
    size: 5Gi
```

- [ ] **Step 5: Create the ExternalSecret that exposes the CNPG-generated DB credentials**

CNPG generates a Secret named `<cluster>-app` (here `langfuse-db-app`) with `uri`, `host`, `port`, `username`, `password`, `dbname`. Langfuse reads `DATABASE_URL`. Use a PushSecret pattern OR pass the CNPG secret directly.

For simplicity, the Langfuse Helm values (Task 0.7) will reference `langfuse-db-app` directly. No ExternalSecret needed for the DB; only the Langfuse instance keys (next step) need ESO.

Create `base-apps/langfuse/external-secret-db.yaml` as a NoOp placeholder marker so this step has an artifact:

```yaml
# CNPG generates langfuse-db-app Secret in this namespace automatically.
# The Langfuse Helm chart references it via env vars.
# This file is intentionally empty of resources — kept for documentation
# alignment with other base-apps directories.
```

(If your linter rejects empty YAML, replace with a single-line comment in a `.md` file in the same dir, or skip this step.)

- [ ] **Step 6: Create the ArgoCD Application for the namespace + db (Langfuse install itself comes in Task 0.6)**

Create `base-apps/langfuse.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: langfuse
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/langfuse
  destination:
    server: https://kubernetes.default.svc
    namespace: langfuse
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

- [ ] **Step 7: Commit and let ArgoCD sync the namespace + DB**

```bash
git add base-apps/langfuse.yaml base-apps/langfuse/
git commit -m "feat(langfuse): namespace, SecretStore, CNPG cluster (Phase 0)"
```

After this commit lands on main (via the eventual PR), wait ~1 minute for ArgoCD sync.

- [ ] **Step 8: Verify CNPG cluster is healthy**

Run (after ArgoCD sync):
```bash
kubectl get cluster -n langfuse
kubectl get secret langfuse-db-app -n langfuse
```
Expected: cluster status `Cluster in healthy state`; secret exists with keys uri/host/port/username/password/dbname.

If the secret hasn't appeared, CNPG is still bootstrapping — wait another minute and recheck.

---

## Task 0.6: Install Langfuse application via Helm-as-ArgoCD-app

**Files:**
- Create: `base-apps/langfuse-app.yaml`

Langfuse's official Helm chart deploys the web/worker/db. Since CNPG is the DB (Task 0.5), the Helm chart's bundled Postgres is disabled.

- [ ] **Step 1: Determine pinned chart version**

Run: `helm search repo langfuse 2>/dev/null || helm repo add langfuse https://langfuse.github.io/langfuse-k8s && helm search repo langfuse/langfuse --versions | head -5`

Pick the latest stable (e.g., `0.x.x`). Record the version chosen.

- [ ] **Step 2: Create the ArgoCD Application for the Helm chart**

Create `base-apps/langfuse-app.yaml`. Replace `<CHART_VERSION>` with the pinned version from Step 1:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: langfuse-app
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "1"  # after langfuse namespace + db (sync-wave 0)
spec:
  project: default
  source:
    chart: langfuse
    repoURL: https://langfuse.github.io/langfuse-k8s
    targetRevision: <CHART_VERSION>
    helm:
      valuesObject:
        # Disable bundled Postgres; use CNPG cluster from Task 0.5
        postgresql:
          deploy: false
        langfuse:
          # CNPG generates a "uri" key in langfuse-db-app secret
          additionalEnv:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: langfuse-db-app
                  key: uri
          nextauth:
            url: https://langfuse.<base-domain>
            secret:
              # Generated at install; rotation requires re-deploy
              valueFrom:
                secretKeyRef:
                  name: langfuse-instance
                  key: nextauth-secret
          salt:
            valueFrom:
              secretKeyRef:
                name: langfuse-instance
                key: salt
        # Ingress
        ingress:
          enabled: true
          className: nginx
          annotations:
            cert-manager.io/cluster-issuer: letsencrypt-prod
            nginx.ingress.kubernetes.io/ssl-redirect: "true"
          hosts:
            - host: langfuse.<base-domain>
              paths:
                - path: /
                  pathType: Prefix
          tls:
            - hosts:
                - langfuse.<base-domain>
              secretName: langfuse-tls
  destination:
    server: https://kubernetes.default.svc
    namespace: langfuse
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
```

**Important:** replace `<base-domain>` with your actual base domain (whatever pattern the existing nginx ingresses use — check `base-apps/backstage/nginx-ingress.yaml` for the pattern).

- [ ] **Step 3: Stage the `langfuse-instance` Secret in Vault**

Generate two random strings:
```bash
NEXTAUTH=$(openssl rand -hex 32)
SALT=$(openssl rand -hex 32)
vault kv put k8s-secrets/langfuse \
  nextauth-secret="$NEXTAUTH" \
  salt="$SALT"
```

- [ ] **Step 4: Add an ExternalSecret to expose `langfuse-instance` into the namespace**

Create `base-apps/langfuse/external-secret-instance.yaml`:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: langfuse-instance
  namespace: langfuse
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: langfuse-instance
    creationPolicy: Owner
  data:
    - secretKey: nextauth-secret
      remoteRef:
        key: langfuse
        property: nextauth-secret
    - secretKey: salt
      remoteRef:
        key: langfuse
        property: salt
```

- [ ] **Step 5: Commit**

```bash
git add base-apps/langfuse-app.yaml base-apps/langfuse/external-secret-instance.yaml
git commit -m "feat(langfuse): install via Helm + instance secrets (Phase 0)"
```

- [ ] **Step 6: After ArgoCD sync, smoke-test Langfuse**

Wait for ArgoCD sync (~2 minutes for the chart to install). Then:

```bash
kubectl get pods -n langfuse
# Expected: langfuse-web, langfuse-worker pods Running
curl -fsS https://langfuse.<base-domain>/api/public/health
# Expected: {"status":"OK"}
```

Open `https://langfuse.<base-domain>` in a browser. Sign up (Langfuse self-host requires creating a user account on first visit). Create a project named **`golden-poc`**. Note the Public Key (starts with `pk-lf-...`) and Secret Key (starts with `sk-lf-...`) — these go into Vault next.

- [ ] **Step 7: Stage Langfuse project keys in Vault**

```bash
vault kv put k8s-secrets/langfuse-project \
  public_key="<pk-lf-... from Step 6>" \
  secret_key="<sk-lf-... from Step 6>" \
  project_id=golden-poc
```

These keys are consumed by kagent (Task 0.10) and Backstage (Phase 3).

---

## Task 0.7: Install agentgateway — namespace + ArgoCD app

**Files:**
- Create: `base-apps/agentgateway.yaml`
- Create: `base-apps/agentgateway/namespace.yaml`
- Create: `base-apps/agentgateway/secret-store.yaml`
- Create: `base-apps/agentgateway/external-secret.yaml`

agentgateway is Solo.io's HTTP/gRPC proxy for AI traffic. We deploy it as a transparent proxy fronting the Anthropic API.

- [ ] **Step 1: Find the Helm chart**

Visit https://agentgateway.dev/docs/install or run:
```bash
helm repo add agentgateway https://agentgateway.dev/charts 2>/dev/null
helm search repo agentgateway --versions | head -5
```

If the chart URL has changed, browse https://github.com/agentgateway and find the actual chart location. Record the resolved repoURL and version.

- [ ] **Step 2: Create namespace manifest**

`base-apps/agentgateway/namespace.yaml`:
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: agentgateway
```

- [ ] **Step 3: Create namespace SecretStore**

`base-apps/agentgateway/secret-store.yaml`:
```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: agentgateway
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "agentgateway"
          serviceAccountRef:
            name: "default"
```

- [ ] **Step 4: Create the Vault role**

```bash
vault write auth/kubernetes/role/agentgateway \
  bound_service_account_names=default,agentgateway \
  bound_service_account_namespaces=agentgateway \
  policies=k8s-secrets-read \
  ttl=24h
```

- [ ] **Step 5: Create the ExternalSecret for the Anthropic key**

`base-apps/agentgateway/external-secret.yaml`:
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: agentgateway-anthropic
  namespace: agentgateway
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: agentgateway-anthropic
    creationPolicy: Owner
  data:
    - secretKey: ANTHROPIC_API_KEY
      remoteRef:
        key: agentgateway
        property: ANTHROPIC_API_KEY
```

- [ ] **Step 6: Create the ArgoCD app for the namespace resources (sync-wave 0)**

`base-apps/agentgateway.yaml`:
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: agentgateway
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/agentgateway
  destination:
    server: https://kubernetes.default.svc
    namespace: agentgateway
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

- [ ] **Step 7: Commit**

```bash
git add base-apps/agentgateway.yaml base-apps/agentgateway/
git commit -m "feat(agentgateway): namespace, SecretStore, Anthropic ExternalSecret (Phase 0)"
```

- [ ] **Step 8: Verify**

After ArgoCD sync:
```bash
kubectl get secret agentgateway-anthropic -n agentgateway
# Expected: secret exists with ANTHROPIC_API_KEY key
```

---

## Task 0.8: Install agentgateway Helm chart

**Files:**
- Create: `base-apps/agentgateway-app.yaml`

- [ ] **Step 1: Create the ArgoCD Application for the Helm chart**

Replace `<REPO_URL>` and `<CHART_VERSION>` with the values resolved in Task 0.7 Step 1:

`base-apps/agentgateway-app.yaml`:
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: agentgateway-app
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  project: default
  source:
    chart: agentgateway
    repoURL: <REPO_URL>
    targetRevision: <CHART_VERSION>
    helm:
      valuesObject:
        # Anthropic upstream config — exact values keys depend on the chart's
        # schema. Adjust based on the chart's values.yaml after Step 2 review.
        upstreams:
          anthropic:
            type: anthropic
            baseURL: https://api.anthropic.com
            apiKeySecret:
              name: agentgateway-anthropic
              key: ANTHROPIC_API_KEY
        # Cluster-internal Service for kagent to call
        service:
          type: ClusterIP
          port: 80
          targetPort: 8080
        # Rate limiting (POC level)
        rateLimit:
          enabled: true
          requestsPerSecond: 10
        # Logging
        logging:
          level: info
          format: json
  destination:
    server: https://kubernetes.default.svc
    namespace: agentgateway
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
```

- [ ] **Step 2: Inspect the chart's actual values schema before merging**

Run:
```bash
helm show values <REPO_URL>/agentgateway --version <CHART_VERSION> > /tmp/agentgateway-values.yaml
less /tmp/agentgateway-values.yaml
```

Compare against the keys used in Step 1's `valuesObject`. If keys differ (e.g., `upstreams` is actually called `providers` or `backends`), update `base-apps/agentgateway-app.yaml` accordingly. **Do not commit the updated file until the keys match the chart's schema.**

- [ ] **Step 3: Commit**

```bash
git add base-apps/agentgateway-app.yaml
git commit -m "feat(agentgateway): install via Helm with Anthropic upstream (Phase 0)"
```

- [ ] **Step 4: After ArgoCD sync, smoke-test agentgateway**

```bash
kubectl get pods -n agentgateway
# Expected: agentgateway pod Running

# From any other namespace, send a Claude request through the gateway:
kubectl run curl-test --rm -i --image=curlimages/curl --restart=Never -- \
  curl -sS -X POST http://agentgateway.agentgateway.svc.cluster.local/v1/messages \
  -H "anthropic-version: 2023-06-01" \
  -H "x-api-key: dummy-internal-token" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 64,
    "messages": [{"role":"user","content":"Say PONG."}]
  }'
```

Expected: a JSON response containing `"content":[{"type":"text","text":"PONG..."}]`.

If the gateway returns 401/403, the upstream key wiring needs adjustment — review the chart values' actual auth-injection mechanism and update `base-apps/agentgateway-app.yaml` accordingly. Iterate until the smoke test passes before proceeding.

---

## Task 0.9: Install agentregistry — namespace + ArgoCD app

**Files:**
- Create: `base-apps/agentregistry.yaml`
- Create: `base-apps/agentregistry/namespace.yaml`
- Create: `base-apps/agentregistry/secret-store.yaml`
- Create: `base-apps/agentregistry/external-secret.yaml`
- Create: `base-apps/agentregistry/ingress.yaml`

- [ ] **Step 1: Create namespace manifest**

`base-apps/agentregistry/namespace.yaml`:
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: agentregistry
```

- [ ] **Step 2: Stage admin token in Vault**

```bash
vault kv put k8s-secrets/agentregistry \
  admin_token="$(openssl rand -hex 32)"
```

- [ ] **Step 3: Create namespace SecretStore**

`base-apps/agentregistry/secret-store.yaml`:
```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: agentregistry
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "agentregistry"
          serviceAccountRef:
            name: "default"
```

- [ ] **Step 4: Create the Vault role**

```bash
vault write auth/kubernetes/role/agentregistry \
  bound_service_account_names=default,agentregistry \
  bound_service_account_namespaces=agentregistry \
  policies=k8s-secrets-read \
  ttl=24h
```

- [ ] **Step 5: Create the ExternalSecret**

`base-apps/agentregistry/external-secret.yaml`:
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: agentregistry-admin
  namespace: agentregistry
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: agentregistry-admin
    creationPolicy: Owner
  data:
    - secretKey: admin_token
      remoteRef:
        key: agentregistry
        property: admin_token
```

- [ ] **Step 6: Create the Ingress for external UI access**

`base-apps/agentregistry/ingress.yaml`:
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: agentregistry
  namespace: agentregistry
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - agentregistry.<base-domain>
      secretName: agentregistry-tls
  rules:
    - host: agentregistry.<base-domain>
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                # Service name comes from the agentregistry chart; verify in Task 0.10 Step 2
                name: agentregistry
                port:
                  number: 12121
```

(Replace `<base-domain>` with your actual base domain.)

- [ ] **Step 7: Create the ArgoCD app**

`base-apps/agentregistry.yaml`:
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: agentregistry
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/agentregistry
  destination:
    server: https://kubernetes.default.svc
    namespace: agentregistry
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

- [ ] **Step 8: Commit**

```bash
git add base-apps/agentregistry.yaml base-apps/agentregistry/
git commit -m "feat(agentregistry): namespace, SecretStore, ExternalSecret, Ingress (Phase 0)"
```

---

## Task 0.10: Install agentregistry Helm chart

**Files:**
- Create: `base-apps/agentregistry-app.yaml`

- [ ] **Step 1: Find the Helm chart**

The agentregistry repo's README documents the Helm install. Run:
```bash
git clone https://github.com/agentregistry-dev/agentregistry /tmp/ar-chart 2>/dev/null
ls /tmp/ar-chart/charts/ /tmp/ar-chart/deploy/ /tmp/ar-chart/helm/ 2>/dev/null
```

Find the `Chart.yaml`. The chart may be hosted at `oci://ghcr.io/agentregistry-dev/agentregistry` or as a packaged tarball. Record the actual location.

If only a local chart exists, fork the chart into `base-apps/agentregistry/chart/` and use `path:` instead of `chart:` in the ArgoCD Application.

- [ ] **Step 2: Create the ArgoCD Application for agentregistry**

Replace `<REPO_URL>` and `<CHART_VERSION>` with the values from Step 1.

`base-apps/agentregistry-app.yaml`:
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: agentregistry-app
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  project: default
  source:
    chart: agentregistry
    repoURL: <REPO_URL>
    targetRevision: <CHART_VERSION>
    helm:
      valuesObject:
        # Persistent storage for OCI artifacts
        persistence:
          enabled: true
          size: 10Gi
          storageClass: ""  # default
        # Web UI on port 12121 (per project docs)
        service:
          type: ClusterIP
          port: 12121
        # Admin token from ExternalSecret (Task 0.9)
        auth:
          adminTokenSecret:
            name: agentregistry-admin
            key: admin_token
  destination:
    server: https://kubernetes.default.svc
    namespace: agentregistry
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - ServerSideApply=true
```

- [ ] **Step 3: Inspect the chart's actual values schema**

Same approach as Task 0.8 Step 2. Run `helm show values <REPO_URL>/agentregistry --version <CHART_VERSION>` and reconcile. Update before commit if keys differ.

- [ ] **Step 4: Commit**

```bash
git add base-apps/agentregistry-app.yaml
git commit -m "feat(agentregistry): install via Helm with persistent storage (Phase 0)"
```

- [ ] **Step 5: After ArgoCD sync, smoke-test agentregistry**

```bash
kubectl get pods -n agentregistry
# Expected: agentregistry pod Running

curl -fsS https://agentregistry.<base-domain>/api/v1/health 2>/dev/null \
  || curl -fsS https://agentregistry.<base-domain>/healthz
# Expected: 200 OK with health body
```

Open `https://agentregistry.<base-domain>` in a browser — should see the agentregistry UI.

- [ ] **Step 6: Install `arctl` CLI locally for skill seeding (Task 0.11)**

Per the agentregistry docs:
```bash
curl -fsSL https://raw.githubusercontent.com/agentregistry-dev/agentregistry/main/scripts/get-arctl | bash
arctl --version
```

Configure `arctl` to point at the registry:
```bash
arctl login https://agentregistry.<base-domain> --token <admin_token from Vault>
```

---

## Task 0.11: Seed agentregistry with the four POC skills

**Files:**
- Create: `scripts/seed-agentregistry.sh`

The four POC skills are pushed via `arctl`. This is one-time work; the script lives in the repo so it's reproducible (e.g., re-seeding after a registry rebuild).

- [ ] **Step 1: Write the seed script**

Create `scripts/seed-agentregistry.sh`:

```bash
#!/usr/bin/env bash
# Seed agentregistry with the four POC skills.
# Idempotent: re-running pushes new versions but doesn't fail if already present.
set -euo pipefail

REGISTRY_URL="${REGISTRY_URL:-https://agentregistry.<base-domain>}"
ADMIN_TOKEN="${ADMIN_TOKEN:?must be set; vault kv get -field=admin_token k8s-secrets/agentregistry}"

arctl login "$REGISTRY_URL" --token "$ADMIN_TOKEN"

# Skill 1: kubernetes-mcp
arctl push skill \
  --name kubernetes-mcp \
  --version v1 \
  --type mcp-server \
  --image ghcr.io/manusa/kubernetes-mcp-server:latest \
  --tags k8s,read-only,observability \
  --description "Read-only Kubernetes API access via MCP."

# Skill 2: prometheus-mcp (Coroot equivalent OK)
arctl push skill \
  --name prometheus-mcp \
  --version v1 \
  --type mcp-server \
  --image ghcr.io/pab1it0/prometheus-mcp-server:latest \
  --tags metrics,read-only,observability \
  --description "PromQL queries via MCP. POC accepts Coroot proxy."

# Skill 3: github-mcp (Anthropic's official server)
arctl push skill \
  --name github-mcp \
  --version v1 \
  --type mcp-server \
  --image ghcr.io/github/github-mcp-server:latest \
  --tags github,review \
  --description "GitHub Pull Request and Issue tools via MCP."

# Skill 4: k8s-yaml-lint (custom; published in Phase 2 of the POC)
# In Phase 0 we register the placeholder; Phase 2 builds and pushes the actual image.
arctl push skill \
  --name k8s-yaml-lint \
  --version v0-placeholder \
  --type mcp-server \
  --image ghcr.io/arigsela/k8s-yaml-lint:placeholder \
  --tags k8s,lint,custom \
  --description "Structural YAML/Kustomize linting via kube-linter. Placeholder until Phase 2 builds the real image."

echo "Seed complete. Verify in UI: $REGISTRY_URL"
arctl list skills
```

(Replace `<base-domain>` and verify exact `arctl push` flag names against `arctl push --help`. The flag set may differ if agentregistry has evolved; adjust accordingly.)

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/seed-agentregistry.sh
```

- [ ] **Step 3: Run the seed script**

```bash
ADMIN_TOKEN=$(vault kv get -field=admin_token k8s-secrets/agentregistry) \
  REGISTRY_URL=https://agentregistry.<base-domain> \
  ./scripts/seed-agentregistry.sh
```

Expected output: four "pushed" lines and a final `arctl list skills` showing all four entries.

- [ ] **Step 4: Verify in the UI**

Open `https://agentregistry.<base-domain>`. The four skills should appear in the catalog.

- [ ] **Step 5: Commit the script**

```bash
git add scripts/seed-agentregistry.sh
git commit -m "feat(agentregistry): seed script for the four POC skills (Phase 0)"
```

---

## Task 0.12: Reconfigure kagent to route LLM calls through agentgateway

**Files:**
- Modify: `base-apps/kagent.yaml`

kagent currently calls Anthropic directly using the `kagent-anthropic` Secret. We point kagent at agentgateway's internal endpoint instead. agentgateway holds the real Anthropic key and forwards.

- [ ] **Step 1: Determine kagent's mechanism for overriding the Anthropic base URL**

Two possibilities depending on kagent's chart values:
- **Option A:** kagent's `providers.anthropic` section accepts a `baseURL` or `endpoint` field. Look in the kagent Helm chart values.yaml.
- **Option B:** kagent uses the upstream Anthropic SDK and respects the `ANTHROPIC_BASE_URL` env var. Inject it via `controller.env` in the Helm values.

Run:
```bash
helm show values ghcr.io/kagent-dev/kagent/helm/kagent --version 0.8.6 \
  | grep -i -A3 "baseurl\|endpoint\|anthropic" | head -50
```

Choose the option that matches the chart and update `base-apps/kagent.yaml` accordingly.

- [ ] **Step 2: Update `base-apps/kagent.yaml`**

If Option A (chart-native field): under `providers.anthropic`, add:
```yaml
        providers:
          default: anthropic
          anthropic:
            provider: Anthropic
            model: claude-haiku-4-5-20251001
            apiKeySecretRef: kagent-anthropic
            apiKeySecretKey: ANTHROPIC_API_KEY
            baseURL: http://agentgateway.agentgateway.svc.cluster.local/v1
```

If Option B (env var): under `controller.env`, append:
```yaml
        controller:
          # ... (preserve existing volumes/volumeMounts)
          env:
            - name: ANTHROPIC_BASE_URL
              value: http://agentgateway.agentgateway.svc.cluster.local/v1
```

The exact YAML location depends on the chart's structure observed in Step 1.

- [ ] **Step 3: Note the implication for kagent's `kagent-anthropic` Secret**

When kagent talks to agentgateway instead of Anthropic, the value of `ANTHROPIC_API_KEY` it sends becomes a token that agentgateway can choose to validate or ignore. For POC simplicity:
- Keep the `kagent-anthropic` Secret in the `kagent` namespace as-is.
- agentgateway is configured (Task 0.8) to ignore inbound auth and inject its own Anthropic key on the upstream call.

If agentgateway is configured to validate inbound tokens (a Phase-1.5 hardening), update both kagent's Secret and agentgateway's allowlist together.

- [ ] **Step 4: Commit**

```bash
git add base-apps/kagent.yaml
git commit -m "feat(kagent): route LLM calls through agentgateway (Phase 0)"
```

- [ ] **Step 5: After ArgoCD sync, smoke-test the new path**

Wait for the kagent controller pod to roll out (~1 minute):
```bash
kubectl rollout status deployment/kagent-controller -n kagent
```

Invoke any existing kagent Agent that's enabled (e.g., `k8s-agent` per `base-apps/kagent.yaml`):
```bash
# Adjust this curl to whatever your existing pattern is for kagent agent invocation;
# the goal is just to make ONE LLM call go through.
kubectl exec -n kagent deploy/kagent-controller -- /usr/local/bin/kagent-cli \
  invoke --agent k8s-agent --input "list namespaces" 2>&1 | head -20
```

Then check agentgateway logs for the request:
```bash
kubectl logs -n agentgateway -l app=agentgateway --tail=20
# Expected: a log line showing POST /v1/messages with model=claude-haiku-4-5-20251001
```

If agentgateway received nothing, the kagent reconfig is still pointing at Anthropic directly — re-check Step 2.

---

## Task 0.13: Configure kagent to emit OTLP traces to Langfuse (in addition to Coroot)

**Files:**
- Modify: `base-apps/kagent.yaml`
- Create: `base-apps/kagent/external-secret-langfuse.yaml`

Currently kagent emits OTLP to Coroot at `coroot-coroot.coroot.svc.cluster.local:4317` (per existing `base-apps/kagent.yaml` lines 121-135). We add a second OTLP exporter targeting Langfuse so traces appear in both UIs (no impact on existing Coroot flow).

- [ ] **Step 1: Identify Langfuse's OTLP ingest endpoint**

Langfuse self-host typically exposes OTLP at `/api/public/otel/v1/traces` (HTTP) on the main web service. Verify by:
```bash
curl -fsS https://langfuse.<base-domain>/api/public/otel/v1/traces -X POST -i 2>&1 | head -5
# Expected: 401 (auth required) or 415 (unsupported media type for empty body) — proves the endpoint exists.
```

- [ ] **Step 2: Stage Langfuse OTLP auth in Vault**

Langfuse OTLP requires Basic Auth with `<public_key>:<secret_key>` (set in Task 0.6 Step 7). Encode the auth header value:

```bash
PK=$(vault kv get -field=public_key k8s-secrets/langfuse-project)
SK=$(vault kv get -field=secret_key k8s-secrets/langfuse-project)
AUTH=$(echo -n "$PK:$SK" | base64 -w 0)
vault kv put k8s-secrets/kagent-langfuse otlp_auth_header="Basic $AUTH"
```

- [ ] **Step 3: Add the Vault role for kagent (if not already present)**

```bash
vault read auth/kubernetes/role/kagent 2>/dev/null \
  || vault write auth/kubernetes/role/kagent \
       bound_service_account_names=default,kagent \
       bound_service_account_namespaces=kagent \
       policies=k8s-secrets-read \
       ttl=24h
```

- [ ] **Step 4: Create the namespace SecretStore (if missing) and ExternalSecret**

Check if `base-apps/kagent/secret-store.yaml` exists. If yes, just add the ExternalSecret. If no, create both.

`base-apps/kagent/external-secret-langfuse.yaml`:
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: kagent-langfuse
  namespace: kagent
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: kagent-langfuse
    creationPolicy: Owner
  data:
    - secretKey: OTLP_AUTH_HEADER
      remoteRef:
        key: kagent-langfuse
        property: otlp_auth_header
```

- [ ] **Step 5: Update `base-apps/kagent.yaml` to add the second OTLP exporter**

Modify the `otel.tracing` block. The kagent chart's exact schema for multiple exporters depends on its OpenTelemetry Collector configuration. Two patterns are common:

**Pattern X — chart supports `otel.tracing.exporters` list:**
```yaml
        otel:
          tracing:
            enabled: true
            exporters:
              - name: coroot
                otlp:
                  endpoint: "coroot-coroot.coroot.svc.cluster.local:4317"
                  protocol: "grpc"
                  insecure: true
              - name: langfuse
                otlp:
                  endpoint: "https://langfuse.<base-domain>/api/public/otel/v1/traces"
                  protocol: "http/protobuf"
                  headers:
                    authorization: ${OTLP_AUTH_HEADER}
```

**Pattern Y — chart only supports a single exporter:**
Leave the existing Coroot exporter, AND deploy a small `otel-collector` Deployment in the `kagent` namespace that fans out (one input from kagent, two outputs to Coroot and Langfuse). This is more work and shifts to a new sub-task — recommend doing Pattern X first if the chart supports it.

Inspect the chart values to determine which pattern applies:
```bash
helm show values ghcr.io/kagent-dev/kagent/helm/kagent --version 0.8.6 \
  | yq '.otel' 2>/dev/null
```

- [ ] **Step 6: Mount the OTLP_AUTH_HEADER env var**

If Pattern X with `${OTLP_AUTH_HEADER}` interpolation requires the env var on the controller pod, also add to `controller.envFrom` in `base-apps/kagent.yaml`:
```yaml
        controller:
          # ...
          envFrom:
            - secretRef:
                name: kagent-langfuse
```

- [ ] **Step 7: Commit**

```bash
git add base-apps/kagent.yaml base-apps/kagent/external-secret-langfuse.yaml
git commit -m "feat(kagent): emit OTLP traces to Langfuse (Phase 0)"
```

- [ ] **Step 8: After ArgoCD sync, smoke-test the trace flow end to end**

```bash
# Trigger a kagent invocation (any agent works)
# (substitute your actual invocation command from Task 0.12 Step 5)

# Then check Langfuse:
# Open https://langfuse.<base-domain> → project "golden-poc" → Traces tab
# Expected: at least one trace appears within ~30 seconds
```

If no trace appears: check `kubectl logs -n kagent deploy/kagent-controller --tail=50 | grep -i otlp` for export errors. Common causes: OTLP_AUTH_HEADER format mismatch (must include the literal word "Basic "), wrong endpoint protocol (HTTP vs gRPC).

---

## Task 0.14: End-to-end Phase 0 verification

**Files:** None (verification-only task)

This task confirms all Phase 0 components are working together before we proceed to Phase 1.

- [ ] **Step 1: All ArgoCD applications healthy**

```bash
argocd app list 2>/dev/null \
  | grep -E "langfuse|langfuse-app|agentgateway|agentgateway-app|agentregistry|agentregistry-app|kagent" \
  | awk '{print $1, $2, $6, $7}' \
  | column -t
```

Expected: all rows show `Synced` and `Healthy`.

- [ ] **Step 2: All four skills in agentregistry**

```bash
curl -fsS -H "Authorization: Bearer $(vault kv get -field=admin_token k8s-secrets/agentregistry)" \
  https://agentregistry.<base-domain>/api/v1/skills | jq '.[] | .name'
```

Expected: `kubernetes-mcp`, `prometheus-mcp`, `github-mcp`, `k8s-yaml-lint` (the placeholder version is acceptable).

- [ ] **Step 3: Anthropic call traverses the gateway**

Run any kagent agent invocation, then immediately:
```bash
kubectl logs -n agentgateway -l app=agentgateway --tail=10 \
  | grep -E "POST /v1/messages|claude-"
```

Expected: at least one log line within the last 30 seconds matches.

- [ ] **Step 4: Trace appears in Langfuse**

Open `https://langfuse.<base-domain>`, navigate to project `golden-poc` → Traces. Expected: trace count > 0.

- [ ] **Step 5: GitHub App can be reached**

```bash
gh api /apps/golden-poc-pr-reviewer 2>&1 \
  || echo "App settings (private endpoint): https://github.com/settings/apps/golden-poc-pr-reviewer"
```

Visit the URL. Verify the App is installed on `arigsela/kubernetes` (Installation ID matches what's in Vault).

- [ ] **Step 6: Update preflight-results doc with end-to-end status**

Append a final section to `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md`:

```markdown
## Phase 0 — End-to-end status

- [x] Langfuse running, project `golden-poc` created, OTLP ingest verified.
- [x] agentgateway running, anthropic upstream verified end-to-end.
- [x] agentregistry running, four skills seeded (k8s-yaml-lint is placeholder).
- [x] kagent reconfigured to route through agentgateway and emit OTLP to Langfuse.
- [x] GitHub App `golden-poc-pr-reviewer` installed on arigsela/kubernetes.

**Phase 1 ready to start.**
```

- [ ] **Step 7: Final commit**

```bash
git add docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md
git commit -m "docs(golden-poc): Phase 0 end-to-end verification complete"
```

Phase 0 complete. Phase 1 plan can now be executed; it has unambiguous answers for both preflight checks and a working platform to deploy onto.
