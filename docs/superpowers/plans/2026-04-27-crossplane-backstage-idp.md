# Crossplane v2 + Backstage IDP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a 4-PR self-service IDP — TeraSky Backstage Crossplane plugins → HomelabApp v1.0 Slim (Namespace + ArgoCD App) → Useful (adds ECR + Kyverno auto-auth) → Full-fat (conditional S3 + Postgres). End state: a Backstage Software Template emits a `HomelabApp` claim PR; ArgoCD applies it; Crossplane composes the resources via `function-python`; TeraSky plugin renders the resource graph in the catalog.

**Architecture:** XRD `HomelabApp` (group `platform.asela.io`, scope `Namespaced`, additive evolution `v1alpha1 → v1alpha2 → v1alpha3` over PR-B/C/D). Composition is a single-function pipeline using `function-python`. The function lives in a new in-repo directory (`base-apps/homelab-app-platform/function-source/`) and is published as an OCI image to ECR per PR. Backstage source-repo work happens in parallel — each PR includes the diff to apply there.

**Tech Stack:** Crossplane v2.2.1, Upbound AWS providers v2.5.3 (+ `provider-aws-ecr` added in PR-C), `function-python` SDK (`crossplane-function-sdk-python`), Backstage v1.0.1 (custom ECR image), TeraSky Backstage Crossplane plugins, Python 3.11+, pytest, ArgoCD master-app pattern, Kyverno ECR-auth policy (existing).

**Source spec:** `docs/superpowers/specs/2026-04-27-crossplane-backstage-idp-design.md`

---

## File Map

### New files in this repo (`arigsela/kubernetes`)

| Path | Phase added | Purpose |
|---|---|---|
| `base-apps/homelab-app-platform/xrd.yaml` | PR-B | `CompositeResourceDefinition` for `HomelabApp` |
| `base-apps/homelab-app-platform/composition.yaml` | PR-B | `Composition` referencing the function pipeline |
| `base-apps/homelab-app-platform/function.yaml` | PR-B | `Function` resource (OCI ref) |
| `base-apps/homelab-app-platform/function-source/Dockerfile` | PR-B | Function image build |
| `base-apps/homelab-app-platform/function-source/requirements.txt` | PR-B | Python deps |
| `base-apps/homelab-app-platform/function-source/crossplane.yaml` | PR-B | Function package metadata |
| `base-apps/homelab-app-platform/function-source/main.py` | PR-B (Slim), updated PR-C/D | Composition function entry + resource builders |
| `base-apps/homelab-app-platform/function-source/tests/test_compose.py` | PR-B (Slim), updated PR-C/D | pytest suite for the function |
| `base-apps/homelab-app-platform/function-source/Makefile` | PR-B | `make build push` shortcuts |
| `base-apps/homelab-app-platform.yaml` | PR-B | ArgoCD Application wiring the directory into master-app |

### Modified files in this repo

| Path | Phase | Change |
|---|---|---|
| `base-apps/backstage/configmaps.yaml` | PR-A | Add `KUBERNETES_API_URL` env |
| `base-apps/backstage/rbac.yaml` | PR-A | New `ClusterRole` + `ClusterRoleBinding` (read-only) |
| `base-apps/backstage/deployments.yaml` | PR-A, PR-B, PR-C, PR-D | Image tag bumps |
| `base-apps/crossplane-aws-provider/provider.yaml` | PR-C | Add `provider-aws-ecr:v2.5.3` Provider |
| `base-apps/homelab-app-platform/xrd.yaml` | PR-C, PR-D | Additive `v1alpha2`, `v1alpha3` versions |
| `base-apps/homelab-app-platform/function.yaml` | PR-C, PR-D | Bump function image tag |
| `base-apps/homelab-app-platform/function-source/main.py` | PR-C, PR-D | Add ECR builder, S3/Postgres bundles, conditional dispatch |
| `base-apps/homelab-app-platform/function-source/tests/test_compose.py` | PR-C, PR-D | Tests for new branches |

### Backstage source repo (separate; user applies + builds image)

| Path | Phase | Change |
|---|---|---|
| `package.json` | PR-A | Add three TeraSky plugin packages |
| `packages/app/src/App.tsx` | PR-A | Register Crossplane resources frontend route + EntityPage tab |
| `packages/backend/src/index.ts` | PR-A | Register Crossplane permissions backend |
| `templates/new-homelab-app/template.yaml` | PR-B (created), PR-C/D (extended) | Software Template with form fields |
| `templates/new-homelab-app/skeleton/{{ values.name }}-claim.yaml` | PR-B (created), PR-C/D (extended) | Nunjucks-rendered claim |
| `app-config.yaml` | PR-B | Register the new template under `catalog.locations` |

---

## Pre-flight (one-time, before Task A1)

Confirm assumptions before any branch creation.

```bash
# In the kubernetes repo
cd /Users/arisela/git/kubernetes
git checkout main && git pull --ff-only origin main
kubectl get providers.pkg.crossplane.io
# Expected: provider-aws-s3, provider-aws-iam, upbound-provider-family-aws all v2.5.3, HEALTHY=True
kubectl -n crossplane-system get deploy crossplane -o jsonpath='{.spec.template.spec.containers[0].image}'
# Expected: xpkg.crossplane.io/crossplane/crossplane:v2.2.1

# Confirm Backstage portal is up
kubectl -n backstage get deploy backstage -o jsonpath='{.spec.template.spec.containers[0].image}'
# Expected: 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.0.1

# Confirm you have access to the Backstage source repo (separate path on disk)
# Set this env var to the path for use in later tasks:
export BACKSTAGE_SRC=/path/to/your/backstage/source/repo
ls "$BACKSTAGE_SRC/packages/app/src/App.tsx"
# Expected: file exists. If not, halt — locate or clone the source repo first.
```

If any check fails, halt and resolve before proceeding.

---

# Phase A — PR-A: TeraSky Backstage Crossplane plugins

Goal: Existing Crossplane MRs visible in Backstage catalog with graph + YAML viewer.

---

## Task A1: Backstage upstream version compatibility check

**Files:** none (read-only inspection)

- [ ] **Step A1.1: Read the portal's Backstage upstream version**

```bash
cd "$BACKSTAGE_SRC"
grep -E '"@backstage/core-app-api"|"@backstage/core-plugin-api"' package.json | head -2
```

Expected: shows the installed `@backstage/*` versions. Record the version.

- [ ] **Step A1.2: Decide on the TeraSky plugin compatibility tier**

TeraSky plugins target Backstage `>=1.30.0` (last verified `1.40.x`). Compare your portal's version:

- If portal Backstage version is **`>= 1.30.0`**: proceed.
- If portal is **older than 1.30.0**: stop — upgrade the portal Backstage version first (separate side quest, out of scope here). Document the gap in your scratch notes and return to this plan after upgrading.

Report the decision in your scratch notes (this affects Task A2 plugin version pin).

---

## Task A2: Add TeraSky plugins to Backstage source repo

**Files:**
- Modify: `$BACKSTAGE_SRC/package.json`
- Modify: `$BACKSTAGE_SRC/packages/app/package.json`
- Modify: `$BACKSTAGE_SRC/packages/backend/package.json`

- [ ] **Step A2.1: Add frontend plugin to `packages/app/package.json`**

```bash
cd "$BACKSTAGE_SRC/packages/app"
yarn add @terasky/backstage-plugin-crossplane-resources-frontend @terasky/backstage-plugin-crossplane-common
```

Expected: `package.json` updated, `yarn.lock` regenerated. The two packages appear under `dependencies`.

- [ ] **Step A2.2: Add permissions backend to `packages/backend/package.json`**

```bash
cd "$BACKSTAGE_SRC/packages/backend"
yarn add @terasky/backstage-plugin-crossplane-permissions-backend @terasky/backstage-plugin-crossplane-common
```

Expected: same two packages added under backend.

- [ ] **Step A2.3: Verify install**

```bash
cd "$BACKSTAGE_SRC"
yarn install
yarn tsc
```

Expected: no errors. If `tsc` fails, the plugin major version doesn't match your Backstage version — pin a compatible older version (check the plugin's npm page for compatibility matrix).

---

## Task A3: Register the TeraSky frontend in `App.tsx`

**Files:**
- Modify: `$BACKSTAGE_SRC/packages/app/src/App.tsx`
- Modify: `$BACKSTAGE_SRC/packages/app/src/components/catalog/EntityPage.tsx`

- [ ] **Step A3.1: Add the Crossplane resources route to `App.tsx`**

Add to the imports section near the top of `App.tsx`:

```typescript
import { CrossplaneResourcesPage } from '@terasky/backstage-plugin-crossplane-resources-frontend';
```

Add inside the `<FlatRoutes>` block:

```typescript
<Route path="/crossplane-resources" element={<CrossplaneResourcesPage />} />
```

- [ ] **Step A3.2: Add Crossplane tab to component EntityPage**

Open `packages/app/src/components/catalog/EntityPage.tsx`. Find the component pages (typically `serviceEntityPage`, `componentEntityPage`). Add an import:

```typescript
import { CrossplaneResourcesTab } from '@terasky/backstage-plugin-crossplane-resources-frontend';
```

Inside the `<EntityLayout>` for the relevant entity types, add a route:

```typescript
<EntityLayout.Route path="/crossplane" title="Crossplane">
  <CrossplaneResourcesTab />
</EntityLayout.Route>
```

- [ ] **Step A3.3: Verify TypeScript compiles**

```bash
cd "$BACKSTAGE_SRC"
yarn tsc
```

Expected: clean compile. Any error here means your imports don't match the plugin's exported names — check the plugin README on npm for exact import paths.

---

## Task A4: Register the permissions backend

**Files:**
- Modify: `$BACKSTAGE_SRC/packages/backend/src/index.ts`

- [ ] **Step A4.1: Register the permissions backend module**

Add to `index.ts`:

```typescript
backend.add(import('@terasky/backstage-plugin-crossplane-permissions-backend'));
```

This goes alongside the other `backend.add(...)` calls.

- [ ] **Step A4.2: Verify build**

```bash
cd "$BACKSTAGE_SRC"
yarn build:backend
```

Expected: no errors.

---

## Task A5: Build and push backstage-portal:v1.1.0 (user-handled gate)

**Files:** none in repo; image build artifact

- [ ] **Step A5.1: Build the new portal image**

```bash
cd "$BACKSTAGE_SRC"
yarn build:all
docker build -t 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.1.0 \
  -f packages/backend/Dockerfile .
```

Expected: image builds cleanly. If TypeScript errors appear here that didn't appear in earlier `yarn tsc` checks, your backend/frontend tsconfigs differ — usually fixed by running `yarn tsc -p packages/backend`.

- [ ] **Step A5.2: Push to ECR**

```bash
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin 852893458518.dkr.ecr.us-east-2.amazonaws.com
docker push 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.1.0
```

Expected: push succeeds. Tag `v1.1.0` is now available.

- [ ] **Step A5.3: Commit and push the Backstage source repo changes**

```bash
cd "$BACKSTAGE_SRC"
git add packages/app packages/backend package.json yarn.lock
git commit -m "feat: add TeraSky Crossplane plugins for catalog visualization

Adds frontend resource viewer + permissions backend.
Built into image v1.1.0."
git push
```

Expected: changes are on the Backstage source repo's main branch (or PR'd into it per that repo's process — adapt to your repo's flow).

---

## Task A6: This repo — RBAC, configmap, image bump

**Files:**
- Modify: `base-apps/backstage/configmaps.yaml`
- Modify: `base-apps/backstage/rbac.yaml`
- Modify: `base-apps/backstage/deployments.yaml`

- [ ] **Step A6.1: Create branch in this repo**

```bash
cd /Users/arisela/git/kubernetes
git checkout main && git pull --ff-only origin main
git checkout -b feat/backstage-terasky-crossplane-plugins
```

- [ ] **Step A6.2: Add `KUBERNETES_API_URL` env to configmap**

Edit `base-apps/backstage/configmaps.yaml`. Add to the `data:` block:

```yaml
data:
  POSTGRES_HOST: "postgresql.postgresql.svc.cluster.local"
  POSTGRES_PORT: "5432"
  AWS_DEFAULT_REGION: "us-east-2"
  VAULT_ADDR: "http://vault.vault.svc.cluster.local:8200"
  # Kubernetes plugin (TeraSky Crossplane plugin dependency)
  KUBERNETES_API_URL: "https://kubernetes.default.svc"
  KUBERNETES_SA_TOKEN_NAME: "backstage-kubernetes"
```

- [ ] **Step A6.3: Read the existing rbac.yaml**

```bash
cat base-apps/backstage/rbac.yaml
```

Take note of the existing `ServiceAccount` name (likely `backstage`). The new ClusterRole binds to it.

- [ ] **Step A6.4: Append a ClusterRole + ClusterRoleBinding for Crossplane read access**

Append to `base-apps/backstage/rbac.yaml`:

```yaml
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: backstage-crossplane-reader
rules:
- apiGroups:
  - pkg.crossplane.io
  - apiextensions.crossplane.io
  - platform.asela.io
  - s3.aws.upbound.io
  - iam.aws.upbound.io
  - ecr.aws.upbound.io
  - aws.upbound.io
  resources: ["*"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: backstage-crossplane-reader
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: backstage-crossplane-reader
subjects:
- kind: ServiceAccount
  name: backstage
  namespace: backstage
```

> Note: `platform.asela.io` and `ecr.aws.upbound.io` don't exist in the cluster yet — they're added in PR-B/C. The ClusterRole grants forward-compatible permissions; the API discovery just won't list those groups until later PRs land.

- [ ] **Step A6.5: Bump deployment image tag to v1.1.0**

In `base-apps/backstage/deployments.yaml`, change:

```yaml
image: 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.0.1
```
to
```yaml
image: 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.1.0
```

- [ ] **Step A6.6: Render against the cluster (read-only diff)**

```bash
kubectl diff -f base-apps/backstage/ 2>&1 | head -60
```

Expected: shows the configmap change, new ClusterRole + Binding, image tag change. No other deltas.

- [ ] **Step A6.7: Commit**

```bash
git add base-apps/backstage/
git commit -m "$(cat <<'EOF'
feat(backstage): install TeraSky Crossplane plugins

Adds ClusterRole granting the Backstage SA read-only access to
Crossplane CRDs (pkg.crossplane.io, apiextensions.crossplane.io,
*.aws.upbound.io, platform.asela.io). Also adds the Kubernetes
plugin envs the TeraSky frontend depends on, and bumps the
backstage-portal image to v1.1.0 (built with the new plugins
registered in App.tsx + the permissions backend in backend/index.ts).

Spec: docs/superpowers/specs/2026-04-27-crossplane-backstage-idp-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin feat/backstage-terasky-crossplane-plugins
```

- [ ] **Step A6.8: Open PR**

```bash
gh pr create --base main --head feat/backstage-terasky-crossplane-plugins \
  --title "feat(backstage): install TeraSky Crossplane plugins (PR-A of IDP)" \
  --body "$(cat <<'EOF'
## Summary
- Adds ClusterRole + ClusterRoleBinding for read-only Crossplane access from Backstage SA
- Adds KUBERNETES_API_URL configmap env (TeraSky frontend dep)
- Bumps backstage-portal image to v1.1.0 (built with the plugins; image already pushed to ECR)

This is PR-A of 4 in the Crossplane v2 + Backstage IDP plan.
Spec: docs/superpowers/specs/2026-04-27-crossplane-backstage-idp-design.md

## Backstage source repo changes (already merged there separately)
- Added @terasky/backstage-plugin-crossplane-resources-frontend
- Added @terasky/backstage-plugin-crossplane-permissions-backend
- Registered route + EntityPage tab in App.tsx
- Registered permissions module in backend/index.ts

## Post-merge verification
- [ ] Backstage pod rolls to v1.1.0
- [ ] Open Backstage portal, navigate to any catalog component
- [ ] Confirm "Crossplane" tab appears on EntityPage
- [ ] Open the global "/crossplane-resources" route — page loads without 403/500

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step A6.9: Wait for human review and merge**

Hand-off step.

---

## Task A7: PR-A post-merge verification

**Files:** none

- [ ] **Step A7.1: Confirm ArgoCD synced**

```bash
kubectl -n argo-cd get application backstage \
  -o jsonpath='{"sync="}{.status.sync.status}{" health="}{.status.health.status}{" rev="}{.status.sync.revision}{"\n"}'
```

Expected within ~3 min: `sync=Synced health=Healthy`.

- [ ] **Step A7.2: Confirm pod rolled to v1.1.0**

```bash
kubectl -n backstage get deploy backstage -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
```

Expected: `...backstage-portal:v1.1.0`.

- [ ] **Step A7.3: Confirm SA has the new permissions**

```bash
kubectl auth can-i list providers.pkg.crossplane.io --as=system:serviceaccount:backstage:backstage
kubectl auth can-i list buckets.s3.aws.upbound.io --as=system:serviceaccount:backstage:backstage
```

Expected: `yes` for both.

- [ ] **Step A7.4: Manual UI verification**

Open the Backstage portal in a browser. Navigate to any registered component. Confirm a **Crossplane** tab appears on the EntityPage. Open the standalone `/crossplane-resources` route — it should load (the "no resources" state is fine; we have no claims yet).

Sign-off criterion: Backstage UI shows the Crossplane tab, no console errors visible in browser dev-tools.

---

# Phase B — PR-B: HomelabApp v1.0 Slim

Goal: Claim a `HomelabApp`, get a Namespace + ArgoCD Application out the other side. End-to-end IDP loop on minimal payload.

---

## Task B1: Create `function-source/` scaffold

**Files:**
- Create: `base-apps/homelab-app-platform/function-source/Dockerfile`
- Create: `base-apps/homelab-app-platform/function-source/requirements.txt`
- Create: `base-apps/homelab-app-platform/function-source/crossplane.yaml`
- Create: `base-apps/homelab-app-platform/function-source/Makefile`
- Create: `base-apps/homelab-app-platform/function-source/.dockerignore`

- [ ] **Step B1.1: Create branch**

```bash
cd /Users/arisela/git/kubernetes
git checkout main && git pull --ff-only origin main
git checkout -b feat/homelab-app-slim
mkdir -p base-apps/homelab-app-platform/function-source/tests
```

- [ ] **Step B1.2: Write `requirements.txt`**

Create `base-apps/homelab-app-platform/function-source/requirements.txt`:

```
crossplane-function-sdk-python>=0.10.0
grpcio>=1.60.0
```

- [ ] **Step B1.3: Write `crossplane.yaml` (function package metadata)**

Create `base-apps/homelab-app-platform/function-source/crossplane.yaml`:

```yaml
apiVersion: meta.pkg.crossplane.io/v1beta1
kind: Function
metadata:
  name: function-homelab-app
  annotations:
    meta.crossplane.io/maintainer: "Ari Sela <arigsela@gmail.com>"
    meta.crossplane.io/source: github.com/arigsela/kubernetes
    meta.crossplane.io/license: Apache-2.0
    meta.crossplane.io/description: |
      Composition function that materializes HomelabApp claims into
      Namespace + ArgoCD Application + (optional) ECR/S3/Postgres bundles.
spec:
  crossplane:
    version: ">=v1.14.0-0"
```

- [ ] **Step B1.4: Write `Dockerfile`**

Create `base-apps/homelab-app-platform/function-source/Dockerfile`:

```dockerfile
FROM python:3.11-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --target=/build/deps -r requirements.txt

FROM python:3.11-slim AS runtime
WORKDIR /function
COPY --from=builder /build/deps /function/deps
ENV PYTHONPATH=/function/deps
COPY main.py crossplane.yaml /function/
EXPOSE 9443
ENTRYPOINT ["python", "/function/main.py"]
```

- [ ] **Step B1.5: Write `Makefile`**

Create `base-apps/homelab-app-platform/function-source/Makefile`:

```makefile
IMAGE ?= 852893458518.dkr.ecr.us-east-2.amazonaws.com/function-homelab-app
TAG   ?= v0.1.0
REGION ?= us-east-2

.PHONY: test build push login

test:
	pytest tests/ -v

build:
	docker build -t $(IMAGE):$(TAG) .

login:
	aws ecr get-login-password --region $(REGION) | \
	docker login --username AWS --password-stdin $(IMAGE)

push: login
	docker push $(IMAGE):$(TAG)
```

- [ ] **Step B1.6: Write `.dockerignore`**

Create `base-apps/homelab-app-platform/function-source/.dockerignore`:

```
tests/
__pycache__/
*.pyc
.pytest_cache/
```

---

## Task B2: TDD — write failing tests for Slim compose

**Files:**
- Create: `base-apps/homelab-app-platform/function-source/tests/__init__.py`
- Create: `base-apps/homelab-app-platform/function-source/tests/test_compose.py`

- [ ] **Step B2.1: Create empty `__init__.py`**

```bash
touch base-apps/homelab-app-platform/function-source/tests/__init__.py
```

- [ ] **Step B2.2: Write the Slim compose tests**

Create `base-apps/homelab-app-platform/function-source/tests/test_compose.py`:

```python
"""Tests for HomelabApp composition function."""
import pytest
from crossplane.function.proto.v1 import run_function_pb2 as fnv1
from google.protobuf import struct_pb2, json_format

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import HomelabAppFunction  # noqa: E402


def make_request(name: str, spec: dict) -> fnv1.RunFunctionRequest:
    """Build a RunFunctionRequest with an observed XR matching the given spec."""
    req = fnv1.RunFunctionRequest()
    xr = {
        "apiVersion": "platform.asela.io/v1alpha1",
        "kind": "HomelabApp",
        "metadata": {"name": name, "namespace": "platform-system"},
        "spec": spec,
    }
    json_format.ParseDict(xr, req.observed.composite.resource)
    return req


def get_resource(rsp: fnv1.RunFunctionResponse, key: str) -> dict:
    """Return the desired resource at `key` as a plain dict."""
    return json_format.MessageToDict(rsp.desired.resources[key].resource)


def test_slim_emits_namespace():
    req = make_request("test-app", {"manifestPath": "base-apps/test-app"})
    rsp = HomelabAppFunction().RunFunction(req, None)

    ns = get_resource(rsp, "namespace")
    assert ns["kind"] == "Namespace"
    assert ns["metadata"]["name"] == "test-app"


def test_slim_emits_argo_application():
    req = make_request("test-app", {"manifestPath": "base-apps/test-app"})
    rsp = HomelabAppFunction().RunFunction(req, None)

    app = get_resource(rsp, "argo-app")
    assert app["kind"] == "Application"
    assert app["apiVersion"] == "argoproj.io/v1alpha1"
    assert app["metadata"]["name"] == "test-app"
    assert app["metadata"]["namespace"] == "argo-cd"
    assert app["spec"]["source"]["path"] == "base-apps/test-app"
    assert app["spec"]["source"]["repoURL"] == "https://github.com/arigsela/kubernetes"
    assert app["spec"]["source"]["targetRevision"] == "main"
    assert app["spec"]["destination"]["namespace"] == "test-app"


def test_slim_argo_app_uses_automated_sync():
    req = make_request("foo", {"manifestPath": "base-apps/foo"})
    rsp = HomelabAppFunction().RunFunction(req, None)

    app = get_resource(rsp, "argo-app")
    assert app["spec"]["syncPolicy"]["automated"]["prune"] is True
    assert app["spec"]["syncPolicy"]["automated"]["selfHeal"] is True
    assert "CreateNamespace=true" in app["spec"]["syncPolicy"]["syncOptions"]


def test_slim_only_emits_two_resources():
    """Slim version emits exactly Namespace + ArgoCD Application — nothing else."""
    req = make_request("foo", {"manifestPath": "base-apps/foo"})
    rsp = HomelabAppFunction().RunFunction(req, None)

    assert set(rsp.desired.resources.keys()) == {"namespace", "argo-app"}
```

- [ ] **Step B2.3: Verify tests fail (no `main.py` yet)**

```bash
cd base-apps/homelab-app-platform/function-source
python -m pip install -r requirements.txt
pytest tests/ -v
```

Expected: `ModuleNotFoundError: No module named 'main'` or similar import failure on every test. This confirms TDD red state.

---

## Task B3: Implement Slim `main.py` to make tests pass

**Files:**
- Create: `base-apps/homelab-app-platform/function-source/main.py`

- [ ] **Step B3.1: Write the Slim function**

Create `base-apps/homelab-app-platform/function-source/main.py`:

```python
"""HomelabApp composition function — entry point.

Receives an observed HomelabApp claim and emits desired-state resources.
v0.1.0 (Slim): Namespace + ArgoCD Application.
"""
import logging
from concurrent import futures

import grpc
from crossplane.function.proto.v1 import run_function_pb2 as fnv1
from crossplane.function.proto.v1 import run_function_pb2_grpc as grpcv1
from crossplane.function import response
from google.protobuf import json_format

REPO_URL = "https://github.com/arigsela/kubernetes"
ARGOCD_NAMESPACE = "argo-cd"

logger = logging.getLogger("function-homelab-app")
logging.basicConfig(level=logging.INFO)


class HomelabAppFunction(grpcv1.FunctionRunnerService):
    def RunFunction(
        self, request: fnv1.RunFunctionRequest, _context
    ) -> fnv1.RunFunctionResponse:
        rsp = response.to(request)
        observed_xr = json_format.MessageToDict(request.observed.composite.resource)
        spec = observed_xr.get("spec", {})
        name = observed_xr["metadata"]["name"]
        logger.info("composing HomelabApp", extra={"name": name})

        _set_resource(rsp, "namespace", make_namespace(name))
        _set_resource(rsp, "argo-app", make_argo_app(name, spec))
        return rsp


def _set_resource(rsp: fnv1.RunFunctionResponse, key: str, body: dict) -> None:
    """Place a desired resource into the response at the given key."""
    json_format.ParseDict(body, rsp.desired.resources[key].resource)


def make_namespace(name: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": name},
    }


def make_argo_app(name: str, spec: dict) -> dict:
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": name,
            "namespace": ARGOCD_NAMESPACE,
        },
        "spec": {
            "project": "default",
            "source": {
                "repoURL": REPO_URL,
                "targetRevision": "main",
                "path": spec["manifestPath"],
            },
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": name,
            },
            "syncPolicy": {
                "automated": {"prune": True, "selfHeal": True},
                "syncOptions": ["CreateNamespace=true"],
            },
        },
    }


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    grpcv1.add_FunctionRunnerServiceServicer_to_server(HomelabAppFunction(), server)
    server.add_insecure_port("[::]:9443")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
```

- [ ] **Step B3.2: Run tests, expect green**

```bash
cd base-apps/homelab-app-platform/function-source
pytest tests/ -v
```

Expected: 4 tests pass.

- [ ] **Step B3.3: Commit**

```bash
cd /Users/arisela/git/kubernetes
git add base-apps/homelab-app-platform/function-source/
git commit -m "feat(homelab-app): add function-source scaffold + Slim compose

Initial Python composition function implementation:
- Namespace + ArgoCD Application emitter
- pytest harness with 4 tests covering Slim behaviour

Image build (v0.1.0) and Crossplane wiring follow in subsequent commits."
```

---

## Task B4: Build and push function-homelab-app:v0.1.0 (user-handled gate)

**Files:** none in repo

- [ ] **Step B4.1: Build the function image**

```bash
cd base-apps/homelab-app-platform/function-source
make build
```

Expected: image builds. Tag: `function-homelab-app:v0.1.0`.

- [ ] **Step B4.2: Push to ECR**

```bash
make push
```

Expected: push succeeds.

- [ ] **Step B4.3: Note the image digest**

```bash
docker inspect 852893458518.dkr.ecr.us-east-2.amazonaws.com/function-homelab-app:v0.1.0 \
  --format '{{ index .RepoDigests 0 }}'
```

Record the digest — Crossplane prefers digest references for reproducibility.

---

## Task B5: Author the XRD

**Files:**
- Create: `base-apps/homelab-app-platform/xrd.yaml`

- [ ] **Step B5.1: Write XRD with v1alpha1 schema**

Create `base-apps/homelab-app-platform/xrd.yaml`:

```yaml
# CompositeResourceDefinition for HomelabApp.
# v1alpha1 (Slim): manifestPath only.
# v1alpha2 will add ecrRepo (PR-C).
# v1alpha3 will add wantsS3 / wantsPostgres (PR-D).
# Additive evolution; older versions stay served.
apiVersion: apiextensions.crossplane.io/v2
kind: CompositeResourceDefinition
metadata:
  name: homelabapps.platform.asela.io
spec:
  scope: Namespaced
  group: platform.asela.io
  names:
    kind: HomelabApp
    plural: homelabapps
  versions:
  - name: v1alpha1
    served: true
    referenceable: true
    schema:
      openAPIV3Schema:
        type: object
        properties:
          spec:
            type: object
            properties:
              manifestPath:
                type: string
                description: |
                  GitOps repo path for this app's manifests
                  (e.g., base-apps/recipe-sharer).
            required:
            - manifestPath
          status:
            type: object
            properties:
              ready:
                type: boolean
```

- [ ] **Step B5.2: Validate YAML syntax**

```bash
kubectl create --dry-run=client -f base-apps/homelab-app-platform/xrd.yaml -o yaml | head -5
```

Expected: clean YAML output, no errors.

---

## Task B6: Author the Composition and Function CRD references

**Files:**
- Create: `base-apps/homelab-app-platform/composition.yaml`
- Create: `base-apps/homelab-app-platform/function.yaml`

- [ ] **Step B6.1: Write the Function resource**

Create `base-apps/homelab-app-platform/function.yaml`:

```yaml
# Function package — references the OCI image we built and pushed.
# Version bumps with each PR (v0.1.0 → v0.2.0 → v0.3.0).
apiVersion: pkg.crossplane.io/v1
kind: Function
metadata:
  name: function-homelab-app
spec:
  package: 852893458518.dkr.ecr.us-east-2.amazonaws.com/function-homelab-app:v0.1.0
  packagePullPolicy: IfNotPresent
  revisionActivationPolicy: Automatic
  revisionHistoryLimit: 1
```

- [ ] **Step B6.2: Write the Composition**

Create `base-apps/homelab-app-platform/composition.yaml`:

```yaml
# Composition for HomelabApp — single-step pipeline calling our function.
# All branching logic lives inside function-homelab-app/main.py.
apiVersion: apiextensions.crossplane.io/v1
kind: Composition
metadata:
  name: homelab-app
  labels:
    crossplane.io/xrd: homelabapps.platform.asela.io
spec:
  compositeTypeRef:
    apiVersion: platform.asela.io/v1alpha1
    kind: HomelabApp
  mode: Pipeline
  pipeline:
  - step: emit-resources
    functionRef:
      name: function-homelab-app
```

- [ ] **Step B6.3: Validate**

```bash
kubectl create --dry-run=client -f base-apps/homelab-app-platform/composition.yaml -o yaml | head -5
kubectl create --dry-run=client -f base-apps/homelab-app-platform/function.yaml -o yaml | head -5
```

Expected: both render without errors.

---

## Task B7: Wire into ArgoCD master-app pattern

**Files:**
- Create: `base-apps/homelab-app-platform.yaml`

- [ ] **Step B7.1: Write ArgoCD Application**

Create `base-apps/homelab-app-platform.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: homelab-app-platform
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/homelab-app-platform
    directory:
      # Skip function-source/ — it's not Kubernetes manifests
      exclude: 'function-source/**'
  destination:
    server: https://kubernetes.default.svc
    namespace: crossplane-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [ ] **Step B7.2: Commit the Crossplane-side files**

```bash
git add base-apps/homelab-app-platform/xrd.yaml \
        base-apps/homelab-app-platform/composition.yaml \
        base-apps/homelab-app-platform/function.yaml \
        base-apps/homelab-app-platform.yaml
git commit -m "feat(homelab-app): add XRD, Composition, Function, ArgoCD app

XRD: HomelabApp v1alpha1 (manifestPath only). Scope Namespaced.
Composition: single-step pipeline calling function-homelab-app.
Function: pinned to ECR image v0.1.0.
ArgoCD app excludes function-source/ (Python sources, not manifests)."
```

---

## Task B8: Backstage source — create the Software Template

**Files:**
- Create: `$BACKSTAGE_SRC/templates/new-homelab-app/template.yaml`
- Create: `$BACKSTAGE_SRC/templates/new-homelab-app/skeleton/{{ values.name }}-claim.yaml`
- Modify: `$BACKSTAGE_SRC/app-config.yaml`

- [ ] **Step B8.1: Create the template directory**

```bash
mkdir -p "$BACKSTAGE_SRC/templates/new-homelab-app/skeleton"
```

- [ ] **Step B8.2: Write the Software Template**

Create `$BACKSTAGE_SRC/templates/new-homelab-app/template.yaml`:

```yaml
apiVersion: scaffolder.backstage.io/v1beta3
kind: Template
metadata:
  name: new-homelab-app
  title: New Homelab App
  description: |
    Bootstrap a new homelab service end-to-end.
    Creates a HomelabApp claim that Crossplane composes into
    a Namespace + ArgoCD Application (and more in later versions).
  tags:
    - crossplane
    - idp
    - homelab
spec:
  owner: platform-team
  type: service

  parameters:
    - title: App identity
      required: [name, manifestPath]
      properties:
        name:
          title: App name
          type: string
          description: Lowercase, hyphenated. Becomes namespace + ArgoCD app + claim name.
          pattern: '^[a-z][a-z0-9-]*[a-z0-9]$'
          maxLength: 50
        manifestPath:
          title: GitOps manifest path
          type: string
          description: Path in arigsela/kubernetes where this app's manifests will live.
          default: 'base-apps/'

  steps:
    - id: fetch
      name: Render claim
      action: fetch:template
      input:
        url: ./skeleton
        values:
          name: ${{ parameters.name }}
          manifestPath: ${{ parameters.manifestPath }}

    - id: publish
      name: Open PR against arigsela/kubernetes
      action: publish:github:pull-request
      input:
        repoUrl: github.com?owner=arigsela&repo=kubernetes
        branchName: feat/${{ parameters.name }}-homelab-app
        title: 'feat(homelab-app): claim ${{ parameters.name }}'
        description: |
          Created via Backstage `new-homelab-app` template.

          - name: ${{ parameters.name }}
          - manifestPath: ${{ parameters.manifestPath }}
        targetPath: base-apps/

  output:
    links:
      - title: Open PR
        url: ${{ steps.publish.output.remoteUrl }}
```

- [ ] **Step B8.3: Write the skeleton claim file**

Create `$BACKSTAGE_SRC/templates/new-homelab-app/skeleton/{{ values.name }}-claim.yaml`:

```yaml
apiVersion: platform.asela.io/v1alpha1
kind: HomelabApp
metadata:
  name: ${{ values.name }}
  namespace: platform-system
spec:
  manifestPath: ${{ values.manifestPath }}
```

- [ ] **Step B8.4: Register the template in `app-config.yaml`**

Append under `catalog.locations` in `$BACKSTAGE_SRC/app-config.yaml`:

```yaml
catalog:
  locations:
    # ... existing locations ...
    - type: file
      target: ../../templates/new-homelab-app/template.yaml
      rules:
        - allow: [Template]
```

(Adjust the relative path to match how other templates are registered in this portal.)

- [ ] **Step B8.5: Local-test the template**

```bash
cd "$BACKSTAGE_SRC"
yarn dev
```

Open http://localhost:3000/create. Confirm "New Homelab App" appears. Click through it with dummy values; do NOT actually create the PR (cancel before the publish step, or run against a fork). Confirm the form renders with the two fields.

---

## Task B9: Build and push backstage-portal:v1.2.0

**Files:** none in repo

- [ ] **Step B9.1: Commit and build**

```bash
cd "$BACKSTAGE_SRC"
git add templates/new-homelab-app app-config.yaml
git commit -m "feat: add new-homelab-app Software Template (PR-B Slim)"
yarn build:all
docker build -t 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.2.0 \
  -f packages/backend/Dockerfile .
```

- [ ] **Step B9.2: Push and propagate**

```bash
docker push 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.2.0
git push
```

---

## Task B10: This repo — bump Backstage image, function image, open PR-B

**Files:**
- Modify: `base-apps/backstage/deployments.yaml`
- Modify: `base-apps/homelab-app-platform/function.yaml`

- [ ] **Step B10.1: Bump Backstage image tag**

Change in `base-apps/backstage/deployments.yaml`: `v1.1.0 → v1.2.0`.

- [ ] **Step B10.2: (Optional) confirm function image tag**

Confirm `base-apps/homelab-app-platform/function.yaml` references `:v0.1.0` (it should, from Task B6.1).

- [ ] **Step B10.3: kubectl diff sanity check**

```bash
kubectl diff -f base-apps/homelab-app-platform/ 2>&1 | head -60
kubectl diff -f base-apps/backstage/ 2>&1 | head -20
```

Expected: shows the new XRD/Composition/Function being created and the Backstage image bump.

- [ ] **Step B10.4: Commit and push**

```bash
git add base-apps/backstage/deployments.yaml \
        base-apps/homelab-app-platform/function.yaml
git commit -m "chore: bump backstage v1.2.0 (with new-homelab-app template)"
git push -u origin feat/homelab-app-slim
```

- [ ] **Step B10.5: Open PR**

```bash
gh pr create --base main --head feat/homelab-app-slim \
  --title "feat(idp): HomelabApp v1.0 Slim — XRD + Composition + template (PR-B)" \
  --body "$(cat <<'EOF'
## Summary
- New XRD `HomelabApp` (v1alpha1, scope Namespaced) at `base-apps/homelab-app-platform/xrd.yaml`
- Composition + Function CRD wiring (function image v0.1.0)
- Python composition function emitting Namespace + ArgoCD Application, with pytest suite (4 tests)
- ArgoCD Application picking up the new `homelab-app-platform/` directory
- Backstage portal v1.2.0 with the `new-homelab-app` Software Template

## Post-merge verification
- [ ] XRD appears: `kubectl get xrd homelabapps.platform.asela.io`
- [ ] Composition `homelab-app` exists and is `Ready`
- [ ] Function `function-homelab-app` is `INSTALLED=True, HEALTHY=True`
- [ ] Open Backstage → Create → "New Homelab App" appears

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step B10.6: Wait for human review and merge**

---

## Task B11: PR-B post-merge verification + Slim smoke test

**Files:** none

- [ ] **Step B11.1: Verify XRD/Composition/Function are healthy**

```bash
kubectl get xrd homelabapps.platform.asela.io
kubectl get composition homelab-app
kubectl get functions.pkg.crossplane.io function-homelab-app -o 'custom-columns=NAME:.metadata.name,INSTALLED:.status.conditions[?(@.type=="Installed")].status,HEALTHY:.status.conditions[?(@.type=="Healthy")].status'
```

Expected: XRD established, Composition exists, Function `INSTALLED=True, HEALTHY=True`.

- [ ] **Step B11.2: Smoke test — create a test claim manually (not via Backstage yet)**

```bash
kubectl create namespace platform-system 2>/dev/null || true
cat <<EOF | kubectl apply -f -
apiVersion: platform.asela.io/v1alpha1
kind: HomelabApp
metadata:
  name: smoke-test-slim
  namespace: platform-system
spec:
  manifestPath: base-apps/smoke-test-slim
EOF
```

Expected: claim created. Check that composed resources appear:

```bash
sleep 30
kubectl get namespace smoke-test-slim
kubectl -n argo-cd get application smoke-test-slim
```

Expected: namespace exists; ArgoCD Application exists (will be `OutOfSync` because `base-apps/smoke-test-slim` doesn't exist in git — that's fine for this test).

- [ ] **Step B11.3: Clean up the smoke test**

```bash
kubectl delete homelabapp.platform.asela.io smoke-test-slim -n platform-system
sleep 20
kubectl get namespace smoke-test-slim 2>&1 | grep -q NotFound && echo "cleaned up"
```

Expected: namespace and ArgoCD app are pruned.

- [ ] **Step B11.4: Backstage end-to-end**

In the Backstage portal, navigate to **Create** → **New Homelab App**. Fill `name=demo-slim`, `manifestPath=base-apps/demo-slim`. Click **Create**. Observe the PR open in GitHub. **Do not merge** — close it after confirming. This validates the full template → PR loop.

---

# Phase C — PR-C: HomelabApp v1.0 Useful (ECR + Kyverno auto-auth)

Goal: Claim now provisions an ECR repository and auto-injects pull secret via existing Kyverno rule.

---

## Task C1: Add `provider-aws-ecr` to existing provider manifest

**Files:**
- Modify: `base-apps/crossplane-aws-provider/provider.yaml`

- [ ] **Step C1.1: Branch from fresh main**

```bash
cd /Users/arisela/git/kubernetes
git checkout main && git pull --ff-only origin main
git checkout -b feat/homelab-app-useful
```

- [ ] **Step C1.2: Append new Provider entry**

Append to `base-apps/crossplane-aws-provider/provider.yaml`:

```yaml
---
# AWS ECR Provider — added in PR-C of IDP work for HomelabApp.
apiVersion: pkg.crossplane.io/v1
kind: Provider
metadata:
  name: provider-aws-ecr
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  package: xpkg.upbound.io/upbound/provider-aws-ecr:v2.5.3
  packagePullPolicy: IfNotPresent
  revisionActivationPolicy: Automatic
  revisionHistoryLimit: 1
  runtimeConfigRef:
    name: aws-provider-runtime
```

- [ ] **Step C1.3: kubectl diff sanity check**

```bash
kubectl diff -f base-apps/crossplane-aws-provider/provider.yaml 2>&1 | head -30
```

Expected: shows the new `provider-aws-ecr` Provider being created. No other changes.

- [ ] **Step C1.4: Commit**

```bash
git add base-apps/crossplane-aws-provider/provider.yaml
git commit -m "feat(crossplane): add provider-aws-ecr v2.5.3 (PR-C of IDP)"
```

---

## Task C2: TDD — extend tests for ECR + namespace label

**Files:**
- Modify: `base-apps/homelab-app-platform/function-source/tests/test_compose.py`

- [ ] **Step C2.1: Append Useful tests**

Append to `base-apps/homelab-app-platform/function-source/tests/test_compose.py`:

```python
# ---- PR-C Useful: ECR repo + Kyverno-trigger label ----

def test_useful_emits_ecr_repository():
    req = make_request(
        "test-app",
        {"manifestPath": "base-apps/test-app", "ecrRepo": "test-app"},
    )
    rsp = HomelabAppFunction().RunFunction(req, None)

    repo = get_resource(rsp, "ecr-repo")
    assert repo["kind"] == "Repository"
    assert repo["apiVersion"] == "ecr.aws.upbound.io/v1beta1"
    assert repo["metadata"]["annotations"]["crossplane.io/external-name"] == "test-app"
    assert repo["spec"]["forProvider"]["region"] == "us-east-2"


def test_useful_namespace_has_kyverno_label():
    req = make_request(
        "test-app",
        {"manifestPath": "base-apps/test-app", "ecrRepo": "test-app"},
    )
    rsp = HomelabAppFunction().RunFunction(req, None)

    ns = get_resource(rsp, "namespace")
    assert ns["metadata"]["labels"]["ecr-pull-secret"] == "enabled"


def test_useful_emits_three_resources():
    """Useful version emits namespace + argo-app + ecr-repo."""
    req = make_request(
        "x",
        {"manifestPath": "base-apps/x", "ecrRepo": "x"},
    )
    rsp = HomelabAppFunction().RunFunction(req, None)
    assert set(rsp.desired.resources.keys()) == {"namespace", "argo-app", "ecr-repo"}
```

- [ ] **Step C2.2: Run tests, expect failures on the new tests**

```bash
cd base-apps/homelab-app-platform/function-source
pytest tests/ -v
```

Expected: 4 Slim tests pass; 3 new Useful tests fail. The Slim test `test_slim_only_emits_two_resources` will need updating in Step C3.2 since v0.2.0 emits 3 resources when `ecrRepo` is present.

---

## Task C3: Update `main.py` to make Useful tests pass

**Files:**
- Modify: `base-apps/homelab-app-platform/function-source/main.py`
- Modify: `base-apps/homelab-app-platform/function-source/tests/test_compose.py` (one test update)

- [ ] **Step C3.1: Update `make_namespace` and add `make_ecr_repo`**

Edit `main.py`. Replace `make_namespace` and add new helper:

```python
AWS_REGION = "us-east-2"


def make_namespace(name: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": name,
            "labels": {
                # Triggers existing Kyverno generate-policy that creates
                # imagePullSecret in the namespace.
                "ecr-pull-secret": "enabled",
            },
        },
    }


def make_ecr_repo(name: str, ecr_repo: str) -> dict:
    return {
        "apiVersion": "ecr.aws.upbound.io/v1beta1",
        "kind": "Repository",
        "metadata": {
            "name": f"{name}-ecr",
            "annotations": {
                # external-name pins the AWS-side repo name.
                "crossplane.io/external-name": ecr_repo,
            },
        },
        "spec": {
            "forProvider": {
                "region": AWS_REGION,
                "imageScanningConfiguration": [
                    {"scanOnPush": True},
                ],
                "imageTagMutability": "IMMUTABLE",
            },
            "providerConfigRef": {"name": "default"},
        },
    }
```

- [ ] **Step C3.2: Update `RunFunction` to emit ECR conditionally on `ecrRepo`**

Replace the body of `RunFunction` in `main.py`:

```python
class HomelabAppFunction(grpcv1.FunctionRunnerService):
    def RunFunction(
        self, request: fnv1.RunFunctionRequest, _context
    ) -> fnv1.RunFunctionResponse:
        rsp = response.to(request)
        observed_xr = json_format.MessageToDict(request.observed.composite.resource)
        spec = observed_xr.get("spec", {})
        name = observed_xr["metadata"]["name"]
        logger.info("composing HomelabApp", extra={"name": name})

        _set_resource(rsp, "namespace", make_namespace(name))
        _set_resource(rsp, "argo-app", make_argo_app(name, spec))
        if spec.get("ecrRepo"):
            _set_resource(rsp, "ecr-repo", make_ecr_repo(name, spec["ecrRepo"]))
        return rsp
```

- [ ] **Step C3.3: Update the Slim "only emits two resources" test**

Edit `tests/test_compose.py` — `test_slim_only_emits_two_resources` becomes the no-ecrRepo case:

```python
def test_no_ecr_only_emits_two_resources():
    """When ecrRepo is omitted, only namespace + argo-app are emitted."""
    req = make_request("foo", {"manifestPath": "base-apps/foo"})
    rsp = HomelabAppFunction().RunFunction(req, None)
    assert set(rsp.desired.resources.keys()) == {"namespace", "argo-app"}
```

- [ ] **Step C3.4: Run all tests**

```bash
pytest tests/ -v
```

Expected: all tests pass (4 Slim tests + 3 new Useful tests = 7 passing).

- [ ] **Step C3.5: Commit**

```bash
cd /Users/arisela/git/kubernetes
git add base-apps/homelab-app-platform/function-source/main.py \
        base-apps/homelab-app-platform/function-source/tests/test_compose.py
git commit -m "feat(homelab-app): add ECR repo + Kyverno-trigger label (PR-C function v0.2.0)

- make_ecr_repo emits an ECR Repository resource when spec.ecrRepo is set
- Namespace gains the 'ecr-pull-secret: enabled' label that drives the
  existing Kyverno generate-policy producing the imagePullSecret
- 7 tests pass (4 Slim + 3 Useful)"
```

---

## Task C4: Update XRD with `v1alpha2` (additive)

**Files:**
- Modify: `base-apps/homelab-app-platform/xrd.yaml`

- [ ] **Step C4.1: Add the v1alpha2 version**

Edit `base-apps/homelab-app-platform/xrd.yaml` — under `spec.versions`, append:

```yaml
  - name: v1alpha2
    served: true
    referenceable: false  # keep v1alpha1 referenceable for now
    schema:
      openAPIV3Schema:
        type: object
        properties:
          spec:
            type: object
            properties:
              manifestPath:
                type: string
                description: GitOps repo path for this app's manifests.
              ecrRepo:
                type: string
                description: ECR repository name (used for image pulls).
            required:
            - manifestPath
            - ecrRepo
          status:
            type: object
            properties:
              ready:
                type: boolean
```

- [ ] **Step C4.2: Validate**

```bash
kubectl diff -f base-apps/homelab-app-platform/xrd.yaml 2>&1 | head -40
```

Expected: shows the new `v1alpha2` version being added to the existing XRD. `v1alpha1` remains served.

- [ ] **Step C4.3: Commit**

```bash
git add base-apps/homelab-app-platform/xrd.yaml
git commit -m "feat(homelab-app): add XRD v1alpha2 with ecrRepo (additive)"
```

---

## Task C5: Build and push function-homelab-app:v0.2.0

**Files:** none in repo

- [ ] **Step C5.1: Build and push**

```bash
cd base-apps/homelab-app-platform/function-source
make TAG=v0.2.0 build push
```

Expected: image v0.2.0 pushed to ECR.

- [ ] **Step C5.2: Bump function.yaml**

In `base-apps/homelab-app-platform/function.yaml`, change `:v0.1.0 → :v0.2.0`.

- [ ] **Step C5.3: kubectl diff**

```bash
cd /Users/arisela/git/kubernetes
kubectl diff -f base-apps/homelab-app-platform/function.yaml 2>&1 | head -10
```

Expected: only `spec.package` changes.

- [ ] **Step C5.4: Commit**

```bash
git add base-apps/homelab-app-platform/function.yaml
git commit -m "chore(homelab-app): bump function image to v0.2.0"
```

---

## Task C6: Backstage source — extend the Software Template

**Files:**
- Modify: `$BACKSTAGE_SRC/templates/new-homelab-app/template.yaml`
- Modify: `$BACKSTAGE_SRC/templates/new-homelab-app/skeleton/{{ values.name }}-claim.yaml`

- [ ] **Step C6.1: Add `ecrRepo` form field**

Edit `template.yaml`. Replace the `parameters[0].properties` block:

```yaml
parameters:
    - title: App identity
      required: [name, manifestPath, ecrRepo]
      properties:
        name:
          title: App name
          type: string
          pattern: '^[a-z][a-z0-9-]*[a-z0-9]$'
          maxLength: 50
        manifestPath:
          title: GitOps manifest path
          type: string
          default: 'base-apps/'
        ecrRepo:
          title: ECR repository name
          type: string
          description: Existing or to-be-created ECR repo holding this app's image.
          pattern: '^[a-z][a-z0-9-]*[a-z0-9]$'
```

Update the `fetch:template` step's `values:` to include `ecrRepo: ${{ parameters.ecrRepo }}`.

- [ ] **Step C6.2: Update the skeleton to use v1alpha2**

Replace `templates/new-homelab-app/skeleton/{{ values.name }}-claim.yaml`:

```yaml
apiVersion: platform.asela.io/v1alpha2
kind: HomelabApp
metadata:
  name: ${{ values.name }}
  namespace: platform-system
spec:
  manifestPath: ${{ values.manifestPath }}
  ecrRepo: ${{ values.ecrRepo }}
```

- [ ] **Step C6.3: Local-test**

```bash
cd "$BACKSTAGE_SRC"
yarn dev
```

Open http://localhost:3000/create/templates/new-homelab-app and confirm the form now includes `ecrRepo`.

- [ ] **Step C6.4: Commit Backstage source changes**

```bash
git add templates/new-homelab-app
git commit -m "feat: extend new-homelab-app template with ecrRepo (PR-C)"
```

---

## Task C7: Build and push backstage-portal:v1.3.0

**Files:** none in repo

- [ ] **Step C7.1: Build and push the new portal image**

```bash
cd "$BACKSTAGE_SRC"
yarn build:all
docker build -t 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.3.0 \
  -f packages/backend/Dockerfile .
docker push 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.3.0
git push
```

- [ ] **Step C7.2: Bump deployment image in this repo**

```bash
cd /Users/arisela/git/kubernetes
```

Edit `base-apps/backstage/deployments.yaml`: `v1.2.0 → v1.3.0`.

- [ ] **Step C7.3: Commit**

```bash
git add base-apps/backstage/deployments.yaml
git commit -m "chore: bump backstage to v1.3.0 (with ecrRepo template)"
```

---

## Task C8: Open PR-C and verify post-merge

**Files:** none

- [ ] **Step C8.1: Push and open PR**

```bash
git push -u origin feat/homelab-app-useful
gh pr create --base main --head feat/homelab-app-useful \
  --title "feat(idp): HomelabApp v1.0 Useful — ECR + Kyverno auto-auth (PR-C)" \
  --body "$(cat <<'EOF'
## Summary
- Adds `provider-aws-ecr:v2.5.3` to the AWS provider manifest
- XRD v1alpha2 served (additive): adds required `ecrRepo` field; v1alpha1 stays served
- Function image v0.2.0: emits ECR Repository + Namespace label triggering existing Kyverno ECR-auth policy
- 7 tests pass (4 Slim + 3 Useful)
- Backstage portal v1.3.0 with `ecrRepo` form field

## Post-merge verification
- [ ] `provider-aws-ecr` is HEALTHY=True
- [ ] XRD `homelabapps.platform.asela.io` lists v1alpha1 + v1alpha2 served
- [ ] Smoke claim with `ecrRepo` set produces an ECR Repository
- [ ] Namespace gets imagePullSecret via Kyverno

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step C8.2: Wait for merge**

- [ ] **Step C8.3: Post-merge — verify provider and smoke test**

```bash
kubectl get providers.pkg.crossplane.io provider-aws-ecr -o 'custom-columns=NAME:.metadata.name,HEALTHY:.status.conditions[?(@.type=="Healthy")].status'
```

Expected: `provider-aws-ecr  True`.

```bash
cat <<EOF | kubectl apply -f -
apiVersion: platform.asela.io/v1alpha2
kind: HomelabApp
metadata:
  name: smoke-test-useful
  namespace: platform-system
spec:
  manifestPath: base-apps/smoke-test-useful
  ecrRepo: smoke-test-useful
EOF
sleep 60
kubectl get repository.ecr.aws.upbound.io smoke-test-useful-ecr -o 'custom-columns=NAME:.metadata.name,SYNCED:.status.conditions[?(@.type=="Synced")].status,READY:.status.conditions[?(@.type=="Ready")].status'
kubectl get namespace smoke-test-useful -o jsonpath='{.metadata.labels.ecr-pull-secret}{"\n"}'
kubectl -n smoke-test-useful get secret | grep -i ecr
```

Expected: ECR Repository SYNCED+READY; namespace label is `enabled`; an ECR pull secret exists in the namespace (Kyverno-generated).

- [ ] **Step C8.4: Clean up smoke test**

```bash
kubectl delete homelabapp.platform.asela.io smoke-test-useful -n platform-system
```

---

# Phase D — PR-D: HomelabApp v1.0 Full-fat (conditional S3 + Postgres)

Goal: Conditional resource bundles. `wantsS3=true` → S3 + IAM bundle. `wantsPostgres=true` → DB + Role + ExternalSecret.

---

## Task D1: TDD — write S3 bundle tests

**Files:**
- Modify: `base-apps/homelab-app-platform/function-source/tests/test_compose.py`

- [ ] **Step D1.1: Branch and add S3 tests**

```bash
cd /Users/arisela/git/kubernetes
git checkout main && git pull --ff-only origin main
git checkout -b feat/homelab-app-fullfat
```

Append to `tests/test_compose.py`:

```python
# ---- PR-D Full-fat: conditional S3 + Postgres ----

def _full_fat_spec(**overrides) -> dict:
    base = {
        "manifestPath": "base-apps/test-app",
        "ecrRepo": "test-app",
        "wantsS3": False,
        "wantsPostgres": False,
    }
    base.update(overrides)
    return base


def test_no_flags_emits_three_resources():
    req = make_request("x", _full_fat_spec())
    rsp = HomelabAppFunction().RunFunction(req, None)
    assert set(rsp.desired.resources.keys()) == {"namespace", "argo-app", "ecr-repo"}


def test_wants_s3_emits_s3_bundle():
    req = make_request("x", _full_fat_spec(wantsS3=True))
    rsp = HomelabAppFunction().RunFunction(req, None)

    s3_keys = {"s3-bucket", "s3-iam-user", "s3-access-key", "s3-policy", "s3-policy-attach"}
    assert s3_keys.issubset(set(rsp.desired.resources.keys()))

    bucket = get_resource(rsp, "s3-bucket")
    assert bucket["kind"] == "Bucket"
    assert bucket["apiVersion"] == "s3.aws.upbound.io/v1beta1"

    user = get_resource(rsp, "s3-iam-user")
    assert user["kind"] == "User"
    assert user["spec"]["forProvider"]["path"] == "/serviceaccounts/"

    key = get_resource(rsp, "s3-access-key")
    assert key["kind"] == "AccessKey"
    assert key["spec"]["writeConnectionSecretToRef"]["namespace"] == "x"
```

- [ ] **Step D1.2: Verify failing**

```bash
cd base-apps/homelab-app-platform/function-source
pytest tests/ -v
```

Expected: 7 prior tests pass; 2 new S3 tests fail.

---

## Task D2: Implement `make_s3_bundle`

**Files:**
- Modify: `base-apps/homelab-app-platform/function-source/main.py`

- [ ] **Step D2.1: Add the bundle builder**

Append to `main.py`:

```python
def make_s3_bundle(name: str, spec: dict) -> dict[str, dict]:
    """Return a dict of {resource-key: resource-body} for the S3 + IAM bundle."""
    bucket_name = f"{name}-bucket"
    user_name = f"{name}-s3-user"
    return {
        "s3-bucket": {
            "apiVersion": "s3.aws.upbound.io/v1beta1",
            "kind": "Bucket",
            "metadata": {"name": bucket_name},
            "spec": {
                "forProvider": {"region": AWS_REGION},
                "providerConfigRef": {"name": "default"},
            },
        },
        "s3-iam-user": {
            "apiVersion": "iam.aws.upbound.io/v1beta1",
            "kind": "User",
            "metadata": {"name": user_name},
            "spec": {
                "forProvider": {"path": "/serviceaccounts/"},
                "providerConfigRef": {"name": "default"},
            },
        },
        "s3-access-key": {
            "apiVersion": "iam.aws.upbound.io/v1beta1",
            "kind": "AccessKey",
            "metadata": {"name": f"{name}-s3-key"},
            "spec": {
                "forProvider": {
                    "userSelector": {
                        "matchControllerRef": True,
                        "matchLabels": {"crossplane.io/composite": name},
                    },
                },
                "writeConnectionSecretToRef": {
                    "name": f"{name}-s3-creds",
                    "namespace": name,
                },
                "providerConfigRef": {"name": "default"},
            },
        },
        "s3-policy": {
            "apiVersion": "iam.aws.upbound.io/v1beta1",
            "kind": "Policy",
            "metadata": {"name": f"{name}-s3-policy"},
            "spec": {
                "forProvider": {
                    "name": f"{name}-s3-access",
                    "policy": (
                        '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":'
                        '["s3:*"],"Resource":["arn:aws:s3:::' + bucket_name + '",'
                        '"arn:aws:s3:::' + bucket_name + '/*"]}]}'
                    ),
                },
                "providerConfigRef": {"name": "default"},
            },
        },
        "s3-policy-attach": {
            "apiVersion": "iam.aws.upbound.io/v1beta1",
            "kind": "UserPolicyAttachment",
            "metadata": {"name": f"{name}-s3-attach"},
            "spec": {
                "forProvider": {
                    "userSelector": {
                        "matchControllerRef": True,
                        "matchLabels": {"crossplane.io/composite": name},
                    },
                    "policyArnSelector": {
                        "matchControllerRef": True,
                        "matchLabels": {"crossplane.io/composite": name},
                    },
                },
                "providerConfigRef": {"name": "default"},
            },
        },
    }
```

- [ ] **Step D2.2: Wire `make_s3_bundle` into `RunFunction`**

In `RunFunction`, after the existing `if spec.get("ecrRepo")` block, add:

```python
        if spec.get("wantsS3"):
            for key, body in make_s3_bundle(name, spec).items():
                _set_resource(rsp, key, body)
```

- [ ] **Step D2.3: Run tests**

```bash
pytest tests/ -v
```

Expected: 9 tests pass (7 prior + 2 S3 tests).

- [ ] **Step D2.4: Commit**

```bash
cd /Users/arisela/git/kubernetes
git add base-apps/homelab-app-platform/function-source/
git commit -m "feat(homelab-app): add S3 conditional bundle (PR-D Full-fat WIP)"
```

---

## Task D3: TDD — write Postgres bundle tests

**Files:**
- Modify: `base-apps/homelab-app-platform/function-source/tests/test_compose.py`

- [ ] **Step D3.1: Append Postgres tests**

```python
def test_wants_postgres_emits_pg_bundle():
    req = make_request("x", _full_fat_spec(wantsPostgres=True))
    rsp = HomelabAppFunction().RunFunction(req, None)

    pg_keys = {"pg-database", "pg-role", "pg-creds-secret"}
    assert pg_keys.issubset(set(rsp.desired.resources.keys()))

    db = get_resource(rsp, "pg-database")
    assert db["kind"] == "Database"
    assert db["apiVersion"].startswith("postgresql.sql.crossplane.io")
    assert db["spec"]["forProvider"]["allowConnections"] is True

    role = get_resource(rsp, "pg-role")
    assert role["kind"] == "Role"
    assert role["spec"]["forProvider"]["privileges"]["login"] is True

    secret = get_resource(rsp, "pg-creds-secret")
    assert secret["kind"] == "ExternalSecret"
    assert secret["metadata"]["namespace"] == "x"
    assert secret["spec"]["target"]["name"] == "x-pg-creds"


def test_both_flags_emits_full_resource_set():
    req = make_request("x", _full_fat_spec(wantsS3=True, wantsPostgres=True))
    rsp = HomelabAppFunction().RunFunction(req, None)

    expected = {
        "namespace", "argo-app", "ecr-repo",
        "s3-bucket", "s3-iam-user", "s3-access-key", "s3-policy", "s3-policy-attach",
        "pg-database", "pg-role", "pg-creds-secret",
    }
    assert set(rsp.desired.resources.keys()) == expected
```

- [ ] **Step D3.2: Verify failing**

```bash
pytest tests/ -v
```

Expected: 9 prior pass; 2 new Postgres tests fail.

---

## Task D4: Implement `make_postgres_bundle`

**Files:**
- Modify: `base-apps/homelab-app-platform/function-source/main.py`

- [ ] **Step D4.1: Add the bundle builder**

Append to `main.py`:

```python
VAULT_KV_BASE = "kv/data"


def make_postgres_bundle(name: str, spec: dict) -> dict[str, dict]:
    """Return Postgres Database + Role + ExternalSecret resources."""
    db_name = name.replace("-", "_")
    role_name = db_name
    return {
        "pg-database": {
            "apiVersion": "postgresql.sql.crossplane.io/v1alpha1",
            "kind": "Database",
            "metadata": {"name": f"{name}-db"},
            "spec": {
                "forProvider": {"allowConnections": True},
                "providerConfigRef": {"name": "postgresql"},
            },
        },
        "pg-role": {
            "apiVersion": "postgresql.sql.crossplane.io/v1alpha1",
            "kind": "Role",
            "metadata": {"name": f"{name}-role"},
            "spec": {
                "forProvider": {
                    "privileges": {
                        "login": True,
                        "createDb": False,
                    },
                    "passwordSecretRef": {
                        "namespace": "crossplane-system",
                        "name": f"{name}-pg-password",
                        "key": "password",
                    },
                },
                "providerConfigRef": {"name": "postgresql"},
            },
        },
        "pg-creds-secret": {
            "apiVersion": "external-secrets.io/v1beta1",
            "kind": "ExternalSecret",
            "metadata": {
                "name": f"{name}-pg-creds",
                "namespace": name,
            },
            "spec": {
                "refreshInterval": "30s",
                "secretStoreRef": {
                    "kind": "ClusterSecretStore",
                    "name": "vault-backend",
                },
                "target": {
                    "name": f"{name}-pg-creds",
                    "creationPolicy": "Owner",
                },
                "data": [
                    {
                        "secretKey": "username",
                        "remoteRef": {"key": f"{VAULT_KV_BASE}/{name}/postgres", "property": "username"},
                    },
                    {
                        "secretKey": "password",
                        "remoteRef": {"key": f"{VAULT_KV_BASE}/{name}/postgres", "property": "password"},
                    },
                ],
            },
        },
    }
```

- [ ] **Step D4.2: Wire into `RunFunction`**

In `RunFunction`, after the `if spec.get("wantsS3")` block, add:

```python
        if spec.get("wantsPostgres"):
            for key, body in make_postgres_bundle(name, spec).items():
                _set_resource(rsp, key, body)
```

- [ ] **Step D4.3: Run tests**

```bash
cd base-apps/homelab-app-platform/function-source
pytest tests/ -v
```

Expected: 11 tests pass (all prior + 2 Postgres tests).

- [ ] **Step D4.4: Commit**

```bash
cd /Users/arisela/git/kubernetes
git add base-apps/homelab-app-platform/function-source/
git commit -m "feat(homelab-app): add Postgres conditional bundle (PR-D Full-fat)"
```

---

## Task D5: Update XRD with `v1alpha3` (additive)

**Files:**
- Modify: `base-apps/homelab-app-platform/xrd.yaml`

- [ ] **Step D5.1: Append v1alpha3 version**

Add to `spec.versions`:

```yaml
  - name: v1alpha3
    served: true
    referenceable: true   # this becomes the canonical version
    schema:
      openAPIV3Schema:
        type: object
        properties:
          spec:
            type: object
            properties:
              manifestPath:
                type: string
                description: GitOps repo path for this app's manifests.
              ecrRepo:
                type: string
                description: ECR repository name.
              wantsS3:
                type: boolean
                default: false
                description: Provision an S3 bucket + IAM bundle.
              wantsPostgres:
                type: boolean
                default: false
                description: Provision a Postgres database + role + ExternalSecret.
            required:
            - manifestPath
            - ecrRepo
          status:
            type: object
            properties:
              ready:
                type: boolean
```

- [ ] **Step D5.2: Set `referenceable: false` on v1alpha2**

Edit `v1alpha2` block — change `referenceable: false` (it was previously the canonical; v1alpha3 now is). The Composition's `compositeTypeRef` will be updated next.

- [ ] **Step D5.3: Update Composition to reference v1alpha3**

In `base-apps/homelab-app-platform/composition.yaml`:

```yaml
  compositeTypeRef:
    apiVersion: platform.asela.io/v1alpha3
    kind: HomelabApp
```

- [ ] **Step D5.4: kubectl diff**

```bash
kubectl diff -f base-apps/homelab-app-platform/xrd.yaml \
             -f base-apps/homelab-app-platform/composition.yaml 2>&1 | head -60
```

Expected: shows v1alpha3 added; Composition updated.

- [ ] **Step D5.5: Commit**

```bash
git add base-apps/homelab-app-platform/xrd.yaml \
        base-apps/homelab-app-platform/composition.yaml
git commit -m "feat(homelab-app): XRD v1alpha3 + Composition refers to it (PR-D)"
```

---

## Task D6: Build and push function-homelab-app:v0.3.0

**Files:**
- Modify: `base-apps/homelab-app-platform/function.yaml`

- [ ] **Step D6.1: Build and push**

```bash
cd base-apps/homelab-app-platform/function-source
make TAG=v0.3.0 build push
```

- [ ] **Step D6.2: Bump `function.yaml`**

Change `:v0.2.0 → :v0.3.0` in `base-apps/homelab-app-platform/function.yaml`.

- [ ] **Step D6.3: Commit**

```bash
cd /Users/arisela/git/kubernetes
git add base-apps/homelab-app-platform/function.yaml
git commit -m "chore(homelab-app): bump function image to v0.3.0"
```

---

## Task D7: Backstage source — extend template with checkboxes

**Files:**
- Modify: `$BACKSTAGE_SRC/templates/new-homelab-app/template.yaml`
- Modify: `$BACKSTAGE_SRC/templates/new-homelab-app/skeleton/{{ values.name }}-claim.yaml`

- [ ] **Step D7.1: Add the two boolean fields**

In `template.yaml`, add a second parameters page:

```yaml
    - title: Optional resources
      properties:
        wantsS3:
          title: Provision S3 bucket + IAM
          type: boolean
          default: false
        wantsPostgres:
          title: Provision Postgres database
          type: boolean
          default: false
```

Add to the `fetch:template` `values:` block:

```yaml
          wantsS3: ${{ parameters.wantsS3 }}
          wantsPostgres: ${{ parameters.wantsPostgres }}
```

- [ ] **Step D7.2: Update skeleton to v1alpha3**

```yaml
apiVersion: platform.asela.io/v1alpha3
kind: HomelabApp
metadata:
  name: ${{ values.name }}
  namespace: platform-system
spec:
  manifestPath: ${{ values.manifestPath }}
  ecrRepo: ${{ values.ecrRepo }}
  wantsS3: ${{ values.wantsS3 }}
  wantsPostgres: ${{ values.wantsPostgres }}
```

- [ ] **Step D7.3: Local-test and commit Backstage source**

```bash
cd "$BACKSTAGE_SRC"
yarn dev
# Open http://localhost:3000/create/templates/new-homelab-app
# Confirm the two checkboxes appear on the second page
git add templates/new-homelab-app
git commit -m "feat: extend new-homelab-app template with wantsS3/wantsPostgres (PR-D)"
```

---

## Task D8: Build and push backstage-portal:v1.4.0

**Files:**
- Modify: `base-apps/backstage/deployments.yaml`

- [ ] **Step D8.1: Build and push**

```bash
cd "$BACKSTAGE_SRC"
yarn build:all
docker build -t 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.4.0 \
  -f packages/backend/Dockerfile .
docker push 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.4.0
git push
```

- [ ] **Step D8.2: Bump deployment image**

In this repo, `base-apps/backstage/deployments.yaml`: `v1.3.0 → v1.4.0`.

```bash
cd /Users/arisela/git/kubernetes
git add base-apps/backstage/deployments.yaml
git commit -m "chore: bump backstage to v1.4.0 (with wantsS3/wantsPostgres)"
```

---

## Task D9: Open PR-D and verify post-merge

**Files:** none

- [ ] **Step D9.1: Push and open PR**

```bash
git push -u origin feat/homelab-app-fullfat
gh pr create --base main --head feat/homelab-app-fullfat \
  --title "feat(idp): HomelabApp v1.0 Full-fat — conditional S3 + Postgres (PR-D)" \
  --body "$(cat <<'EOF'
## Summary
- XRD v1alpha3 served + referenceable: adds wantsS3, wantsPostgres optional booleans
- Function image v0.3.0: emits S3 bundle (5 resources) when wantsS3, Postgres bundle (3 resources) when wantsPostgres
- 11 tests pass (4 Slim + 3 Useful + 2 S3 + 2 Postgres + dispatch sanity)
- Backstage portal v1.4.0 with checkboxes for both flags

## Post-merge verification (smoke test)
- [ ] Claim with both flags reaches all 11 composed resources READY=True
- [ ] Delete claim → clean cascade

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step D9.2: Wait for merge**

- [ ] **Step D9.3: Smoke test — full-fat claim**

```bash
cat <<EOF | kubectl apply -f -
apiVersion: platform.asela.io/v1alpha3
kind: HomelabApp
metadata:
  name: scratch-app
  namespace: platform-system
spec:
  manifestPath: base-apps/scratch-app
  ecrRepo: scratch-app
  wantsS3: true
  wantsPostgres: true
EOF
```

- [ ] **Step D9.4: Wait for full convergence (~2-3 min) and verify all 11 resources**

```bash
sleep 180
kubectl get -A \
  buckets.s3.aws.upbound.io \
  users.iam.aws.upbound.io \
  accesskeys.iam.aws.upbound.io \
  policies.iam.aws.upbound.io \
  userpolicyattachments.iam.aws.upbound.io \
  repositories.ecr.aws.upbound.io \
  databases.postgresql.sql.crossplane.io \
  roles.postgresql.sql.crossplane.io \
  externalsecrets.external-secrets.io \
  | grep scratch-app
```

Expected: all `scratch-app-*` resources `SYNCED=True, READY=True`. Plus the namespace `scratch-app` and the ArgoCD application:

```bash
kubectl get namespace scratch-app
kubectl -n argo-cd get application scratch-app
```

- [ ] **Step D9.5: Verify Backstage catalog graph**

Open the Backstage portal. Navigate to the `scratch-app` entity (you may need to register the catalog entity manually if Backstage doesn't auto-discover claims yet). The Crossplane tab should show the full resource graph with all 11 leaves.

- [ ] **Step D9.6: Clean up**

```bash
kubectl delete homelabapp.platform.asela.io scratch-app -n platform-system
sleep 60
kubectl get namespace scratch-app 2>&1 | grep -q NotFound && echo "namespace pruned"
kubectl -n argo-cd get application scratch-app 2>&1 | grep -q NotFound && echo "argo app pruned"
```

Expected: all 11 composed resources, the namespace, and the ArgoCD app are gone.

---

## Done

Final state:
- `HomelabApp` v1alpha3 is the canonical XRD
- `function-homelab-app:v0.3.0` runs all branches with 11 passing tests
- Backstage portal `v1.4.0` exposes the full template
- The TeraSky Crossplane tab visualizes claims and their composed resources

Spec acceptance criteria (§10) satisfied. Each PR's post-merge verification has been executed and signed off.

---

## Self-review (writer's notes — do not execute)

**Spec coverage:**
- Spec §3 target state (XRD, Composition, function-python pipeline, Backstage template, TeraSky plugins) → covered by Tasks A1-A7, B1-B11, C1-C8, D1-D9.
- Spec §5 file-level changes per PR → mapped 1:1 to task files.
- Spec §6 demo flow → exercised by smoke tests in B11, C8, D9.
- Spec §7 learning checkpoints → naturally hit per-PR.
- Spec §8 risks 1-7 → addressed via image-build gates (1, 2), pytest harness from PR-B onward (3), ExternalSecret + writeConnectionSecretToRef in D2/D4 (4), narrow ClusterRole in A6.4 (5), additive XRD versions in B5/C4/D5 (6), explicit upstream check in A1 (7).
- Spec §10 acceptance criteria → covered by D9.4-D9.6.

**Placeholder scan:** searched for "TBD", "TODO", "implement later", "fill in" — none present. All YAML and Python is concrete.

**Type consistency:**
- `make_namespace`, `make_argo_app`, `make_ecr_repo`, `make_s3_bundle`, `make_postgres_bundle` — used identically in tests and main.py.
- Resource keys (`namespace`, `argo-app`, `ecr-repo`, `s3-bucket`, etc.) — used identically across tests and bundle builders.
- XRD versions (`v1alpha1`, `v1alpha2`, `v1alpha3`) referenced consistently in XRD, Composition, skeleton claim, and tests.
