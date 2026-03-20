# Langflow Helm Chart Deployment Plan

**Last Updated**: 2026-03-20
**Current Status**: In Progress (Phases 1-5 manifests created, pending Vault setup + validation)
**Completion**: 22/30 tasks (73%)

---

## Overview

### What We're Deploying
[Langflow](https://github.com/langflow-ai/langflow) is a visual framework for building multi-agent and RAG applications. It provides a drag-and-drop UI for composing AI flows using LLM providers, vector stores, and custom components.

### Why Two Instances
Langflow provides two official Helm charts designed for distinct purposes:

- **`langflow-ide`** (development): Full environment with visual editor UI + API backend. Used to create, test, and debug flows. Includes both frontend (port 8080) and backend (port 7860) components deployed as separate workloads.
- **`langflow-runtime`** (production): Lightweight, locked-down runtime that executes exported flows as a service. No UI -- API-only. Flows are loaded at startup via `downloadFlows` URLs or mounted volumes.

### Helm Chart Details
- **Repository**: `https://langflow-ai.github.io/langflow-helm-charts`
- **GitHub**: [langflow-ai/langflow-helm-charts](https://github.com/langflow-ai/langflow-helm-charts)
- **Chart Versions**: `langflow-ide-0.1.1` and `langflow-runtime-0.1.1` (released 2025-01-24)
- **App Version**: `latest` (image tag override recommended)
- **IDE dependency**: Bitnami PostgreSQL 15.x (optional, disabled in our setup)

### Target Repository
All manifests described below go into **`arigsela/kubernetes`** (the GitOps repo), not `claude-agents`.

---

## Architecture

```
arigsela/kubernetes (GitOps repo)
  base-apps/
    langflow-ide.yaml          # ArgoCD Application (Helm source)
    langflow-ide/              # Supporting manifests (secrets, ingress)
      secret-store.yaml
      external-secret.yaml
      nginx-ingress.yaml
    langflow-runtime.yaml      # ArgoCD Application (Helm source)
    langflow-runtime/          # Supporting manifests (secrets)
      secret-store.yaml
      external-secret.yaml
```

### ArgoCD Strategy
For Helm-based apps, ArgoCD supports `source.helm` with inline `values`. This is the same pattern used by `external-secrets.yaml` in the existing repo. The Helm values are defined directly in the ArgoCD Application manifest, while supporting resources (ExternalSecrets, Ingress) live in a companion directory managed by a second ArgoCD Application.

### Database Strategy
Both IDE and Runtime will use the **existing PostgreSQL** instance at `postgresql.postgresql.svc.cluster.local:5432`. We will:
1. Create a dedicated `langflow` database and user in the existing PostgreSQL
2. Store credentials in Vault under `k8s-secrets/langflow`
3. Use ExternalSecrets to surface them as Kubernetes Secrets
4. Reference those secrets in the Helm values via `secretKeyRef`

---

## Phase 1: ArgoCD Application Manifests for Langflow

### Subphase 1.1: Langflow IDE ArgoCD Application
**File**: `base-apps/langflow-ide.yaml`

- ✅ Create ArgoCD Application manifest with `source.helm` pointing to the Langflow Helm repo
- ✅ Set `chart: langflow-ide`, `repoURL: https://langflow-ai.github.io/langflow-helm-charts`
- ✅ Set `targetRevision: 0.1.1`
- ✅ Configure `destination.namespace: langflow-ide`
- ✅ Enable `syncPolicy.automated` with `prune: true` and `selfHeal: true`
- ✅ Add `CreateNamespace=true` sync option
- ✅ Add inline `helm.values` (detailed in Phase 2)

**Reference** (based on existing `external-secrets.yaml` pattern):
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: langflow-ide
  namespace: argo-cd
spec:
  project: default
  source:
    chart: langflow-ide
    repoURL: https://langflow-ai.github.io/langflow-helm-charts
    targetRevision: 0.1.1
    helm:
      releaseName: langflow-ide
      values: |
        # Values from Phase 2
  destination:
    server: https://kubernetes.default.svc
    namespace: langflow-ide
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

### Subphase 1.2: Langflow IDE Config (supporting manifests)
**File**: `base-apps/langflow-ide-config.yaml`

- ✅ Create a second ArgoCD Application pointing to `base-apps/langflow-ide/` directory
- ✅ This manages ExternalSecrets, SecretStore, and Ingress that must exist BEFORE or alongside the Helm release
- ✅ Set destination namespace to `langflow-ide`

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: langflow-ide-config
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/langflow-ide
  destination:
    server: https://kubernetes.default.svc
    namespace: langflow-ide
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

### Subphase 1.3: Langflow Runtime ArgoCD Application
**File**: `base-apps/langflow-runtime.yaml`

- ✅ Create ArgoCD Application manifest with `source.helm`
- ✅ Set `chart: langflow-runtime`, same Helm repo URL
- ✅ Set `targetRevision: 0.1.1`
- ✅ Configure `destination.namespace: langflow-runtime`
- ✅ Add inline `helm.values` (detailed in Phase 3)

### Subphase 1.4: Langflow Runtime Config (supporting manifests)
**File**: `base-apps/langflow-runtime-config.yaml`

- ✅ Create ArgoCD Application pointing to `base-apps/langflow-runtime/` directory
- ✅ Set destination namespace to `langflow-runtime`

---

## Phase 2: Helm Values Configuration (IDE Chart)

### Subphase 2.1: Backend Configuration
Inline in `langflow-ide.yaml` under `helm.values`:

- ✅ Disable built-in PostgreSQL (`postgresql.enabled: false`)
- ✅ Enable external database with `langflow.backend.externalDatabase.enabled: true`
- ✅ Configure external database connection via `secretKeyRef` references:
  - `driver.value: "postgresql"`
  - `host.valueFrom.secretKeyRef` -> `langflow-db-secrets` / `db-host`
  - `port.value: "5432"`
  - `database.valueFrom.secretKeyRef` -> `langflow-db-secrets` / `db-name`
  - `user.valueFrom.secretKeyRef` -> `langflow-db-secrets` / `db-user`
  - `password.valueFrom.secretKeyRef` -> `langflow-db-secrets` / `db-password`
- ✅ Disable SQLite (`langflow.backend.sqlite.enabled: false`)
- ✅ Set backend resources (requests: 0.5 CPU, 1Gi memory)
- ⬜ Configure security context (runAsUser: 1000, readOnlyRootFilesystem: true) — deferred to post-deployment tuning

### Subphase 2.2: Frontend Configuration
- ✅ Enable frontend (`langflow.frontend.enabled: true`)
- ✅ Set frontend resources (requests: 0.3 CPU, 512Mi memory)

### Subphase 2.3: Environment Variables for API Keys
- ✅ Configure `extraEnv` to inject Anthropic API key from Kubernetes Secret:
  ```yaml
  extraEnv:
    - name: ANTHROPIC_API_KEY
      valueFrom:
        secretKeyRef:
          name: langflow-api-secrets
          key: anthropic-api-key
  ```
- ⬜ Add any additional provider keys (OpenAI, etc.) as needed via same pattern

### Subphase 2.4: Node Scheduling
- ✅ Add `nodeSelector` for `node.kubernetes.io/workload: application` (matching existing app pattern)

**Expected Helm values block**:
```yaml
helm:
  releaseName: langflow-ide
  values: |
    postgresql:
      enabled: false
    langflow:
      backend:
        sqlite:
          enabled: false
        externalDatabase:
          enabled: true
          driver:
            value: "postgresql"
          host:
            valueFrom:
              secretKeyRef:
                name: langflow-db-secrets
                key: db-host
          port:
            value: "5432"
          database:
            valueFrom:
              secretKeyRef:
                name: langflow-db-secrets
                key: db-name
          user:
            valueFrom:
              secretKeyRef:
                name: langflow-db-secrets
                key: db-user
          password:
            valueFrom:
              secretKeyRef:
                name: langflow-db-secrets
                key: db-password
        resources:
          requests:
            cpu: "500m"
            memory: "1Gi"
        extraEnv:
          - name: ANTHROPIC_API_KEY
            valueFrom:
              secretKeyRef:
                name: langflow-api-secrets
                key: anthropic-api-key
      frontend:
        enabled: true
        resources:
          requests:
            cpu: "300m"
            memory: "512Mi"
    nodeSelector:
      node.kubernetes.io/workload: application
```

---

## Phase 3: Helm Values Configuration (Runtime Chart)

### Subphase 3.1: Core Runtime Configuration
Inline in `langflow-runtime.yaml` under `helm.values`:

- ✅ Configure database URL via environment variable pointing to the same PostgreSQL
- ✅ Set `env` with `LANGFLOW_DATABASE_URL` referencing the `langflow-db-secrets` Secret
- ✅ Set resources (requests: 1 CPU, 2Gi memory for production workloads)
- ✅ Set `replicaCount: 1` initially (scale later based on load)
- ✅ Configure security context (readOnlyRootFilesystem: true, runAsUser: 1000)

### Subphase 3.2: API Keys and Flow Loading
- ✅ Configure `extraEnv` for Anthropic API key (same secretKeyRef pattern as IDE)
- ⬜ Configure `downloadFlows` if flows should be loaded from URLs at startup (or leave empty initially)

### Subphase 3.3: Node Scheduling
- ✅ Add `nodeSelector` for `node.kubernetes.io/workload: application`

**Expected Helm values block**:
```yaml
helm:
  releaseName: langflow-runtime
  values: |
    replicaCount: 1
    env:
      - name: LANGFLOW_DATABASE_URL
        valueFrom:
          secretKeyRef:
            name: langflow-db-secrets
            key: database-url
      - name: ANTHROPIC_API_KEY
        valueFrom:
          secretKeyRef:
            name: langflow-api-secrets
            key: anthropic-api-key
    resources:
      requests:
        cpu: "1000m"
        memory: "2Gi"
    securityContext:
      runAsUser: 1000
      runAsNonRoot: true
      readOnlyRootFilesystem: true
    nodeSelector:
      node.kubernetes.io/workload: application
```

---

## Phase 4: Vault + External Secrets for API Keys

### Subphase 4.1: Vault Secret Creation
- ⬜ Create Vault secrets at path `k8s-secrets/langflow`:
  - `anthropic-api-key` - Anthropic API key for Claude
  - `db-host` - `postgresql.postgresql.svc.cluster.local`
  - `db-port` - `5432`
  - `db-name` - `langflow`
  - `db-user` - `langflow`
  - `db-password` - PostgreSQL password for langflow user
  - `database-url` - Full SQLAlchemy URL: `postgresql://langflow:<password>@postgresql.postgresql.svc.cluster.local:5432/langflow`
- ⬜ Create Vault role `langflow-ide` for the langflow-ide namespace
- ⬜ Create Vault role `langflow-runtime` for the langflow-runtime namespace
- ⬜ Create Vault policy allowing both roles to read `k8s-secrets/langflow`

### Subphase 4.2: SecretStore for IDE Namespace
**File**: `base-apps/langflow-ide/secret-store.yaml`

- ✅ Create SecretStore for `langflow-ide` namespace using the established pattern

```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: langflow-ide
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "langflow-ide"
          serviceAccountRef:
            name: "default"
```

### Subphase 4.3: ExternalSecret for IDE Namespace
**File**: `base-apps/langflow-ide/external-secret.yaml`

- ✅ Create ExternalSecret producing `langflow-db-secrets` (db credentials)
- ✅ Create ExternalSecret producing `langflow-api-secrets` (API keys)

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: langflow-db-secrets
  namespace: langflow-ide
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: langflow-db-secrets
    creationPolicy: Owner
  data:
    - secretKey: db-host
      remoteRef:
        key: langflow
        property: db-host
    - secretKey: db-name
      remoteRef:
        key: langflow
        property: db-name
    - secretKey: db-user
      remoteRef:
        key: langflow
        property: db-user
    - secretKey: db-password
      remoteRef:
        key: langflow
        property: db-password
---
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: langflow-api-secrets
  namespace: langflow-ide
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: langflow-api-secrets
    creationPolicy: Owner
  data:
    - secretKey: anthropic-api-key
      remoteRef:
        key: langflow
        property: anthropic-api-key
```

### Subphase 4.4: SecretStore + ExternalSecret for Runtime Namespace
**Files**: `base-apps/langflow-runtime/secret-store.yaml`, `base-apps/langflow-runtime/external-secret.yaml`

- ✅ Mirror the same pattern for `langflow-runtime` namespace (different Vault role, same secret paths)

### Subphase 4.5: PostgreSQL Database Setup
- ⬜ Create `langflow` database and user in the existing PostgreSQL instance
  ```sql
  CREATE USER langflow WITH PASSWORD '<from-vault>';
  CREATE DATABASE langflow OWNER langflow;
  GRANT ALL PRIVILEGES ON DATABASE langflow TO langflow;
  ```

---

## Phase 5: Ingress/Networking (Accessing the IDE UI)

### Subphase 5.1: NGINX Ingress for IDE
**File**: `base-apps/langflow-ide/nginx-ingress.yaml`

The IDE needs external access for the visual editor. The Runtime does NOT need external ingress (accessed internally by other services).

- ✅ Create Ingress resource for the Langflow IDE frontend service

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: langflow-ide-nginx
  namespace: langflow-ide
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
    nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "120"
    nginx.ingress.kubernetes.io/proxy-connect-timeout: "30"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "120"
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - langflow.arigsela.com
    secretName: langflow-ide-tls
  rules:
  - host: langflow.arigsela.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: langflow-ide-langflow-service-frontend
            port:
              number: 8080
```

### Subphase 5.2: Backend API Route (if needed)
- ⬜ Determine if the frontend proxies API calls to the backend or if a separate ingress path is needed for `/api`
- ⬜ If needed, add a second path rule routing `/api/*` to the backend service on port 7860

### Subphase 5.3: DNS Configuration
- ⬜ Create DNS A/CNAME record for `langflow.arigsela.com` pointing to the NGINX ingress load balancer IP

---

## Phase 6: Validation and Testing

### Subphase 6.1: Pre-Deployment Checks
- ⬜ Verify Vault secrets are populated at `k8s-secrets/langflow`
- ⬜ Verify Vault roles and policies exist for both namespaces
- ⬜ Verify PostgreSQL database and user are created
- ⬜ Verify DNS record resolves correctly

### Subphase 6.2: Deployment Validation
- ⬜ Commit manifests to `arigsela/kubernetes` on a feature branch
- ⬜ Create PR and verify ArgoCD detects the new Applications
- ⬜ Merge and monitor ArgoCD sync status for all four Applications:
  - `langflow-ide` (Helm)
  - `langflow-ide-config` (manifests)
  - `langflow-runtime` (Helm)
  - `langflow-runtime-config` (manifests)
- ⬜ Verify ExternalSecrets sync successfully (check `kubectl get externalsecrets -n langflow-ide`)
- ⬜ Verify pods are running in both namespaces

### Subphase 6.3: Functional Testing
- ⬜ Access `https://langflow.arigsela.com` and verify the IDE UI loads
- ⬜ Create a simple test flow in the IDE using the Anthropic/Claude component
- ⬜ Verify the flow executes successfully (validates API key injection)
- ⬜ Export a flow and test loading it in the Runtime instance
- ⬜ Verify Runtime API responds at its internal service URL

---

## Technical Notes

### PostgreSQL
- The existing PostgreSQL at `postgresql.postgresql.svc.cluster.local:5432` will host the `langflow` database
- Langflow uses SQLAlchemy and supports PostgreSQL natively with the `postgresql` driver
- The IDE chart's built-in Bitnami PostgreSQL dependency will be **disabled** (`postgresql.enabled: false`)
- For production best practices, enable SSL: append `?sslmode=require` to the database URL if PostgreSQL is configured with TLS

### Persistence
- The IDE backend runs as a **StatefulSet** which provides stable network identity
- With external PostgreSQL, the only local storage needed is for temporary files (emptyDir volumes for `/tmp`, `/app/flows`, `/app/data`)
- The SQLite volume (`/app/db`) is unused when external PostgreSQL is configured but the chart still mounts it -- this is harmless

### Scaling
- **IDE**: Typically 1 replica (single developer environment). Can scale backend replicas if multiple developers need concurrent access
- **Runtime**: Start with 1 replica, scale horizontally via `replicaCount` or HPA. Production recommendation is 3 replicas with 2Gi RAM and 1 CPU each
- Langflow supports Prometheus metrics via `LANGFLOW_PROMETHEUS_ENABLED=True` (add to extraEnv if monitoring is desired)

### Security
- Both charts default to non-root execution (UID 1000) with read-only root filesystem
- All capabilities are dropped (`capabilities.drop: ["ALL"]`)
- API keys are never stored in Git -- they flow: Vault -> ExternalSecret -> K8s Secret -> Pod env var
- Consider enabling Langflow authentication (`langflow.backend.autoLogin: false`) and setting a superuser password via Vault

### Image Tags
- The charts default to `latest` which is not ideal for production
- Pin to a specific version tag (e.g., `1.1.1`) in the Helm values:
  ```yaml
  langflow:
    global:
      image:
        tag: "1.1.1"  # Pin to specific version
  ```

### Service Names (Important for Ingress)
The Helm chart uses a `nameOverride: "langflow-service"` by default. The actual service names created will be:
- Frontend: `langflow-ide-langflow-service-frontend` (port 8080)
- Backend: `langflow-ide-langflow-service-backend` (port 7860)

Verify these names after initial deployment and update the Ingress `backend.service.name` if they differ.

### Sync Order Consideration
ExternalSecrets must create the K8s Secrets BEFORE the Helm chart pods start (otherwise pods will fail with missing secret references). ArgoCD handles this naturally since the `-config` Application syncs independently. However, on first deployment:
1. Merge the `-config` manifests first (or at the same time)
2. Wait for ExternalSecrets to show `SecretSynced` status
3. The Helm Application pods will retry and eventually pick up the secrets

If timing is an issue, add `argocd.argoproj.io/sync-wave` annotations to control ordering.

---

## File Summary

| File (in `arigsela/kubernetes`) | Purpose |
|---|---|
| `base-apps/langflow-ide.yaml` | ArgoCD App - Helm chart for IDE |
| `base-apps/langflow-ide-config.yaml` | ArgoCD App - supporting manifests for IDE |
| `base-apps/langflow-ide/secret-store.yaml` | Vault SecretStore for langflow-ide namespace |
| `base-apps/langflow-ide/external-secret.yaml` | ExternalSecrets for DB creds + API keys |
| `base-apps/langflow-ide/nginx-ingress.yaml` | NGINX Ingress for IDE UI access |
| `base-apps/langflow-runtime.yaml` | ArgoCD App - Helm chart for Runtime |
| `base-apps/langflow-runtime-config.yaml` | ArgoCD App - supporting manifests for Runtime |
| `base-apps/langflow-runtime/secret-store.yaml` | Vault SecretStore for langflow-runtime namespace |
| `base-apps/langflow-runtime/external-secret.yaml` | ExternalSecrets for DB creds + API keys |
