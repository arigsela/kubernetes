# Backstage + Crossplane IDP — Application Onboarding Template — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v1 of a Backstage Software Template (`application-template`) backed by a Crossplane v2 Composition that provisions Deployment + Service + Ingress + optional CloudNativePG database, end-to-end through the existing GitOps + master-app pipeline.

**Architecture:** Four phases. Phase 1 lays the Crossplane platform (XRD, Composition skeleton, function-python install, ArgoCD apps) and the local `crossplane render` test harness. Phase 2 fills in the Composition Python script under TDD against committed golden YAML outputs, then in-cluster smoke. Phase 3 ships the Backstage Template + content + app-config wiring, with a local scaffolder dry-run before the user rebuilds the image. Phase 4 runs the full end-to-end through the live Backstage UI plus a regression on the existing CrewAI template.

**Tech Stack:** Crossplane v2.2.1, `function-python` (community), CloudNativePG, ArgoCD, Backstage v1.48.0, Nunjucks (Backstage's template engine), `crossplane render` CLI, kubectl, GitHub MCP.

**Source spec:** `docs/superpowers/specs/2026-04-28-backstage-crossplane-idp-design.md` (commit `c3ae043`).

**Repos touched:**
- `arigsela/kubernetes` — manifests, tests, ArgoCD apps, plan doc updates.
- `arigsela/backstage` — template, content, app-config, image rebuild + tag bump (image build is **the user's** responsibility per CLAUDE.md; the plan stops at "build me an image with these changes").

---

## File Map

### `arigsela/kubernetes` (this repo)

| Path | Action | Responsibility |
|---|---|---|
| `base-apps/crossplane-functions.yaml` | Create | ArgoCD Application that points at `base-apps/crossplane-functions/` |
| `base-apps/crossplane-functions/function-python.yaml` | Create | Crossplane `Function` CR pinning `function-python` to a specific OCI image |
| `base-apps/crossplane-compositions.yaml` | Create | ArgoCD Application that points at `base-apps/crossplane-compositions/` |
| `base-apps/crossplane-compositions/xrd-application.yaml` | Create | `XApplication` XRD (namespaced, v1alpha1) |
| `base-apps/crossplane-compositions/composition-application.yaml` | Create | `Application` Composition with inline Python script |
| `tests/composition/render.sh` | Create | One-line wrapper around `crossplane render` for the golden-diff loop |
| `tests/composition/functions.yaml` | Create | Functions list passed to `crossplane render` (just `function-python`) |
| `tests/composition/xr-minimal.yaml` | Create | Test input: `XApplication` with no DB |
| `tests/composition/xr-with-db.yaml` | Create | Test input: `XApplication` with `dbNeeded: true` |
| `tests/composition/expected-xr-minimal.yaml` | Create | Golden output for `xr-minimal.yaml` (filename matches `expected-${CASE}.yaml` from `render.sh`) |
| `tests/composition/expected-xr-with-db.yaml` | Create | Golden output for `xr-with-db.yaml` |
| `tests/composition/README.md` | Create | How to run the regression + how to update golden when the Composition intentionally changes |
| `base-apps/backstage/deployments.yaml` | Modify (Phase 3) | Bump `backstage-portal` image tag once the user produces the new image |

### `arigsela/backstage` (cloned at `docs/reference/backstage/` for reference; changes happen in that repo)

| Path | Action | Responsibility |
|---|---|---|
| `examples/templates/application/template.yaml` | Create | Backstage `Template` definition: form parameters + scaffolder steps |
| `examples/templates/application/content-k8s/catalog-info.yaml` | Create | Nunjucks-templated `Component` entity for the scaffolded app |
| `examples/templates/application/content-k8s/application-xr.yaml` | Create | Nunjucks-templated `XApplication` XR |
| `examples/templates/application/content-k8s/argocd-application.yaml` | Create | Nunjucks-templated ArgoCD `Application` |
| `examples/templates/application/README.md` | Create | One page on what this template does + the dev-loop |
| `app-config.yaml` | Modify | One new `catalog.locations` entry registering the template |

### What the plan does NOT touch

- Any existing app under `base-apps/` other than `backstage/deployments.yaml` (single line tag bump in Phase 3).
- The CrewAI template (`examples/templates/crewai-agent/`) — included as a Phase 4 regression check.
- Vault, ESO `SecretStore` resources, cert-manager, nginx-ingress, master-app, terraform.
- Backstage source code under `packages/` or `plugins/` — the new template is configuration only.

---

## Pre-flight (run once before Task 1.1)

### P.1 — Local CLI prerequisites

```bash
crossplane version --client
# Expected: v1.x or higher (any modern release works for `render`)

docker info >/dev/null && echo "docker ok"
# Expected: "docker ok" — required because function-python runs as an OCI container during `crossplane render`

yq --version
# Expected: yq version 4.x — used to diff rendered output against goldens
```

If `crossplane` is missing: `brew install crossplane`.
If Docker is not running: open Docker Desktop / start the daemon.
If `yq` is missing: `brew install yq`.

### P.2 — Confirm cluster access

```bash
kubectl config current-context
# Expected: homelab cluster context (192.168.0.100)

kubectl -n crossplane-system get deploy crossplane -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
# Expected: contains "crossplane:v2.2.1" (matches base-apps/crossplane-system/Chart.yaml)

kubectl -n postgresql get cluster postgresql-cluster -o jsonpath='{.status.phase}{"\n"}'
# Expected: "Cluster in healthy state" — confirms CNPG operator works in this cluster
```

### P.3 — Confirm `arigsela/backstage` is cloned and on `main`

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
git status --short        # Expected: empty (clean working tree)
git branch --show-current  # Expected: main
git remote -v              # Expected: origin = https://github.com/arigsela/backstage.git
cd /Users/arisela/git/kubernetes
```

Halt and resolve any failure above before starting Phase 1.

### P.4 — Branch strategy

All commits in this plan land on a feature branch in `arigsela/kubernetes` (`feat/crossplane-idp-application-template`) opened as a PR at the end of each phase. ArgoCD auto-syncs `main`, so we never push directly to `main` — the merge of each phase's PR is the deployment trigger.

```bash
cd /Users/arisela/git/kubernetes
git checkout -b feat/crossplane-idp-application-template
```

---

## Phase 1 — Crossplane platform foundations

**What this phase delivers:** XRD, Composition skeleton (empty Python script — produces zero resources), `function-python` install, two new ArgoCD Applications, and the local `crossplane render` test harness with one **failing** golden test. Composition logic is intentionally absent — Phase 2 fills it in under TDD.

**Phase 1 exit criteria:**
- New ArgoCD apps `crossplane-functions` and `crossplane-compositions` show Healthy in ArgoCD.
- `kubectl get xrds` lists `xapplications.platform.arigsela.com`.
- `kubectl get functions.pkg.crossplane.io` lists `function-python`.
- `tests/composition/render.sh` runs end-to-end and produces YAML output (which fails the golden-diff because the script is empty — that's expected).

### Task 1.1 — Set up the test harness

**Files:**
- Create: `tests/composition/render.sh`
- Create: `tests/composition/functions.yaml`
- Create: `tests/composition/README.md`

- [ ] **Step 1: Create `tests/composition/functions.yaml`**

This file enumerates Functions for `crossplane render` — only `function-python` for now.

```yaml
# tests/composition/functions.yaml
# Function references passed to `crossplane render`. Must list every function
# the Composition uses. Image must match base-apps/crossplane-functions/function-python.yaml.
apiVersion: pkg.crossplane.io/v1
kind: Function
metadata:
  name: function-python
  annotations:
    render.crossplane.io/runtime: Docker
spec:
  package: xpkg.upbound.io/crossplane-contrib/function-python:v0.4.0
```

> **Verification note:** before locking in `v0.4.0`, confirm the latest release at <https://github.com/crossplane-contrib/function-python/releases>. If newer, use the latest. The same version must appear in `base-apps/crossplane-functions/function-python.yaml` (Task 1.4).

- [ ] **Step 2: Create `tests/composition/render.sh`**

```bash
#!/usr/bin/env bash
# tests/composition/render.sh — local TDD loop for the Composition.
# Usage:
#   ./tests/composition/render.sh xr-minimal
#   ./tests/composition/render.sh xr-with-db
# Diffs `crossplane render` output against tests/composition/expected-<name>.yaml.
# Exit code 0 = match, non-zero = diff.

set -euo pipefail

CASE="${1:?Usage: $0 <case-name without extension>}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
COMPO="${ROOT}/../../base-apps/crossplane-compositions/composition-application.yaml"
XRD="${ROOT}/../../base-apps/crossplane-compositions/xrd-application.yaml"
FUNCS="${ROOT}/functions.yaml"
XR="${ROOT}/${CASE}.yaml"
EXPECTED="${ROOT}/expected-${CASE}.yaml"

ACTUAL="$(crossplane render -x "${XR}" "${COMPO}" "${FUNCS}" --extra-resources "${XRD}")"

# Normalize both sides before diff:
# - sort_by(.kind) makes document order deterministic (crossplane render emits
#   composed resources alphabetically by kind, which differs from how a human
#   typically orders the golden).
# - del(.status) removes runtime-controller status (synthetic Ready=True the
#   render emits on the XR) — we test composed children, not render-time status.
# - del(.metadata.ownerReferences) and del(.metadata.labels."crossplane.io/composite")
#   strip render-tooling plumbing crossplane render adds to composed resources.
# - (... comments="") strips the file-header comments yq otherwise leaks through.
NORMALIZE='[.] | sort_by(.kind) | .[] | (... comments="") | del(.status) | del(.metadata.ownerReferences) | del(.metadata.labels."crossplane.io/composite") | sort_keys(..)'
diff <(yq ea -P "${NORMALIZE}" <<<"${ACTUAL}") <(yq ea -P "${NORMALIZE}" "${EXPECTED}")
```

- [ ] **Step 3: Make it executable and add `tests/composition/README.md`**

```bash
chmod +x /Users/arisela/git/kubernetes/tests/composition/render.sh
```

```markdown
# Composition tests

Local regression suite for `Application` Composition (XRD: `XApplication`).

## Run

```bash
./tests/composition/render.sh xr-minimal
./tests/composition/render.sh xr-with-db
```

Exit code 0 = output matches `expected-<case>.yaml`. Non-zero = diff printed; investigate.

## Update goldens (when Composition intentionally changes)

```bash
# Capture new output as the golden:
crossplane render \
  tests/composition/xr-minimal.yaml \
  base-apps/crossplane-compositions/composition-application.yaml \
  tests/composition/functions.yaml \
  --extra-resources base-apps/crossplane-compositions/xrd-application.yaml \
  | yq -P 'sort_keys(..)' \
  > tests/composition/expected-xr-minimal.yaml
```

## Requires

- `crossplane` CLI
- Docker daemon running (function-python pulls + runs as OCI image)
- `yq` v4
```

- [ ] **Step 4: Commit**

```bash
git add tests/composition/render.sh tests/composition/functions.yaml tests/composition/README.md
git commit -m "test(composition): add crossplane render harness"
```

### Task 1.2 — Write the `XApplication` XRD

**Files:**
- Create: `base-apps/crossplane-compositions/xrd-application.yaml`

- [ ] **Step 1: Create the XRD file**

```yaml
# base-apps/crossplane-compositions/xrd-application.yaml
# XApplication — the developer-facing API for "I have an image, run it."
# Namespaced (Crossplane v2 default). Schema is v1alpha1 — we will iterate.
# Image pull secret, health probes, resource limits, kubernetes-id label etc.
# are platform defaults set by the Composition, not XR fields.
apiVersion: apiextensions.crossplane.io/v2
kind: CompositeResourceDefinition
metadata:
  name: xapplications.platform.arigsela.com
  annotations:
    argocd.argoproj.io/sync-wave: "2"
spec:
  scope: Namespaced
  group: platform.arigsela.com
  names:
    kind: XApplication
    plural: xapplications
  defaultCompositionRef:
    name: application
  versions:
    - name: v1alpha1
      served: true
      referenceable: true
      schema:
        openAPIV3Schema:
          type: object
          required: [spec]
          properties:
            spec:
              type: object
              required: [image, host, port]
              properties:
                image:
                  type: string
                  description: "Full container image ref including tag, e.g. nginx:1.25"
                host:
                  type: string
                  description: "FQDN for the public Ingress, e.g. my-app.arigsela.com"
                port:
                  type: integer
                  default: 8080
                  minimum: 1
                  maximum: 65535
                replicas:
                  type: integer
                  default: 2
                  minimum: 1
                  maximum: 10
                env:
                  type: array
                  description: "Plain-text env vars; never use for secrets"
                  items:
                    type: object
                    required: [name, value]
                    properties:
                      name:  { type: string }
                      value: { type: string }
                dbNeeded:
                  type: boolean
                  default: false
                dbStorage:
                  type: string
                  default: "1Gi"
            status:
              type: object
              x-kubernetes-preserve-unknown-fields: true
```

- [ ] **Step 2: Validate with kubectl client-side**

```bash
kubectl apply --dry-run=client -f base-apps/crossplane-compositions/xrd-application.yaml
# Expected: "compositeresourcedefinition.apiextensions.crossplane.io/xapplications.platform.arigsela.com created (dry run)"
```

- [ ] **Step 3: Commit**

```bash
git add base-apps/crossplane-compositions/xrd-application.yaml
git commit -m "feat(crossplane): add XApplication XRD (namespaced, v1alpha1)"
```

### Task 1.3 — Write the Composition skeleton (empty script)

**Files:**
- Create: `base-apps/crossplane-compositions/composition-application.yaml`

The script returns no resources. This makes `render.sh xr-minimal` produce *empty* output — the failing test for Phase 2.

- [ ] **Step 1: Create the Composition**

```yaml
# base-apps/crossplane-compositions/composition-application.yaml
# Application — single Composition for XApplication.
# Pipeline: one step, function-python, with the script that maps XR spec to
# Deployment + Service + Ingress + optional CNPG Cluster.
# In Phase 1 the script is intentionally empty; Phase 2 fills it in under TDD.
apiVersion: apiextensions.crossplane.io/v1
kind: Composition
metadata:
  name: application
  annotations:
    argocd.argoproj.io/sync-wave: "3"
spec:
  compositeTypeRef:
    apiVersion: platform.arigsela.com/v1alpha1
    kind: XApplication
  mode: Pipeline
  pipeline:
    - step: render-resources
      functionRef:
        name: function-python
      input:
        apiVersion: python.fn.crossplane.io/v1beta1
        kind: Script
        script: |
          # PHASE 1 STUB — replaced by Phase 2 Task 2.2.
          def compose(req, rsp):
              # Deliberately produce nothing so render.sh fails until Phase 2.
              pass
```

- [ ] **Step 2: Validate**

```bash
kubectl apply --dry-run=client -f base-apps/crossplane-compositions/composition-application.yaml
# Expected: "composition.apiextensions.crossplane.io/application created (dry run)"
```

- [ ] **Step 3: Commit**

```bash
git add base-apps/crossplane-compositions/composition-application.yaml
git commit -m "feat(crossplane): add Application Composition skeleton"
```

### Task 1.4 — Install `function-python`

**Files:**
- Create: `base-apps/crossplane-functions/function-python.yaml`

- [ ] **Step 1: Create the Function CR**

```yaml
# base-apps/crossplane-functions/function-python.yaml
# function-python — runs an inline Python script during Composition rendering.
# Version MUST match tests/composition/functions.yaml so local render and
# in-cluster reconcile produce the same outputs.
apiVersion: pkg.crossplane.io/v1
kind: Function
metadata:
  name: function-python
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  package: xpkg.upbound.io/crossplane-contrib/function-python:v0.4.0
  packagePullPolicy: IfNotPresent
  revisionActivationPolicy: Automatic
  revisionHistoryLimit: 1
```

> Verify the version matches `tests/composition/functions.yaml`. If you bumped the version in Task 1.1, bump it here too.

- [ ] **Step 2: Validate**

```bash
kubectl apply --dry-run=client -f base-apps/crossplane-functions/function-python.yaml
# Expected: "function.pkg.crossplane.io/function-python created (dry run)"
```

- [ ] **Step 3: Commit**

```bash
git add base-apps/crossplane-functions/function-python.yaml
git commit -m "feat(crossplane): install function-python"
```

### Task 1.5 — Wire two new ArgoCD Applications

**Files:**
- Create: `base-apps/crossplane-functions.yaml`
- Create: `base-apps/crossplane-compositions.yaml`

Master-app picks these up automatically (every `base-apps/*.yaml` becomes an ArgoCD Application).

- [ ] **Step 1: Create `base-apps/crossplane-functions.yaml`**

```yaml
# base-apps/crossplane-functions.yaml
# ArgoCD Application for Crossplane composition functions.
# Synced before crossplane-compositions (lower wave on the Function CR inside).
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: crossplane-functions
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/crossplane-functions
  destination:
    server: https://kubernetes.default.svc
    namespace: crossplane-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=false
```

- [ ] **Step 2: Create `base-apps/crossplane-compositions.yaml`**

```yaml
# base-apps/crossplane-compositions.yaml
# ArgoCD Application for XRDs and Compositions (cluster-scoped resources).
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: crossplane-compositions
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/crossplane-compositions
  destination:
    server: https://kubernetes.default.svc
    namespace: crossplane-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=false
```

- [ ] **Step 3: Commit + open Phase 1 PR**

```bash
git add base-apps/crossplane-functions.yaml base-apps/crossplane-compositions.yaml
git commit -m "feat(crossplane): wire ArgoCD apps for functions + compositions"
git push -u origin feat/crossplane-idp-application-template
```

Open a PR titled **"Phase 1: Crossplane platform foundations for IDP template"** with body referencing this plan + spec. Wait for review/merge.

### Task 1.6 — Verify Phase 1 in cluster

(Runs **after** PR merge.)

- [ ] **Step 1: Watch ArgoCD pick up the new apps**

```bash
kubectl -n argo-cd get applications crossplane-functions crossplane-compositions
# Expected (after sync): both Applications show STATUS=Healthy SYNC=Synced
```

- [ ] **Step 2: Confirm Function is installed and healthy**

```bash
kubectl get functions.pkg.crossplane.io function-python
# Expected: INSTALLED=True HEALTHY=True
```

- [ ] **Step 3: Confirm XRD and Composition are registered**

```bash
kubectl get xrd xapplications.platform.arigsela.com
# Expected: ESTABLISHED=True OFFERED=True

kubectl get composition application
# Expected: row returned, no error
```

- [ ] **Step 4: Confirm `kubectl explain` recognizes the new kind**

```bash
kubectl explain xapplication.spec
# Expected: lists fields image, host, port, replicas, env, dbNeeded, dbStorage
```

If any check fails — halt, inspect ArgoCD sync logs and Crossplane events, fix forward, do not start Phase 2.

---

## Phase 2 — Composition Python script (TDD) + in-cluster smoke

**What this phase delivers:** the actual Composition logic, written under TDD against committed golden YAML. Two `crossplane render` test cases pass end-to-end. Two manual `kubectl apply` smoke tests confirm the wire works in cluster (no Backstage involvement yet).

**Phase 2 exit criteria:**
- `tests/composition/render.sh xr-minimal` exits 0.
- `tests/composition/render.sh xr-with-db` exits 0.
- A manually-applied `XApplication` (no DB) produces a running Pod reachable via Ingress.
- A manually-applied `XApplication` (with DB) produces a CNPG Cluster + a Pod with `DATABASE_URL` env var.

### Task 2.1 — Write the failing test (no DB)

**Files:**
- Create: `tests/composition/xr-minimal.yaml`
- Create: `tests/composition/expected-xr-minimal.yaml`

- [ ] **Step 1: Create `tests/composition/xr-minimal.yaml`**

```yaml
# tests/composition/xr-minimal.yaml
# TDD input: smallest meaningful XApplication, no DB.
apiVersion: platform.arigsela.com/v1alpha1
kind: XApplication
metadata:
  name: smoke-app
  namespace: platform-smoke
spec:
  image: nginxinc/nginx-unprivileged:1.25-alpine
  host: smoke-app.arigsela.com
  port: 8080
  replicas: 1
  dbNeeded: false
```

- [ ] **Step 2: Create `tests/composition/expected-xr-minimal.yaml`**

The expected output of `crossplane render` for that input. Resources: Deployment + Service + Ingress, all in `platform-smoke` namespace. The Composition must produce this exactly.

```yaml
# tests/composition/expected-xr-minimal.yaml
# Sorted by yq -P 'sort_keys(..)'. Keep alphabetic key order.
---
apiVersion: platform.arigsela.com/v1alpha1
kind: XApplication
metadata:
  name: smoke-app
  namespace: platform-smoke
spec:
  dbNeeded: false
  host: smoke-app.arigsela.com
  image: nginxinc/nginx-unprivileged:1.25-alpine
  port: 8080
  replicas: 1
status:
  conditions: []
---
apiVersion: apps/v1
kind: Deployment
metadata:
  annotations:
    crossplane.io/composition-resource-name: deployment
  labels:
    app.kubernetes.io/managed-by: crossplane
    app.kubernetes.io/name: smoke-app
    backstage.io/kubernetes-id: smoke-app
  name: smoke-app
  namespace: platform-smoke
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: smoke-app
  template:
    metadata:
      labels:
        app.kubernetes.io/managed-by: crossplane
        app.kubernetes.io/name: smoke-app
        backstage.io/kubernetes-id: smoke-app
    spec:
      containers:
        - image: nginxinc/nginx-unprivileged:1.25-alpine
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8080
            initialDelaySeconds: 30
            periodSeconds: 30
          name: app
          ports:
            - containerPort: 8080
              name: http
          readinessProbe:
            httpGet:
              path: /healthz
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            limits:
              cpu: 500m
              memory: 512Mi
            requests:
              cpu: 100m
              memory: 128Mi
      imagePullSecrets:
        - name: ecr-auth
---
apiVersion: v1
kind: Service
metadata:
  annotations:
    crossplane.io/composition-resource-name: service
  labels:
    app.kubernetes.io/managed-by: crossplane
    app.kubernetes.io/name: smoke-app
    backstage.io/kubernetes-id: smoke-app
  name: smoke-app
  namespace: platform-smoke
spec:
  ports:
    - name: http
      port: 80
      targetPort: 8080
  selector:
    app.kubernetes.io/name: smoke-app
  type: ClusterIP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    crossplane.io/composition-resource-name: ingress
    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
  labels:
    app.kubernetes.io/managed-by: crossplane
    app.kubernetes.io/name: smoke-app
    backstage.io/kubernetes-id: smoke-app
  name: smoke-app
  namespace: platform-smoke
spec:
  ingressClassName: nginx
  rules:
    - host: smoke-app.arigsela.com
      http:
        paths:
          - backend:
              service:
                name: smoke-app
                port:
                  number: 80
            path: /
            pathType: Prefix
  tls:
    - hosts:
        - smoke-app.arigsela.com
      secretName: smoke-app-tls
```

- [ ] **Step 3: Verify the test fails (because Composition is empty)**

```bash
./tests/composition/render.sh xr-minimal
# Expected: non-zero exit, diff shows expected output but actual is just the XR with no children
```

- [ ] **Step 4: Commit the test**

```bash
git add tests/composition/xr-minimal.yaml tests/composition/expected-xr-minimal.yaml
git commit -m "test(composition): add failing render test for minimal XApplication"
```

### Task 2.2 — Implement the Composition Python (no-DB path)

**Files:**
- Modify: `base-apps/crossplane-compositions/composition-application.yaml` (replace the stub `script:` with the real Python).

- [ ] **Step 1: Replace the inline `script:` block with the implementation**

The full file becomes:

```yaml
apiVersion: apiextensions.crossplane.io/v1
kind: Composition
metadata:
  name: application
  annotations:
    argocd.argoproj.io/sync-wave: "3"
spec:
  compositeTypeRef:
    apiVersion: platform.arigsela.com/v1alpha1
    kind: XApplication
  mode: Pipeline
  pipeline:
    - step: render-resources
      functionRef:
        name: function-python
      input:
        apiVersion: python.fn.crossplane.io/v1beta1
        kind: Script
        script: |
          from crossplane.function import resource

          STD_LABELS_TEMPLATE = {
              "app.kubernetes.io/name": None,
              "app.kubernetes.io/managed-by": "crossplane",
              "backstage.io/kubernetes-id": None,
          }

          def std_labels(name):
              labels = dict(STD_LABELS_TEMPLATE)
              labels["app.kubernetes.io/name"] = name
              labels["backstage.io/kubernetes-id"] = name
              return labels

          def make_deployment(name, namespace, image, port, replicas, env_list, env_from_secret):
              container = {
                  "name": "app",
                  "image": image,
                  "ports": [{"name": "http", "containerPort": port}],
                  "resources": {
                      "requests": {"cpu": "100m", "memory": "128Mi"},
                      "limits":   {"cpu": "500m", "memory": "512Mi"},
                  },
                  "livenessProbe": {
                      "httpGet": {"path": "/healthz", "port": port},
                      "initialDelaySeconds": 30, "periodSeconds": 30,
                  },
                  "readinessProbe": {
                      "httpGet": {"path": "/healthz", "port": port},
                      "initialDelaySeconds": 5, "periodSeconds": 10,
                  },
              }
              if env_list:
                  container["env"] = [{"name": e["name"], "value": e["value"]} for e in env_list]
              if env_from_secret:
                  container.setdefault("env", []).append({
                      "name": "DATABASE_URL",
                      "valueFrom": {"secretKeyRef": {"name": env_from_secret, "key": "uri"}},
                  })
                  container["envFrom"] = [{"secretRef": {"name": env_from_secret}}]
              return {
                  "apiVersion": "apps/v1",
                  "kind": "Deployment",
                  "metadata": {"name": name, "namespace": namespace, "labels": std_labels(name)},
                  "spec": {
                      "replicas": replicas,
                      "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
                      "template": {
                          "metadata": {"labels": std_labels(name)},
                          "spec": {
                              "imagePullSecrets": [{"name": "ecr-auth"}],
                              "containers": [container],
                          },
                      },
                  },
              }

          def make_service(name, namespace, port):
              return {
                  "apiVersion": "v1",
                  "kind": "Service",
                  "metadata": {"name": name, "namespace": namespace, "labels": std_labels(name)},
                  "spec": {
                      "type": "ClusterIP",
                      "selector": {"app.kubernetes.io/name": name},
                      "ports": [{"name": "http", "port": 80, "targetPort": port}],
                  },
              }

          def make_ingress(name, namespace, host):
              return {
                  "apiVersion": "networking.k8s.io/v1",
                  "kind": "Ingress",
                  "metadata": {
                      "name": name, "namespace": namespace, "labels": std_labels(name),
                      "annotations": {
                          "cert-manager.io/cluster-issuer": "letsencrypt-prod",
                          "nginx.ingress.kubernetes.io/ssl-redirect": "true",
                          "nginx.ingress.kubernetes.io/force-ssl-redirect": "true",
                      },
                  },
                  "spec": {
                      "ingressClassName": "nginx",
                      "tls": [{"hosts": [host], "secretName": f"{name}-tls"}],
                      "rules": [{
                          "host": host,
                          "http": {"paths": [{
                              "path": "/", "pathType": "Prefix",
                              "backend": {"service": {"name": name, "port": {"number": 80}}},
                          }]},
                      }],
                  },
              }

          def compose(req, rsp):
              # struct_to_dict converts the protobuf Struct to a Python dict —
              # raw req.observed.composite.resource doesn't support .get(), and
              # int fields come back as floats (Struct stores numbers as doubles).
              xr = resource.struct_to_dict(req.observed.composite.resource)
              spec = xr["spec"]
              name = xr["metadata"]["name"]
              namespace = xr["metadata"]["namespace"]

              # Cast numeric fields back to int — K8s API server rejects
              # containerPort: 8080.0 at apply time even though render accepts it.
              port = int(spec["port"])
              replicas = int(spec.get("replicas", 2))

              env_from = None  # no DB in this version of the script

              resource.update(
                  rsp.desired.resources["deployment"],
                  make_deployment(name, namespace, spec["image"], port,
                                  replicas, spec.get("env", []), env_from),
              )
              resource.update(
                  rsp.desired.resources["service"],
                  make_service(name, namespace, port),
              )
              resource.update(
                  rsp.desired.resources["ingress"],
                  make_ingress(name, namespace, spec["host"]),
              )
```

- [ ] **Step 2: Run the failing test — should now pass**

```bash
./tests/composition/render.sh xr-minimal
# Expected: exit 0, no diff output
```

If diff output appears, inspect, edit script, re-run. The golden YAML in `expected-xr-minimal.yaml` is the source of truth — fix the Python until output matches.

- [ ] **Step 3: Commit**

```bash
git add base-apps/crossplane-compositions/composition-application.yaml
git commit -m "feat(crossplane): implement Composition (Deployment+Service+Ingress)"
```

### Task 2.3 — Add the with-DB test + extend the script

**Files:**
- Create: `tests/composition/xr-with-db.yaml`
- Create: `tests/composition/expected-xr-with-db.yaml`
- Modify: `base-apps/crossplane-compositions/composition-application.yaml` (extend `compose()` + add `make_cnpg_cluster()`)

- [ ] **Step 1: Create `tests/composition/xr-with-db.yaml`**

```yaml
apiVersion: platform.arigsela.com/v1alpha1
kind: XApplication
metadata:
  name: smoke-db-app
  namespace: platform-smoke
spec:
  image: nginxinc/nginx-unprivileged:1.25-alpine
  host: smoke-db-app.arigsela.com
  port: 8080
  replicas: 1
  dbNeeded: true
  dbStorage: 1Gi
```

- [ ] **Step 2: Create `tests/composition/expected-xr-with-db.yaml`**

Same structure as `expected-xr-minimal.yaml` but for `smoke-db-app`, with **two changes**:
1. Deployment container has `envFrom` for `smoke-db-app-db-app` Secret + `env: DATABASE_URL` from the same Secret.
2. Add a fourth child: a CNPG `Cluster` named `smoke-db-app-db`.

```yaml
---
apiVersion: platform.arigsela.com/v1alpha1
kind: XApplication
metadata:
  name: smoke-db-app
  namespace: platform-smoke
spec:
  dbNeeded: true
  dbStorage: 1Gi
  host: smoke-db-app.arigsela.com
  image: nginxinc/nginx-unprivileged:1.25-alpine
  port: 8080
  replicas: 1
status:
  conditions: []
---
apiVersion: apps/v1
kind: Deployment
metadata:
  annotations:
    crossplane.io/composition-resource-name: deployment
  labels:
    app.kubernetes.io/managed-by: crossplane
    app.kubernetes.io/name: smoke-db-app
    backstage.io/kubernetes-id: smoke-db-app
  name: smoke-db-app
  namespace: platform-smoke
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: smoke-db-app
  template:
    metadata:
      labels:
        app.kubernetes.io/managed-by: crossplane
        app.kubernetes.io/name: smoke-db-app
        backstage.io/kubernetes-id: smoke-db-app
    spec:
      containers:
        - env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  key: uri
                  name: smoke-db-app-db-app
          envFrom:
            - secretRef:
                name: smoke-db-app-db-app
          image: nginxinc/nginx-unprivileged:1.25-alpine
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8080
            initialDelaySeconds: 30
            periodSeconds: 30
          name: app
          ports:
            - containerPort: 8080
              name: http
          readinessProbe:
            httpGet:
              path: /healthz
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            limits:
              cpu: 500m
              memory: 512Mi
            requests:
              cpu: 100m
              memory: 128Mi
      imagePullSecrets:
        - name: ecr-auth
---
apiVersion: v1
kind: Service
metadata:
  annotations:
    crossplane.io/composition-resource-name: service
  labels:
    app.kubernetes.io/managed-by: crossplane
    app.kubernetes.io/name: smoke-db-app
    backstage.io/kubernetes-id: smoke-db-app
  name: smoke-db-app
  namespace: platform-smoke
spec:
  ports:
    - name: http
      port: 80
      targetPort: 8080
  selector:
    app.kubernetes.io/name: smoke-db-app
  type: ClusterIP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    crossplane.io/composition-resource-name: ingress
    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
  labels:
    app.kubernetes.io/managed-by: crossplane
    app.kubernetes.io/name: smoke-db-app
    backstage.io/kubernetes-id: smoke-db-app
  name: smoke-db-app
  namespace: platform-smoke
spec:
  ingressClassName: nginx
  rules:
    - host: smoke-db-app.arigsela.com
      http:
        paths:
          - backend:
              service:
                name: smoke-db-app
                port:
                  number: 80
            path: /
            pathType: Prefix
  tls:
    - hosts:
        - smoke-db-app.arigsela.com
      secretName: smoke-db-app-tls
---
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  annotations:
    crossplane.io/composition-resource-name: dbcluster
  labels:
    app.kubernetes.io/managed-by: crossplane
    app.kubernetes.io/name: smoke-db-app
    backstage.io/kubernetes-id: smoke-db-app
  name: smoke-db-app-db
  namespace: platform-smoke
spec:
  bootstrap:
    initdb:
      database: app
      owner: app
  imageName: ghcr.io/cloudnative-pg/postgresql:16
  instances: 1
  storage:
    size: 1Gi
```

- [ ] **Step 3: Verify the new test fails**

```bash
./tests/composition/render.sh xr-with-db
# Expected: non-zero exit; diff shows missing CNPG Cluster + missing envFrom/DATABASE_URL on Deployment
```

- [ ] **Step 4: Extend the Composition Python**

Edit `base-apps/crossplane-compositions/composition-application.yaml`. Two changes inside the `script:` block:

a) Add a `make_cnpg_cluster` helper above `compose()`. It takes the **parent app name** (not the cluster name) and derives the cluster name internally — this keeps the `app.kubernetes.io/name` label faithful to the parent and avoids string-replace bugs on names that themselves contain `-db`:

```python
def make_cnpg_cluster(parent_name, namespace, instances, storage):
    return {
        "apiVersion": "postgresql.cnpg.io/v1",
        "kind": "Cluster",
        "metadata": {
            "name": f"{parent_name}-db",
            "namespace": namespace,
            "labels": std_labels(parent_name),
        },
        "spec": {
            "instances": instances,
            "imageName": "ghcr.io/cloudnative-pg/postgresql:16",
            "bootstrap": {"initdb": {"database": "app", "owner": "app"}},
            "storage": {"size": storage},
        },
    }
```

b) Update `compose()` to use the DB. Note `make_cnpg_cluster(name, ...)` passes the **parent** name; the helper derives `<parent>-db` internally:

```python
def compose(req, rsp):
    xr = resource.struct_to_dict(req.observed.composite.resource)
    spec = xr["spec"]
    name = xr["metadata"]["name"]
    namespace = xr["metadata"]["namespace"]

    port = int(spec["port"])
    replicas = int(spec.get("replicas", 2))

    db_secret = f"{name}-db-app" if spec.get("dbNeeded") else None
    env_from  = db_secret

    resource.update(rsp.desired.resources["deployment"],
        make_deployment(name, namespace, spec["image"], port,
                        replicas, spec.get("env", []), env_from))
    resource.update(rsp.desired.resources["service"],
        make_service(name, namespace, port))
    resource.update(rsp.desired.resources["ingress"],
        make_ingress(name, namespace, spec["host"]))

    if spec.get("dbNeeded"):
        resource.update(rsp.desired.resources["dbcluster"],
            make_cnpg_cluster(name, namespace,
                              instances=1, storage=spec.get("dbStorage", "1Gi")))
```

- [ ] **Step 5: Run both tests, confirm both pass**

```bash
./tests/composition/render.sh xr-minimal
# Expected: exit 0
./tests/composition/render.sh xr-with-db
# Expected: exit 0
```

- [ ] **Step 6: Commit**

```bash
git add base-apps/crossplane-compositions/composition-application.yaml \
        tests/composition/xr-with-db.yaml \
        tests/composition/expected-xr-with-db.yaml
git commit -m "feat(crossplane): Composition supports CNPG database (dbNeeded path)"
```

### Task 2.4 — Open + merge Phase 2 PR

- [ ] **Step 1: Push and open PR**

```bash
git push
gh pr edit  # if PR exists, otherwise:
# gh pr create --title "Phase 2: Composition Python script (TDD) for IDP template" \
#   --body "Implements the Application Composition under TDD against committed golden YAML. Adds two render tests. No XR is applied to the cluster in this PR; smoke tests in Task 2.5 happen post-merge." --base main
```

Wait for review/merge.

### Task 2.5 — In-cluster smoke (manual XR apply, no Backstage yet)

**Runs after the Phase 2 PR is merged and ArgoCD has synced the new Composition into the cluster.**

- [ ] **Step 1: Confirm Composition is updated in cluster**

```bash
kubectl get composition application -o jsonpath='{.spec.pipeline[0].input.script}' | head -5
# Expected: starts with "from crossplane.function import resource"
# (NOT the Phase-1 stub "PHASE 1 STUB")
```

- [ ] **Step 2: Create `platform-smoke` namespace**

```bash
kubectl create namespace platform-smoke
# Expected: "namespace/platform-smoke created" (or "AlreadyExists" — fine)
```

- [ ] **Step 3: Apply the no-DB XR**

```bash
kubectl apply -f tests/composition/xr-minimal.yaml
# Expected: "xapplication.platform.arigsela.com/smoke-app created"
```

- [ ] **Step 4: Watch the children get created**

```bash
kubectl -n platform-smoke get deploy,svc,ingress -l app.kubernetes.io/name=smoke-app
# Expected (after ~30s): one of each
kubectl -n platform-smoke get pods -l app.kubernetes.io/name=smoke-app
# Expected: pod Running
```

- [ ] **Step 5: Verify Ingress/cert** (DNS for `smoke-app.arigsela.com` must resolve to the cluster — if you don't want to set up DNS for a smoke test, skip the curl and only check the Ingress object exists)

```bash
kubectl -n platform-smoke get cert smoke-app-tls
# Expected: READY=True (may take 1-2 min for cert-manager to complete)
curl -sI https://smoke-app.arigsela.com/ 2>/dev/null | head -1 || echo "skipped — DNS not configured"
```

- [ ] **Step 6: Apply the with-DB XR**

```bash
kubectl apply -f tests/composition/xr-with-db.yaml
# Expected: "xapplication.platform.arigsela.com/smoke-db-app created"
```

- [ ] **Step 7: Watch the CNPG Cluster bootstrap**

```bash
kubectl -n platform-smoke get cluster smoke-db-app-db -w
# Expected: phase progresses through "Setting up primary" → "Cluster in healthy state"
# Ctrl-C when healthy
```

- [ ] **Step 8: Verify the app Pod has DATABASE_URL**

```bash
kubectl -n platform-smoke exec deploy/smoke-db-app -- printenv DATABASE_URL
# Expected: a postgres URI like postgresql://app:***@smoke-db-app-db-rw.platform-smoke.svc:5432/app
```

- [ ] **Step 9: Tear down both XRs**

```bash
kubectl delete -f tests/composition/xr-minimal.yaml
kubectl delete -f tests/composition/xr-with-db.yaml
# CNPG does NOT delete the PVC by default; left in place per spec §8.
kubectl -n platform-smoke get pvc
# Expected: a PVC for smoke-db-app-db remains; manually delete if you want a fully clean namespace.
```

If any check fails, halt — the Composition is broken in cluster, fix forward before Phase 3.

---

## Phase 3 — Backstage template + image rebuild

**What this phase delivers:** the new Software Template visible in the Backstage UI. The template content is committed to `arigsela/backstage`, app-config is updated, and the user rebuilds + pushes the image. We bump the deployed tag in `arigsela/kubernetes` (this repo) to flip cluster traffic to the new image.

**Phase 3 exit criteria:**
- Backstage UI `/create` page shows "Application (Crossplane)" template alongside "CrewAI Multi-Agent Project."
- Selecting it shows the three-step form (Identity / Workload / Database).
- Local scaffolder dry-run produces files matching expected XR / ArgoCD App YAML.

**Note on responsibilities:** the user has stated in CLAUDE.md "I will handle running the builds of images." Tasks below split clearly: I produce the changes in `arigsela/backstage` (no docker build); the user runs the build + push; then I bump the tag in `arigsela/kubernetes`.

### Task 3.1 — Create the Backstage template

Working directory: `/Users/arisela/git/kubernetes/docs/reference/backstage` (the cloned `arigsela/backstage` repo).

**Files (in `arigsela/backstage`):**
- Create: `examples/templates/application/template.yaml`
- Create: `examples/templates/application/README.md`

- [ ] **Step 1: Create a feature branch in the Backstage repo**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
git checkout -b feat/application-template
```

- [ ] **Step 2: Create the template directory and `template.yaml`**

```bash
mkdir -p examples/templates/application/content-k8s
```

Then write `examples/templates/application/template.yaml`:

```yaml
# examples/templates/application/template.yaml
# Application (Crossplane) — onboard an existing container image as a managed
# app via Crossplane Composition. Single-repo: only opens a PR to
# arigsela/kubernetes; does not create a source-code repo.
# Companion design doc: arigsela/kubernetes:docs/superpowers/specs/2026-04-28-backstage-crossplane-idp-design.md
apiVersion: scaffolder.backstage.io/v1beta3
kind: Template
metadata:
  name: application-template
  title: Application (Crossplane)
  description: >-
    Onboard an existing container image as a managed application. Provisions
    Deployment + Service + Ingress and an optional CloudNativePG database
    via a Crossplane v2 Composition.
  tags:
    - crossplane
    - kubernetes
    - recommended
spec:
  owner: group:platform-engineering
  type: service

  parameters:
    - title: Identity
      required: [name, owner]
      properties:
        name:
          type: string
          title: Application name
          description: Lowercase, hyphenated. Used as namespace and resource prefix.
          pattern: "^[a-z][a-z0-9-]{2,38}[a-z0-9]$"
        description:
          type: string
          title: Description
          description: One-line summary shown in the Backstage catalog.
        owner:
          type: string
          title: Owner
          ui:field: OwnerPicker
          ui:options:
            catalogFilter:
              kind: [Group, User]

    - title: Workload
      required: [image, host, port]
      properties:
        image:
          type: string
          title: Container image (full ref including tag)
          description: e.g. 852893458518.dkr.ecr.us-east-2.amazonaws.com/my-app:1.0.0
        host:
          type: string
          title: Public hostname
          description: e.g. my-app.arigsela.com (DNS must already point at the cluster)
        port:
          type: integer
          title: Container port
          default: 8080
        replicas:
          type: integer
          title: Replicas
          default: 2
          minimum: 1
          maximum: 10

    - title: Database (optional)
      properties:
        dbNeeded:
          type: boolean
          title: Provision a Postgres database (CloudNativePG)
          default: false
        dbStorage:
          type: string
          title: Database storage size
          default: "1Gi"

  steps:
    - id: fetch
      name: Render manifests
      action: fetch:template
      input:
        url: ./content-k8s
        values:
          name: ${{ parameters.name }}
          namespace: ${{ parameters.name }}
          description: ${{ parameters.description }}
          owner: ${{ parameters.owner }}
          image: ${{ parameters.image }}
          host: ${{ parameters.host }}
          port: ${{ parameters.port }}
          replicas: ${{ parameters.replicas }}
          dbNeeded: ${{ parameters.dbNeeded }}
          dbStorage: ${{ parameters.dbStorage }}

    - id: publish
      name: Open PR to arigsela/kubernetes
      action: publish:github:pull-request
      input:
        repoUrl: github.com?repo=kubernetes&owner=arigsela
        branchName: scaffold/${{ parameters.name }}
        title: "feat(${{ parameters.name }}): onboard via Crossplane Application"
        description: |
          Generated by Backstage `application-template`.
          Owner: ${{ parameters.owner }}
          Database: ${{ parameters.dbNeeded }}
        targetPath: ""

    - id: register
      name: Register in catalog
      action: catalog:register
      input:
        repoContentsUrl: ${{ steps.publish.output.repoContentsUrl }}
        catalogInfoPath: /base-apps/${{ parameters.name }}/catalog-info.yaml

  output:
    links:
      - title: Pull request
        url: ${{ steps.publish.output.remoteUrl }}
      - title: Catalog entry
        icon: catalog
        entityRef: ${{ steps.register.output.entityRef }}
```

- [ ] **Step 3: Write `examples/templates/application/README.md`**

```markdown
# application-template

Backstage Software Template that onboards an existing container image as a
managed Kubernetes application. Provisions Deployment + Service + Ingress and
an optional CloudNativePG Postgres via the `XApplication` Crossplane Composition.

## What it does

1. Renders three Nunjucks-templated YAML files into `base-apps/<name>/` and
   one ArgoCD `Application` at `base-apps/<name>.yaml`.
2. Opens a PR against `arigsela/kubernetes`.
3. Registers a `catalog-info.yaml` Location in the Backstage catalog so the
   new app appears as a Component immediately.

## Inputs

| Field | Required | Default | Notes |
|---|---|---|---|
| name | yes | — | Lowercase, hyphenated; becomes namespace + resource prefix |
| owner | yes | — | Backstage Group or User |
| description | no | — | One-line summary |
| image | yes | — | Full container image ref including tag |
| host | yes | — | Public FQDN; DNS must already resolve to the cluster |
| port | no | 8080 | Container port |
| replicas | no | 2 | 1–10 |
| dbNeeded | no | false | Provision a CNPG `Cluster` |
| dbStorage | no | 1Gi | PVC size for the CNPG cluster |

## Companion docs

- Design: `arigsela/kubernetes:docs/superpowers/specs/2026-04-28-backstage-crossplane-idp-design.md`
- Plan: `arigsela/kubernetes:docs/superpowers/plans/2026-04-28-backstage-crossplane-idp.md`
```

- [ ] **Step 4: Commit**

```bash
git add examples/templates/application/template.yaml examples/templates/application/README.md
git commit -m "feat(template): add application-template skeleton"
```

### Task 3.2 — Write the three content files (Nunjucks)

**Files (in `arigsela/backstage`):**
- Create: `examples/templates/application/content-k8s/catalog-info.yaml`
- Create: `examples/templates/application/content-k8s/application-xr.yaml`
- Create: `examples/templates/application/content-k8s/argocd-application.yaml`

> The CrewAI template uses Nunjucks with `${{ values.foo }}` substitutions and `{% raw %}...{% endraw %}` blocks for any literal `{ }`. The files below are pure YAML with no literal braces, so no `{% raw %}` is needed.

- [ ] **Step 1: `catalog-info.yaml`**

This file ends up at `base-apps/<name>/catalog-info.yaml` in `arigsela/kubernetes`.

```yaml
# Backstage Component for ${{ values.name }}
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: ${{ values.name }}
  description: ${{ values.description | default("Managed via XApplication Composition") }}
  annotations:
    github.com/project-slug: arigsela/kubernetes
    backstage.io/kubernetes-id: ${{ values.name }}
  tags:
    - crossplane
    - xapplication
spec:
  type: service
  lifecycle: experimental
  owner: ${{ values.owner }}
  system: platform
```

- [ ] **Step 2: `application-xr.yaml`**

This file ends up at `base-apps/<name>/application-xr.yaml`.

```yaml
# XApplication (Crossplane) for ${{ values.name }}
apiVersion: platform.arigsela.com/v1alpha1
kind: XApplication
metadata:
  name: ${{ values.name }}
  namespace: ${{ values.namespace }}
spec:
  image: ${{ values.image }}
  host: ${{ values.host }}
  port: ${{ values.port }}
  replicas: ${{ values.replicas }}
  dbNeeded: ${{ values.dbNeeded }}
  dbStorage: ${{ values.dbStorage }}
```

- [ ] **Step 3: `argocd-application.yaml`**

This file ends up at `base-apps/<name>.yaml` (top level — picked up by master-app).

```yaml
# ArgoCD Application for ${{ values.name }}
# Sync wave 10 ensures XRD/Composition land before this app's XR.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: ${{ values.name }}
  namespace: argo-cd
  annotations:
    argocd.argoproj.io/sync-wave: "10"
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/${{ values.name }}
  destination:
    server: https://kubernetes.default.svc
    namespace: ${{ values.namespace }}
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [ ] **Step 4: Commit**

```bash
git add examples/templates/application/content-k8s/
git commit -m "feat(template): add Nunjucks content for application-template"
```

### Task 3.3 — Register the template in `app-config.yaml`

**Files (in `arigsela/backstage`):**
- Modify: `app-config.yaml` — add one entry under `catalog.locations`.

- [ ] **Step 1: Locate the existing CrewAI registration as the pattern reference**

```bash
grep -n "crewai-agent" /Users/arisela/git/kubernetes/docs/reference/backstage/app-config.yaml
# Expected: line 228 — "target: ../../examples/templates/crewai-agent/template.yaml"
```

- [ ] **Step 2: Add the new template entry directly below the crewai-agent block**

Edit `app-config.yaml`. Insert (preserving 4-space YAML indent — match the surrounding entries):

```yaml
    # Application (Crossplane) — onboard a container image as a managed app.
    # See: examples/templates/application/template.yaml
    - type: file
      target: ../../examples/templates/application/template.yaml
      rules:
        - allow: [Template]
```

The block goes between the existing `crewai-agent` `Template` location (around line 228) and the `org.yaml` location.

- [ ] **Step 3: Commit**

```bash
git add app-config.yaml
git commit -m "feat(config): register application-template under catalog.locations"
```

### Task 3.4 — Local scaffolder dry-run

**Goal:** Verify the template renders and produces correct YAML before asking the user to rebuild the production image. Runs `yarn dev` against a sandbox GitHub repo.

- [ ] **Step 1: Confirm `app-config.local.yaml` exists with valid local secrets**

```bash
test -f app-config.local.yaml && echo "ok" || echo "MISSING — see backstage README"
```

If missing, the existing repo has docs on creating one for local dev (`README.md`). The local config needs `GITHUB_TOKEN`, GitHub OAuth client, and Postgres connection. This is a one-time setup; reuse the values from prior CrewAI-template development.

- [ ] **Step 2: Use a sandbox kubernetes-style repo to avoid PR noise on `arigsela/kubernetes`**

Create (or reuse) `arigsela/scaffolder-sandbox`. Update the template's `repoUrl` in `template.yaml` **temporarily**:

```diff
- repoUrl: github.com?repo=kubernetes&owner=arigsela
+ repoUrl: github.com?repo=scaffolder-sandbox&owner=arigsela
```

(Do NOT commit this change — revert before Step 6.)

- [ ] **Step 3: Start Backstage locally**

```bash
yarn install --immutable
yarn dev
# Expected: backend on :7007, frontend on :3000 — both compile clean
```

- [ ] **Step 4: Walk the wizard**

Open <http://localhost:3000/create>. Click "Application (Crossplane)". Fill:
- name: `dryrun-app`
- owner: any catalog Group
- image: `nginx:1.25`
- host: `dryrun-app.arigsela.com`
- port: `8080`
- replicas: `1`
- dbNeeded: `false`

Click Create. Wait for steps to complete.

- [ ] **Step 5: Verify the PR + content**

Check <https://github.com/arigsela/scaffolder-sandbox/pulls> — a PR `scaffold/dryrun-app` should exist. Inspect:
- `base-apps/dryrun-app/catalog-info.yaml` — Component entity for `dryrun-app`.
- `base-apps/dryrun-app/application-xr.yaml` — XApplication with the form values.
- `base-apps/dryrun-app.yaml` — ArgoCD Application, sync-wave 10.

Optionally, **diff the rendered XR against `tests/composition/xr-minimal.yaml`** structure (different name/host but same shape) — mismatches indicate a bug in the Nunjucks templates.

- [ ] **Step 6: Repeat with `dbNeeded: true`** to verify boolean and `dbStorage` plumb through. Close both PRs, delete the branches.

- [ ] **Step 7: Revert the `repoUrl` change in `template.yaml`** so the production template targets `arigsela/kubernetes`.

```bash
git diff examples/templates/application/template.yaml
# Expected: empty (the diff was uncommitted, so just discard via re-edit)
```

- [ ] **Step 8: Stop `yarn dev` (Ctrl-C). Commit nothing — Step 7 reverted the only change.**

### Task 3.5 — Hand off to user: image rebuild + push

**This task is for the user to perform.** Per CLAUDE.md, image builds are the user's responsibility. The plan documents the inputs and acceptance criteria.

- [ ] **Step 1: User opens PR on `arigsela/backstage` `feat/application-template` branch**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
git push -u origin feat/application-template
gh pr create --title "feat: add application-template (Crossplane IDP)" \
  --body "Adds a new Backstage Software Template that onboards a container image as a managed Crossplane XApplication. See companion design + plan in arigsela/kubernetes:docs/superpowers/."
```

- [ ] **Step 2: User merges the PR to `main`**

- [ ] **Step 3: User rebuilds and pushes the image**

> **The user runs the docker build + push.** The plan does not run this. Inputs:
> - Source branch: `arigsela/backstage:main` (post-merge)
> - Target image tag: `v1.1.0` (next minor after `v1.0.1`)
> - Target registry: `852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal`
>
> Acceptance: image exists in ECR with tag `v1.1.0` and the `application-template/` directory baked in.

### Task 3.6 — Bump the deployed tag in `arigsela/kubernetes`

(Runs **after** Task 3.5 produces the new image.)

**Files:**
- Modify: `base-apps/backstage/deployments.yaml:27` — image tag `v1.0.1` → `v1.1.0`.

- [ ] **Step 1: Edit the tag**

```bash
cd /Users/arisela/git/kubernetes
# Stay on the same feature branch (feat/crossplane-idp-application-template)
```

In `base-apps/backstage/deployments.yaml`, line 27:

```diff
-        image: 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.0.1
+        image: 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.1.0
```

- [ ] **Step 2: Commit + push**

```bash
git add base-apps/backstage/deployments.yaml
git commit -m "chore(backstage): bump image to v1.1.0 (application-template)"
git push
```

- [ ] **Step 3: Open Phase 3 PR**

```bash
gh pr create --title "Phase 3: Backstage application-template image bump" \
  --body "Bumps backstage-portal to v1.1.0 which contains the new application-template. See arigsela/backstage PR feat/application-template (already merged)." \
  --base main
```

- [ ] **Step 4: After PR merge, verify Backstage UI**

```bash
kubectl -n backstage get pod -l app=backstage -o jsonpath='{.items[0].spec.containers[0].image}{"\n"}'
# Expected: ...:v1.1.0
```

Open <https://backstage.arigsela.com/create>. Confirm the "Application (Crossplane)" card is visible alongside "CrewAI Multi-Agent Project."

If absent: check `kubectl logs deploy/backstage -n backstage` for `app-config.yaml` parse errors or `catalog.locations` failures.

---

## Phase 4 — End-to-end via Backstage UI + regression

**What this phase delivers:** the canonical demo path. A real onboarding through the live Backstage UI produces a running app in cluster. Plus a regression check that the existing CrewAI template still works.

**Phase 4 exit criteria:**
- An app named `helloapp` (or similar throwaway name) onboarded via the Backstage UI is running in cluster, reachable on its hostname, with a CNPG cluster.
- The Backstage entity page for that app shows live pod status under the "Kubernetes" tab.
- A scaffolded CrewAI agent test run (using the existing CrewAI template) still succeeds (regression).
- Both demo apps are torn down by deleting their `base-apps/<name>/` and `base-apps/<name>.yaml` files via PR.

### Task 4.1 — End-to-end: scaffold a real app (no DB)

- [ ] **Step 1: Open Backstage `/create`** and select **Application (Crossplane)**.

- [ ] **Step 2: Fill the form**

- name: `helloapp`
- owner: a Group entity (e.g. `group:platform-engineering`)
- image: `nginxinc/nginx-unprivileged:1.25-alpine`
- host: `helloapp.arigsela.com` (configure DNS first if not already wildcarded)
- port: `8080`
- replicas: `1`
- dbNeeded: `false`

Click Create.

- [ ] **Step 3: Verify the PR**

A PR `scaffold/helloapp` is opened against `arigsela/kubernetes`. Review the diff:
- `base-apps/helloapp/catalog-info.yaml`
- `base-apps/helloapp/application-xr.yaml`
- `base-apps/helloapp.yaml`

- [ ] **Step 4: Merge the PR**

- [ ] **Step 5: Watch ArgoCD pick up the new Application**

```bash
kubectl -n argo-cd get application helloapp -w
# Expected: SYNC=Synced HEALTH=Healthy within ~30s
```

- [ ] **Step 6: Watch the children get created**

```bash
kubectl -n helloapp get xapplication
# Expected: helloapp XApplication, SYNCED=True READY=True

kubectl -n helloapp get deploy,svc,ingress,pod
# Expected: 1 Deployment, 1 Service, 1 Ingress, 1+ Pod Running
```

- [ ] **Step 7: Verify Backstage K8s tab**

Open <https://backstage.arigsela.com/catalog/default/component/helloapp/kubernetes>.
- Expected: shows the helloapp Deployment, Pod, and Service from the homelab cluster.
- The data is sourced via the `backstage.io/kubernetes-id: helloapp` label that the Composition stamps.

- [ ] **Step 8: Verify Ingress + cert**

```bash
curl -sI https://helloapp.arigsela.com/ | head -1
# Expected: 200 OK (or 404 — nginx default — but TLS handshake must succeed)
```

### Task 4.2 — End-to-end: scaffold an app with DB

- [ ] **Step 1: Run the wizard again**

- name: `dbtestapp`
- image: same nginx
- host: `dbtestapp.arigsela.com`
- dbNeeded: `true`
- dbStorage: `1Gi`

- [ ] **Step 2: Merge the resulting PR**

- [ ] **Step 3: Watch the CNPG Cluster bootstrap**

```bash
kubectl -n dbtestapp get cluster dbtestapp-db -w
# Expected: phase progresses to "Cluster in healthy state" (~1-2 min)
```

- [ ] **Step 4: Verify DATABASE_URL plumbing**

```bash
kubectl -n dbtestapp exec deploy/dbtestapp -- printenv DATABASE_URL
# Expected: postgresql://app:***@dbtestapp-db-rw.dbtestapp.svc:5432/app
```

### Task 4.3 — Regression: CrewAI template still works

- [ ] **Step 1: Open Backstage `/create`** and select **CrewAI Multi-Agent Project**.

- [ ] **Step 2: Walk the existing wizard with throwaway values** (do not merge the resulting PR — close it). Ensure the form still loads and the scaffolder steps still complete.

- [ ] **Step 3: Document the result** in the PR description for Phase 4.

If the CrewAI template is broken — halt. Likely root causes: `app-config.yaml` indentation regression (Task 3.3), or a yarn workspace issue from the image rebuild.

### Task 4.4 — Tear down the demo apps

- [ ] **Step 1: Open a single PR removing the two demo apps**

```bash
git checkout -b chore/teardown-demo-apps
git rm base-apps/helloapp.yaml base-apps/dbtestapp.yaml
git rm -r base-apps/helloapp base-apps/dbtestapp
git commit -m "chore: remove e2e smoke apps after acceptance"
git push -u origin chore/teardown-demo-apps
gh pr create --title "Teardown e2e smoke apps (helloapp, dbtestapp)" \
  --body "Removes the two demo apps scaffolded during the IDP template e2e validation. Documented in the spec §8 as the standard decommission path."
```

- [ ] **Step 2: Merge; ArgoCD prunes the apps; Crossplane reaps the children.**

- [ ] **Step 3: Manually clean up CNPG PVCs** (per spec §8 — CNPG does not auto-delete):

```bash
kubectl -n dbtestapp delete pvc --all
kubectl delete namespace helloapp dbtestapp
```

### Task 4.5 — Update the plan with run notes + close out

- [ ] **Step 1: Append a `## Run Notes` section to this plan file** capturing:
  - Date the e2e ran.
  - Any deviations from the plan (versions used, command tweaks, etc.).
  - Anything surprising worth carrying into the v2 spec.

- [ ] **Step 2: Commit the run notes** as a final small PR.

```bash
git add docs/superpowers/plans/2026-04-28-backstage-crossplane-idp.md
git commit -m "docs(plan): add Phase 4 run notes for IDP template"
git push
gh pr create --title "Plan run notes: Backstage + Crossplane IDP" --base main
```

---

## Self-review

**Spec coverage check:**

| Spec section | Plan task |
|---|---|
| §3 Architecture wire | Phase 1 + Phase 4 (full e2e) |
| §4 Components | Tasks 1.1–1.5, 3.1–3.3, 3.5–3.6 |
| §5 XApplication XRD | Task 1.2 |
| §6 Composition + Python | Tasks 1.3, 2.2, 2.3 |
| §6.2 (A) DB credential injection | Task 2.3 (the `env_from` + `DATABASE_URL` lines in the script) |
| §6.2 (B) CNPG defaults | Task 2.3 (`make_cnpg_cluster` helper) |
| §6.2 (C) Image pull secret | Task 2.2 (hardcoded `ecr-auth`) |
| §6.2 (D) Standard labels | Task 2.2 (`std_labels` helper) |
| §7 Backstage Template + content | Tasks 3.1–3.3 |
| §8 Failure modes | No dedicated task — surfaced as "halt and resolve" instructions throughout |
| §9 Layer 1 testing | Task 1.1 (harness), 2.1 + 2.3 (test cases), 2.2 + 2.3 (Composition implementation) |
| §9 Layer 2 testing | Task 3.4 (scaffolder dry-run) |
| §9 Layer 3 testing | Task 2.5 (manual XR), Tasks 4.1–4.2 (full e2e) |
| §11 Acceptance criteria | All checked off in Phase 4 |
| §12 Open follow-ups | Task 1.1 Step 1 verification note + plan-author note in Phase 1 |

**No placeholders:** every step contains the actual command or YAML. Two version-sensitive items (`function-python:v0.4.0`) are flagged with a verification note rather than left as TBD.

**Type / name consistency:** spot-checked — `XApplication` API group/kind, `<name>-db` / `<name>-db-app` Secret naming, `app.kubernetes.io/name` / `backstage.io/kubernetes-id` labels are consistent across XRD, Composition Python, golden YAML, and Nunjucks content.

**Branch / PR strategy:** four PRs (one per phase) plus the small Backstage repo PR in Phase 3 plus the teardown PR in Phase 4. Six PRs total. Each phase is independently revertable.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-28-backstage-crossplane-idp.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
