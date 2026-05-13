# Smoke-Test App — design spec

**Date:** 2026-05-13
**Status:** Approved for implementation planning
**Author:** Ari Sela (with Claude)

## Summary

Add a new IDP self-service capability: a Backstage scaffolder template + Crossplane Composition that provisions an **isolated vcluster** and an **ArgoCD-managed hello-world workload** (Deployment + Service + Ingress) inside it. Users fill a form, a PR opens with a single `SmokeTestApp` claim, merge triggers Crossplane to spin up the vcluster + register it with ArgoCD + sync the workload. Cleanup is a PR that removes the claim — Crossplane cascade-deletes everything. Intended for short-lived experimentation: try a container image, see it respond at `http://<host>.smoke.arigsela.com`, throw it away.

## Goals

- Self-service "spin up an isolated test environment from a container image" workflow accessible from the Backstage UI
- Strong isolation: each smoke test gets a dedicated vcluster (its own API server, RBAC, CRDs — not just a namespace inside `sandbox-1`)
- Workload reachable at a stable LAN URL for `curl`/browser testing
- Deletion is a single PR; nothing leaks

## Non-goals (v1)

The following are deliberately out of scope. Each is a credible v1.1 follow-up.

- **TTL / auto-cleanup.** Stale smoke tests stay until manually deleted.
- **TLS on the workload Ingress.** Plain HTTP only — LAN-scoped ephemeral test workload.
- **Custom workload shape beyond image/port/host/replicas.** No env vars, volumes, probes, ConfigMaps, multiple containers, etc. — if you need any of that, use `XApplication`.
- **Vault/ESO inside the smoke-test vcluster.** No secret injection.
- **Backstage catalog entries for smoke tests.** Smoke tests don't get a `catalog-info.yaml`.
- **Status-reflected-in-Backstage UI.** The XR `status.phase` is set, but Backstage doesn't surface it without further plugin work.
- **Public access via Cloudflare Tunnel.** LAN-only via `/etc/hosts` (or LAN DNS) pattern, same as `*.vcluster.arigsela.com`.

## Design decisions (locked during brainstorming)

| Question | Decision |
|---|---|
| Isolation level | Dedicated vcluster per smoke test |
| Workload delivery into vcluster | ArgoCD with the vcluster registered as an external cluster |
| Lifecycle | Manual delete (no TTL) |
| Where workload manifests live | A curated Helm chart in this repo: `charts/smoke-test-workload/` |
| Cluster registration mechanic | Crossplane Composition emits the ArgoCD cluster Secret (read vcluster's kubeconfig via `function-extra-resources`) |
| Deletion UX | Reuse existing `crossplane:teardown:open-decommission-pr` action if generic; small fork if hardcoded to `XApplication` |
| DNS pattern | `<host>.smoke.arigsela.com` via `/etc/hosts` (or LAN DNS wildcard) → `10.0.1.50` |
| Workload Ingress | Created **inside** the vcluster, synced to host (`sync.toHost.ingresses.enabled: true`) |

## Architecture

```
User → Backstage form (smoke-test/template.yaml)
         │  name, image, host, port?, replicas?
         ▼
       Scaffolder → publish:github:pull-request
         │  PR with two files:
         │    base-apps/smoke-test-<name>.yaml         (ArgoCD Application)
         │    base-apps/smoke-test-<name>/claim.yaml   (SmokeTestApp claim)
         ▼
       PR merged → master-app picks up the Application → applies the claim
         │
         ▼
       Crossplane runs composition-smoke-test (function-python + function-extra-resources)
         │
         │ FIRST RECONCILE — vc-<name> kubeconfig secret doesn't exist yet
         ▼
       Emits on HOST cluster:
         1. Namespace: vcluster-<name>
         2. ArgoCD Application: vcluster-<name>
              source: charts.loft.sh chart vcluster v0.34.0
              values override: sync.toHost.ingresses.enabled: true
         │ vcluster pod boots, writes Secret vc-<name> with kubeconfig
         │
         │ SECOND RECONCILE — function-extra-resources finds vc-<name>
         ▼
       Emits on HOST cluster:
         3. Secret: argo-cd/cluster-<name>            (ArgoCD cluster registration)
              label: argocd.argoproj.io/secret-type=cluster
              stringData.server: https://vcluster.vcluster-<name>.svc:443
              stringData.config: TLS material from vc-<name>
         4. ArgoCD Application: smoke-<name>-workload
              source: this repo's charts/smoke-test-workload (Helm)
              values: image, port, host, replicas from XR claim
              destination.name: smoke-<name>           (matches Secret stringData.name)
         │
         ▼
       ArgoCD syncs the workload Application → talks to vcluster API
       via in-cluster svc → renders Helm chart → creates
       Deployment + Service + Ingress inside the vcluster
         │
         ▼
       vcluster syncer pushes Ingress to host (sync.toHost.ingresses.enabled: true)
       Host nginx-ingress picks it up at <host>.smoke.arigsela.com
         │
         ▼
       User curls http://<host>.smoke.arigsela.com → response from workload
```

**Why ArgoCD-to-vcluster uses in-cluster service URL, not public ingress:** ArgoCD can present a client cert (from `vc-<name>` kubeconfig material) to authenticate. The in-cluster vcluster Service is reachable without DNS or TLS-passthrough gymnastics. The `*.vcluster.arigsela.com` ingress path is for human kubectl access only.

**Why `sync.toHost.ingresses.enabled: true` for smoke-test vclusters:** `sandbox-1` uses `false` because we don't want user-controlled ingresses leaking to the host nginx. For smoke tests, that leak is precisely the point — it's how `<host>.smoke.arigsela.com` becomes reachable.

## Components

### 1. XRD — `XSmokeTestApp` (namespaced)

Path: `base-apps/crossplane-compositions/xrd-smoke-test.yaml`

Tiny schema:

```yaml
apiVersion: apiextensions.crossplane.io/v1
kind: CompositeResourceDefinition
metadata:
  name: xsmoketestapps.platform.arigsela.com
spec:
  group: platform.arigsela.com
  names:
    kind: XSmokeTestApp
    plural: xsmoketestapps
  claimNames:
    kind: SmokeTestApp
    plural: smoketestapps
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
              required: [image, host]
              properties:
                image: { type: string }
                host:
                  type: string
                  pattern: '^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'
                  description: Subdomain label — workload reachable at <host>.smoke.arigsela.com
                port:
                  type: integer
                  default: 80
                  minimum: 1
                  maximum: 65535
                replicas:
                  type: integer
                  default: 1
                  minimum: 1
                  maximum: 3
            status:
              type: object
              properties:
                vclusterURL: { type: string }
                ingressURL: { type: string }
                phase:
                  type: string
                  enum: [Provisioning, Ready, Failed]
```

**Claim shape:**
```yaml
apiVersion: platform.arigsela.com/v1alpha1
kind: SmokeTestApp
metadata:
  name: hello-world
  namespace: default
spec:
  image: nginx
  host: hello
  port: 80
```

### 2. Composition — `composition-smoke-test`

Path: `base-apps/crossplane-compositions/composition-smoke-test.yaml`

Pipeline-mode Composition with two functions:
- `function-python` (existing) — same upstream as the XApplication Composition uses
- `function-extra-resources` (NEW) — installed alongside `function-python` in `base-apps/crossplane-functions/`

The Python step:
- Reads the XR claim (image, host, port, replicas)
- Receives output from `function-extra-resources` request for `Secret/vc-<name>` in namespace `vcluster-<name>`
- Phase 1 (always): emits Namespace + vcluster ArgoCD Application
- Phase 2 (when vc-<name> secret exists): also emits ArgoCD cluster Secret + workload Application
- Updates `status.phase`: `Provisioning` (phase 1 only), `Ready` (phase 2 emitted)

**vcluster Application values** (shape mirrors `base-apps/vcluster-sandbox-1.yaml`):

```yaml
controlPlane:
  backingStore:
    etcd:
      embedded: { enabled: false }
  statefulSet:
    persistence:
      volumeClaim: { enabled: false }
    resources:
      limits:   { cpu: 1, memory: 512Mi }
      requests: { cpu: 100m, memory: 256Mi }
  service:
    spec: { type: ClusterIP }
sync:
  toHost:
    ingresses: { enabled: true }     # KEY DIFFERENCE from sandbox-1
    services:  { enabled: true }
  fromHost:
    storageClasses: { enabled: true }
```

No `controlPlane.proxy.extraSANs`, no Certificate, no host-side Ingress for the vcluster API — smoke-test vclusters aren't externally reachable; ArgoCD uses the in-cluster Service.

**ArgoCD cluster Secret** (phase 2):

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: cluster-<name>
  namespace: argo-cd
  labels:
    argocd.argoproj.io/secret-type: cluster
    smoketest.platform.arigsela.com/owner: <claim-name>
type: Opaque
stringData:
  name: smoke-<name>
  server: https://vcluster.vcluster-<name>.svc:443
  config: |
    { "tlsClientConfig": { "insecure": false, "caData": "...", "certData": "...", "keyData": "..." } }
```

The Python function base64-decodes `vc-<name>.data.config` (kubeconfig YAML), parses it, base64-encodes the cert/key/CA fields, and assembles the JSON config payload.

**Workload Application** (phase 2):

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: smoke-<name>-workload
  namespace: argo-cd
spec:
  project: default
  destination:
    name: smoke-<name>                 # matches Secret stringData.name
    namespace: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: charts/smoke-test-workload
    helm:
      releaseName: smoke
      valuesObject:
        image: <image>
        port: <port>
        host: <host>.smoke.arigsela.com
        replicas: <replicas>
  syncPolicy:
    automated: { prune: true, selfHeal: true }
    syncOptions: [CreateNamespace=true, ServerSideApply=true]
```

### 3. Curated Helm chart — `charts/smoke-test-workload/`

Path: new directory at repo root (the existing repo has no top-level `charts/` directory yet — this introduces the pattern).

```
charts/smoke-test-workload/
├── Chart.yaml
├── values.yaml
└── templates/
    ├── deployment.yaml
    ├── service.yaml
    └── ingress.yaml
```

**Chart.yaml:**
```yaml
apiVersion: v2
name: smoke-test-workload
description: Minimal hello-world workload for IDP smoke tests
type: application
version: 0.1.0
appVersion: "1.0"
```

**values.yaml** (defaults):
```yaml
image: ""
port: 80
host: ""
replicas: 1
```

**templates/deployment.yaml:**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: smoke-app
  labels:
    app.kubernetes.io/name: smoke-app
    app.kubernetes.io/managed-by: smoke-test-workload
spec:
  replicas: {{ .Values.replicas }}
  selector:
    matchLabels:
      app.kubernetes.io/name: smoke-app
  template:
    metadata:
      labels:
        app.kubernetes.io/name: smoke-app
    spec:
      containers:
        - name: app
          image: {{ .Values.image | quote }}
          ports:
            - name: http
              containerPort: {{ .Values.port }}
          resources:
            requests: { cpu: 50m, memory: 64Mi }
            limits:   { cpu: 500m, memory: 256Mi }
```

**templates/service.yaml:**
```yaml
apiVersion: v1
kind: Service
metadata:
  name: smoke-app
spec:
  type: ClusterIP
  ports:
    - name: http
      port: 80
      targetPort: {{ .Values.port }}
  selector:
    app.kubernetes.io/name: smoke-app
```

**templates/ingress.yaml:**
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: smoke-app
spec:
  ingressClassName: nginx
  rules:
    - host: {{ .Values.host | quote }}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service: { name: smoke-app, port: { number: 80 } }
```

**Hardcoded values inside the chart (intentional):**
- Resource limits 50m/64Mi requests, 500m/256Mi limits — prevents smoke tests from becoming load tests
- No probes, ConfigMaps, Secrets, PVCs, env vars — smoke tests prove "image runs and Service routes", nothing more
- Resource names hardcoded to `smoke-app` — each smoke test gets its own vcluster, namespace handles isolation

### 4. Backstage scaffolder template

Paths:
- `docs/reference/backstage/examples/templates/smoke-test/template.yaml` — template metadata, parameters, steps
- `docs/reference/backstage/examples/templates/smoke-test/content/base-apps/smoke-test-${{ values.name }}.yaml` — ArgoCD Application Nunjucks template
- `docs/reference/backstage/examples/templates/smoke-test/content/base-apps/smoke-test-${{ values.name }}/claim.yaml` — XR claim Nunjucks template

Template form fields (mirrors `application/template.yaml` shape):

| Field | Type | Validation |
|---|---|---|
| `name` | string | `^[a-z]([-a-z0-9]*[a-z0-9])?$`, max 30 |
| `owner` | string (OwnerPicker) | Group or User from catalog |
| `image` | string | required |
| `host` | string | `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`, max 30 |
| `port` | integer | default 80 |
| `replicas` | integer | default 1, min 1, max 3 |

Steps:
1. `fetch:template` with parameters as values
2. `publish:github:pull-request` with branch `smoke-test/${name}`, PR title `feat(smoke-test): provision ${name}`

Content files written by the scaffolder:

**`base-apps/smoke-test-${name}.yaml`** — ArgoCD Application following the master-app convention:
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: smoke-test-${name}
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/smoke-test-${name}
  destination:
    server: https://kubernetes.default.svc
    namespace: default
  syncPolicy:
    automated: { prune: true, selfHeal: true }
    syncOptions: [CreateNamespace=true]
```

**`base-apps/smoke-test-${name}/claim.yaml`** — the XR claim:
```yaml
apiVersion: platform.arigsela.com/v1alpha1
kind: SmokeTestApp
metadata:
  name: ${name}
  namespace: default
  annotations:
    backstage.io/kubernetes-id: ${name}
    terasky.backstage.io/owner: ${owner}
spec:
  image: ${image}
  host: ${host}
  port: ${port}
  replicas: ${replicas}
```

### 5. Decommission UX

Reuses `crossplane:teardown:open-decommission-pr` if generic. **Implementation plan must verify this first** — if hardcoded to `XApplication`, two options:
- (a) Parameterize the existing action to accept XR kind as input (preferred — Backstage image rebuild)
- (b) Add a parallel action specifically for `XSmokeTestApp` (more code, less invasive)

Either way the user-facing UX is identical: a "Decommission smoke test" template in Backstage that opens a PR removing `base-apps/smoke-test-<name>.yaml` and the `smoke-test-<name>/` directory.

### 6. Crossplane Function dependency — `function-extra-resources`

New file: `base-apps/crossplane-functions/function-extra-resources.yaml`

```yaml
apiVersion: pkg.crossplane.io/v1
kind: Function
metadata:
  name: function-extra-resources
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  package: xpkg.upbound.io/crossplane-contrib/function-extra-resources:v0.0.x
```

Pin a specific version once installed. No other platform changes required.

## Cascade-delete semantics

When the SmokeTestApp claim is deleted (via PR + ArgoCD prune):

1. Crossplane sees XR deletion
2. Cascade-deletes all 4 managed resources:
   - **Workload Application** → ArgoCD prunes Deployment/Service/Ingress from vcluster (Ingress sync to host also removed)
   - **ArgoCD cluster Secret** → ArgoCD forgets the vcluster
   - **vcluster Application** → ArgoCD prunes vcluster Helm release → StatefulSet, Service, syncer pod gone
   - **Namespace `vcluster-<name>`** → finalizers clean up anything residual

No orphans. No imperative bootstrapping/teardown Jobs.

## Cross-cutting concerns

### ECR / Kyverno
ECR images Just Work. Synced Pods on the host trigger the existing `inject-ecr-pull-secret` ClusterPolicy; the `generate-ecr-secret` policy already clones `ecr-registry` into the new namespace on creation. No extra wiring.

### DNS
User adds `*.smoke.arigsela.com → 10.0.1.50` wildcard to LAN DNS resolver (Pi-hole/router) once. `/etc/hosts` works as quick-start, one line per smoke test.

### Resource cost
Per smoke test: vcluster pod ≈150 MiB + workload (up to 3 replicas × 256 MiB limit). Typical ≈ 200–400 MiB. Worth noting in user docs so platform doesn't fill up unnoticed.

### Status reflection
Composition writes back `status.vclusterURL`, `status.ingressURL`, `status.phase` to the XR. Backstage doesn't surface this without further plugin work — out of scope for v1.

## Risks & open questions for the implementation plan to resolve

1. **`crossplane:teardown:open-decommission-pr` is generic vs. hardcoded.** Implementation plan reads its source first, branches accordingly.
2. **`function-extra-resources` version pin.** Need to verify latest stable and pin during implementation.
3. **Python function complexity.** Parsing the vcluster kubeconfig YAML and producing the ArgoCD cluster Secret JSON is non-trivial. Plan includes a focused unit-test pass.
4. **Two-phase reconcile latency.** Vcluster pod boot takes ~30s. The smoke test is "Ready" ~45-60s after the PR merge. Acceptable but worth documenting expected wall-clock.
5. **First-time consumer of `function-extra-resources`.** May need a small validation Composition + claim cycle to verify the function works in your environment before wiring it into the smoke-test Composition.

## Success criteria

- [ ] `XSmokeTestApp` claim accepted by API (XRD installed)
- [ ] PR scaffolded by Backstage `smoke-test-app` template merges cleanly
- [ ] Within ~60s of merge, `vcluster-<name>` namespace + vcluster pod Running
- [ ] Within ~90s of merge, ArgoCD cluster Secret `cluster-<name>` exists in `argo-cd` namespace and shows as a healthy cluster in the ArgoCD UI
- [ ] Workload Application `smoke-<name>-workload` Synced/Healthy
- [ ] `curl http://<host>.smoke.arigsela.com` returns the expected response (e.g. nginx welcome page for `image: nginx`)
- [ ] Decommission PR merges → all 4 Crossplane managed resources cascade-delete → namespace gone, no orphaned Secrets in `argo-cd` ns
- [ ] No regressions in existing `XApplication` or `vcluster-sandbox-1` deployments

## Implementation phasing (high-level — for writing-plans)

1. **Foundation:** Install `function-extra-resources`. Validate with a trivial test Composition that reads a known secret and emits another.
2. **Chart:** Add `charts/smoke-test-workload/` with templates. Lint and render-test locally.
3. **XRD + Composition (phase 1 only):** XRD installed, Composition that emits just the vcluster bits. Verify a manually-applied claim provisions a vcluster end-to-end (no cluster registration yet).
4. **Composition phase 2:** Add `function-extra-resources` step + Python logic to emit ArgoCD cluster Secret and workload Application. Verify end-to-end: claim → vcluster → registration → workload Sync.
5. **Backstage template:** Add scaffolder template, register in `app-config.yaml`, rebuild Backstage image. Validate end-to-end from the UI.
6. **Decommission:** Verify `crossplane:teardown:open-decommission-pr`. Adjust or fork as needed.
7. **Docs:** Runbook at `docs/reference/smoke-test/README.md` mirroring the vcluster runbook pattern.
