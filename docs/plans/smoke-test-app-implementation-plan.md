# Smoke-Test App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Phase 0 (not started) — 0/7 phases complete
**Last Updated:** 2026-05-13
**Spec:** [docs/superpowers/specs/2026-05-13-smoke-test-app-design.md](../superpowers/specs/2026-05-13-smoke-test-app-design.md) (PR #268 — must be merged before starting Phase 1)
**Related:** [vcluster implementation plan](./vcluster-implementation-plan.md) for the underlying vcluster platform

## Goal

Add a Backstage-fronted IDP feature that, from a single form, provisions an isolated vcluster + a parameterized hello-world workload (Deployment + Service + Ingress) inside it, all delivered via Crossplane + ArgoCD. Deletion is one PR with cascade-delete by Crossplane.

## Architecture (recap)

User fills Backstage form → PR with `SmokeTestApp` claim → Crossplane Composition emits: Namespace + ArgoCD Application for vcluster Helm chart → vcluster pod boots → function-extra-resources picks up the generated kubeconfig Secret → Composition's second reconcile emits ArgoCD cluster Secret (registering the vcluster) + ArgoCD Application for the workload (source = curated Helm chart in this repo). Cascade-delete on XR removal.

## Tech Stack

- Crossplane Composition (pipeline mode) with `function-python` v0.4.0 + `function-extra-resources` v0.3.0
- vcluster Helm chart v0.34.0 (already proven via sandbox-1)
- Helm chart `charts/smoke-test-workload/` in this repo
- Backstage scaffolder template (Nunjucks → publish:github:pull-request)
- ArgoCD cluster registration via standard `argocd.argoproj.io/secret-type: cluster` Secret

## Success Criteria

- [ ] Backstage `smoke-test-app` template renders a form, submits a PR with two files
- [ ] On merge, master-app creates `smoke-test-<name>` Application, ArgoCD applies the `SmokeTestApp` claim
- [ ] Within ~60s, Crossplane emits Namespace + vcluster Application; vcluster pod Running
- [ ] Within ~90s of merge, ArgoCD cluster Secret `cluster-<name>` exists; cluster shows as healthy in ArgoCD UI
- [ ] Workload Application `smoke-<name>-workload` Synced/Healthy
- [ ] `curl http://<host>.smoke.arigsela.com` returns the workload response (e.g. nginx welcome page for `image: nginx`)
- [ ] Deleting the `SmokeTestApp` claim cascades through Crossplane → all 4 managed resources gone → no orphans in `argo-cd` namespace
- [ ] `tests/composition/` has rendered test cases for both reconcile phases

## Risks & Open Questions (must resolve during implementation)

1. **`crossplane:teardown:open-decommission-pr` generic-ness** — Phase 6 reads its source; forks only if hardcoded to `XApplication`.
2. **kubeconfig parsing complexity** — Phase 4's Python script must base64-decode the secret, parse YAML, and reformat. Phase 4 includes a unit-test step before wiring into the Composition.
3. **Two-phase reconcile timing** — vcluster pod boot is ~30s. Tasks include explicit waits with timeouts.
4. **First-time use of `function-extra-resources` in this cluster** — Phase 1 validates with a trivial test before depending on it for the smoke-test Composition.

---

## Phase 0: Prerequisites

### Task 0.1: Spec PR is merged

**Steps:**

- [ ] Confirm PR #268 (smoke-test-app design spec) is merged into `main`:

  ```bash
  git -C /Users/arisela/git/kubernetes checkout main
  git -C /Users/arisela/git/kubernetes pull --ff-only origin main
  ls docs/superpowers/specs/2026-05-13-smoke-test-app-design.md
  # Expected: file exists
  ```

- [ ] Verify ArgoCD CRD types are available (used in emitted resources):

  ```bash
  kubectl api-resources --api-group=argoproj.io | grep -i application
  # Expected: applications, applicationsets entries
  ```

### Task 0.2: Verify existing IDP precedents

**Steps:**

- [ ] Confirm XApplication composition + function-python are healthy:

  ```bash
  kubectl get composition application -o jsonpath='{.metadata.name}{"\n"}'
  # Expected: application
  kubectl get function function-python -o jsonpath='{.status.conditions[?(@.type=="Healthy")].status}{"\n"}'
  # Expected: True
  ```

- [ ] Confirm sandbox-1 vcluster is healthy (we'll mirror its Helm values):

  ```bash
  kubectl -n vcluster-sandbox-1 get pods -l app=vcluster
  # Expected: 1/1 Running
  ```

---

## Phase 1: Install `function-extra-resources` + validate

### Task 1.1: Add the Function resource

**Files:** Create: `base-apps/crossplane-functions/function-extra-resources.yaml`

**Steps:**

- [ ] Create the manifest:

  ```yaml
  # base-apps/crossplane-functions/function-extra-resources.yaml
  # Reads existing K8s resources during Composition rendering so the
  # smoke-test Composition can pick up vcluster's generated kubeconfig
  # secret on the second reconcile pass.
  apiVersion: pkg.crossplane.io/v1
  kind: Function
  metadata:
    name: function-extra-resources
    annotations:
      argocd.argoproj.io/sync-wave: "1"
  spec:
    package: xpkg.upbound.io/crossplane-contrib/function-extra-resources:v0.3.0
    packagePullPolicy: IfNotPresent
    revisionActivationPolicy: Automatic
    revisionHistoryLimit: 1
  ```

- [ ] On a feature branch, commit and push:

  ```bash
  git -C /Users/arisela/git/kubernetes checkout -b feat/smoke-test-foundation
  git -C /Users/arisela/git/kubernetes add base-apps/crossplane-functions/function-extra-resources.yaml
  git -C /Users/arisela/git/kubernetes commit -m "feat(crossplane): add function-extra-resources for smoke-test composition"
  git -C /Users/arisela/git/kubernetes push -u origin feat/smoke-test-foundation
  ```

- [ ] Open PR via `gh pr create` or `mcp__github__create_pull_request`. Merge after review.

### Task 1.2: Verify function installed

**Steps:**

- [ ] After merge + ArgoCD reconcile:

  ```bash
  kubectl -n argo-cd patch application crossplane-functions --type merge \
    -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'
  sleep 15
  kubectl get function function-extra-resources -o jsonpath='{.status.conditions[?(@.type=="Healthy")].status}{"\n"}'
  # Expected: True
  ```

### Task 1.3: Quick sanity check on the function (optional)

The function is well-tested upstream. Recommended quick check: just confirm the function pod is running with no startup errors. Skip the full integration test — Phase 4 will exercise the function end-to-end with real data.

**Steps:**

- [ ] Look at the function-extra-resources runtime pod:

  ```bash
  kubectl -n crossplane-system get pods -l pkg.crossplane.io/function=function-extra-resources
  # Expected: a single pod, status Running
  kubectl -n crossplane-system logs -l pkg.crossplane.io/function=function-extra-resources --tail=20
  # Expected: no errors; "Listening on" or similar startup line
  ```

- [ ] If the pod isn't Running, troubleshoot before proceeding to Phase 2. Common causes: image pull failure (check `kubectl describe`), Function CRD version mismatch (check Crossplane core version compatibility).

---

## Phase 2: Curated workload chart `charts/smoke-test-workload/`

### Task 2.1: Create the chart structure

**Files:** Create:
- `charts/smoke-test-workload/Chart.yaml`
- `charts/smoke-test-workload/values.yaml`
- `charts/smoke-test-workload/templates/deployment.yaml`
- `charts/smoke-test-workload/templates/service.yaml`
- `charts/smoke-test-workload/templates/ingress.yaml`
- `charts/smoke-test-workload/.helmignore`
- `charts/smoke-test-workload/README.md`

**Steps:**

- [ ] On a new feature branch:

  ```bash
  git -C /Users/arisela/git/kubernetes checkout main && git -C /Users/arisela/git/kubernetes pull --ff-only origin main
  git -C /Users/arisela/git/kubernetes checkout -b feat/smoke-test-workload-chart
  mkdir -p charts/smoke-test-workload/templates
  ```

- [ ] `Chart.yaml`:

  ```yaml
  apiVersion: v2
  name: smoke-test-workload
  description: Minimal hello-world workload for IDP smoke tests
  type: application
  version: 0.1.0
  appVersion: "1.0"
  ```

- [ ] `values.yaml`:

  ```yaml
  # Image to deploy (required — no default)
  image: ""
  # Container port the Service routes to
  port: 80
  # Full hostname for the Ingress (Composition passes <host>.smoke.arigsela.com)
  host: ""
  # Pod replica count
  replicas: 1
  ```

- [ ] `templates/deployment.yaml`:

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

- [ ] `templates/service.yaml`:

  ```yaml
  apiVersion: v1
  kind: Service
  metadata:
    name: smoke-app
    labels:
      app.kubernetes.io/name: smoke-app
  spec:
    type: ClusterIP
    ports:
      - name: http
        port: 80
        targetPort: {{ .Values.port }}
    selector:
      app.kubernetes.io/name: smoke-app
  ```

- [ ] `templates/ingress.yaml`:

  ```yaml
  apiVersion: networking.k8s.io/v1
  kind: Ingress
  metadata:
    name: smoke-app
    labels:
      app.kubernetes.io/name: smoke-app
  spec:
    ingressClassName: nginx
    rules:
      - host: {{ .Values.host | quote }}
        http:
          paths:
            - path: /
              pathType: Prefix
              backend:
                service:
                  name: smoke-app
                  port:
                    number: 80
  ```

- [ ] `.helmignore`:

  ```
  .DS_Store
  README.md
  ```

- [ ] `README.md`:

  ```markdown
  # smoke-test-workload chart

  Minimal Helm chart consumed by the `XSmokeTestApp` Crossplane Composition.
  Templates a hello-world Deployment + Service + Ingress with these values:

  | Value | Type | Default | Description |
  |---|---|---|---|
  | `image` | string | _required_ | Container image |
  | `port` | integer | 80 | Container port |
  | `host` | string | _required_ | Ingress hostname (full FQDN) |
  | `replicas` | integer | 1 | Pod replicas |

  Not for direct user consumption — the Composition fills these values from
  the `SmokeTestApp` claim.
  ```

### Task 2.2: Helm lint + render-test locally

**Steps:**

- [ ] Lint the chart:

  ```bash
  helm lint charts/smoke-test-workload
  # Expected: 1 chart(s) linted, 0 chart(s) failed
  ```

- [ ] Render with sample values to make sure templates produce valid YAML:

  ```bash
  helm template smoke-test charts/smoke-test-workload \
    --set image=nginx \
    --set host=hello.smoke.arigsela.com \
    --set port=80 \
    --set replicas=1
  # Expected: valid Deployment, Service, Ingress YAML output
  ```

- [ ] Pipe the rendered output to `kubectl apply --dry-run=client -f -` to catch schema errors:

  ```bash
  helm template smoke-test charts/smoke-test-workload \
    --set image=nginx --set host=hello.smoke.arigsela.com \
    | kubectl apply --dry-run=client -f -
  # Expected: deployment.apps/smoke-app created (dry run), service/smoke-app created (dry run), ingress.networking.k8s.io/smoke-app created (dry run)
  ```

### Task 2.3: Commit + PR

**Steps:**

- [ ] Commit:

  ```bash
  git -C /Users/arisela/git/kubernetes add charts/smoke-test-workload
  git -C /Users/arisela/git/kubernetes commit -m "feat(smoke-test): add curated workload Helm chart"
  git -C /Users/arisela/git/kubernetes push -u origin feat/smoke-test-workload-chart
  ```

- [ ] Open and merge PR.

---

## Phase 3: XRD + Composition Phase-1 (vcluster only, no workload yet)

This phase produces a working `SmokeTestApp` claim that provisions a vcluster but does NOT yet register it with ArgoCD or deploy the workload. Splitting Phase 3 / Phase 4 gives a safe checkpoint where we can confirm the simpler emit path works end-to-end before adding the kubeconfig parsing complexity.

### Task 3.1: Create the XRD

**Files:** Create: `base-apps/crossplane-compositions/xrd-smoke-test.yaml`

**Steps:**

- [ ] New branch:

  ```bash
  git -C /Users/arisela/git/kubernetes checkout main && git -C /Users/arisela/git/kubernetes pull --ff-only origin main
  git -C /Users/arisela/git/kubernetes checkout -b feat/smoke-test-xrd-composition
  ```

- [ ] Write the XRD:

  ```yaml
  # base-apps/crossplane-compositions/xrd-smoke-test.yaml
  # XSmokeTestApp — namespaced XR for IDP smoke-test app provisioning.
  apiVersion: apiextensions.crossplane.io/v1
  kind: CompositeResourceDefinition
  metadata:
    name: xsmoketestapps.platform.arigsela.com
    annotations:
      argocd.argoproj.io/sync-wave: "2"
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
                  image:
                    type: string
                    description: Container image to deploy (e.g. nginx, ghcr.io/me/app:v1)
                  host:
                    type: string
                    pattern: '^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'
                    maxLength: 30
                    description: Subdomain label — ingress at <host>.smoke.arigsela.com
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

### Task 3.2: Create the Composition (Phase-1 only)

**Files:** Create: `base-apps/crossplane-compositions/composition-smoke-test.yaml`

This version emits only Phase 1 resources (Namespace + vcluster Application) and stamps `status.phase: Provisioning`. Phase 4 adds the second function step.

**Steps:**

- [ ] Write the Composition:

  ```yaml
  # base-apps/crossplane-compositions/composition-smoke-test.yaml
  # Smoke-test app Composition. Pipeline-mode.
  # Phase 1 (this file): emits Namespace + vcluster Application only.
  # Phase 2 adds function-extra-resources step + ArgoCD cluster Secret + workload App.
  apiVersion: apiextensions.crossplane.io/v1
  kind: Composition
  metadata:
    name: smoke-test
    annotations:
      argocd.argoproj.io/sync-wave: "3"
  spec:
    compositeTypeRef:
      apiVersion: platform.arigsela.com/v1alpha1
      kind: XSmokeTestApp
    mode: Pipeline
    pipeline:
      - step: render-resources
        functionRef:
          name: function-python
        input:
          apiVersion: python.fn.crossplane.io/v1beta1
          kind: Script
          script: |
            import json
            from crossplane.function import resource

            def compose(req, rsp):
                spec = req.observed.composite.resource.get("spec", {})
                name = req.observed.composite.resource.get("metadata", {}).get("name", "unknown")
                ns_name = f"vcluster-{name}"

                # 1. Namespace
                resource.update(rsp.desired.resources["namespace"], {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": ns_name,
                        "labels": {
                            "app.kubernetes.io/managed-by": "crossplane",
                            "smoketest.platform.arigsela.com/owner": name,
                        },
                    },
                })

                # 2. ArgoCD Application for the vcluster Helm chart
                vcluster_app = {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {
                        "name": f"vcluster-{name}",
                        "namespace": "argo-cd",
                        "labels": {
                            "smoketest.platform.arigsela.com/owner": name,
                        },
                    },
                    "spec": {
                        "project": "default",
                        "destination": {
                            "server": "https://kubernetes.default.svc",
                            "namespace": ns_name,
                        },
                        "source": {
                            "repoURL": "https://charts.loft.sh",
                            "chart": "vcluster",
                            "targetRevision": "0.34.0",
                            "helm": {
                                "releaseName": "vcluster",
                                "valuesObject": {
                                    "controlPlane": {
                                        "backingStore": {"etcd": {"embedded": {"enabled": False}}},
                                        "statefulSet": {
                                            "persistence": {"volumeClaim": {"enabled": False}},
                                            "resources": {
                                                "limits": {"cpu": 1, "memory": "512Mi"},
                                                "requests": {"cpu": "100m", "memory": "256Mi"},
                                            },
                                        },
                                        "service": {"spec": {"type": "ClusterIP"}},
                                    },
                                    "sync": {
                                        "toHost": {
                                            "ingresses": {"enabled": True},
                                            "services":  {"enabled": True},
                                        },
                                        "fromHost": {"storageClasses": {"enabled": True}},
                                    },
                                },
                            },
                        },
                        "syncPolicy": {
                            "automated": {"prune": True, "selfHeal": True},
                            "syncOptions": ["CreateNamespace=true", "ServerSideApply=true"],
                        },
                    },
                }
                resource.update(rsp.desired.resources["vcluster-application"], vcluster_app)

                # Stamp status
                rsp.desired.composite.resource.setdefault("status", {}).update({
                    "phase": "Provisioning",
                    "vclusterURL": f"https://vcluster.{ns_name}.svc:443",
                    "ingressURL": f"http://{spec.get('host','')}.smoke.arigsela.com",
                })
  ```

### Task 3.3: Register the Composition with the master-app

`base-apps/crossplane-compositions.yaml` is the parent ArgoCD Application — verify the new files are picked up by its directory source. The composition directory is already watched, so no change needed; verify by listing:

**Steps:**

- [ ] Confirm the parent Application points at the directory:

  ```bash
  kubectl -n argo-cd get application crossplane-compositions -o jsonpath='{.spec.source.path}{"\n"}'
  # Expected: base-apps/crossplane-compositions
  ```

  If empty/different, the new XRD + Composition won't sync. Stop and update `base-apps/crossplane-compositions.yaml` first.

### Task 3.4: Commit + PR + verify

**Steps:**

- [ ] Commit and push:

  ```bash
  git -C /Users/arisela/git/kubernetes add base-apps/crossplane-compositions/
  git -C /Users/arisela/git/kubernetes commit -m "feat(smoke-test): add XSmokeTestApp XRD + phase-1 Composition"
  git -C /Users/arisela/git/kubernetes push origin feat/smoke-test-xrd-composition
  ```

- [ ] Open PR; merge.

- [ ] After merge + ArgoCD sync, verify XRD installed:

  ```bash
  kubectl get xrd xsmoketestapps.platform.arigsela.com
  # Expected: shows ESTABLISHED=True, OFFERED=True
  kubectl api-resources --api-group=platform.arigsela.com | grep -i smoke
  # Expected: smoketestapps, xsmoketestapps
  ```

### Task 3.5: Manually apply a test claim, verify vcluster comes up

This validates the Composition before adding the workload-delivery complexity.

**Steps:**

- [ ] Apply a test claim directly (NOT via Backstage yet):

  ```bash
  cat <<'EOF' | kubectl apply -f -
  apiVersion: platform.arigsela.com/v1alpha1
  kind: SmokeTestApp
  metadata:
    name: phase3-test
    namespace: default
  spec:
    image: nginx
    host: phase3-test
    port: 80
  EOF
  ```

- [ ] Watch for the vcluster to come up:

  ```bash
  kubectl get xsmoketestapp -A
  # Expected: phase3-test, status.phase=Provisioning
  for i in {1..30}; do
    kubectl -n vcluster-phase3-test get pods 2>/dev/null | grep -q Running && break
    sleep 5; echo "${i}*5s waiting..."
  done
  kubectl -n vcluster-phase3-test get pods
  # Expected: vcluster-... 1/1 Running
  ```

- [ ] Confirm the in-cluster vcluster API is reachable:

  ```bash
  kubectl -n vcluster-phase3-test get secret vc-vcluster
  # Expected: secret exists with data fields
  ```

- [ ] Clean up the test claim (so it doesn't linger):

  ```bash
  kubectl delete smoketestapp phase3-test
  # Watch the namespace get pruned (~60s)
  kubectl get ns vcluster-phase3-test 2>&1 | head -2
  # Eventually: Error from server (NotFound)
  ```

---

## Phase 4: Composition Phase-2 (cluster registration + workload App)

Add the `function-extra-resources` step that reads `Secret/vc-vcluster` from the vcluster namespace, parses the kubeconfig, and emits both the ArgoCD cluster Secret and the workload Application.

### Task 4.1: Update Composition with second function step

**Files:** Modify: `base-apps/crossplane-compositions/composition-smoke-test.yaml`

**Steps:**

- [ ] New branch:

  ```bash
  git -C /Users/arisela/git/kubernetes checkout main && git -C /Users/arisela/git/kubernetes pull --ff-only origin main
  git -C /Users/arisela/git/kubernetes checkout -b feat/smoke-test-phase2
  ```

- [ ] Replace the Composition's `pipeline:` block. Add a new step **before** `render-resources` that fetches the vcluster kubeconfig secret:

  ```yaml
  spec:
    # ... compositeTypeRef and mode: Pipeline unchanged ...
    pipeline:
      - step: fetch-vcluster-secret
        functionRef:
          name: function-extra-resources
        input:
          apiVersion: extra-resources.fn.crossplane.io/v1beta1
          kind: Input
          spec:
            extraResources:
              - kind: ExtraResources
                into: vcluster-kubeconfig
                type: Reference
                ref:
                  apiVersion: v1
                  kind: Secret
                  # name is `vc-<release-name>`; we deploy with releaseName: vcluster (fixed)
                  name: vc-vcluster
                  # namespace is per-XR — function-extra-resources templates from XR
                  namespace: vcluster-{{ index .observed.composite.resource.metadata.name }}
      - step: render-resources
        functionRef:
          name: function-python
        input:
          apiVersion: python.fn.crossplane.io/v1beta1
          kind: Script
          script: |
            # PHASE 2 SCRIPT - replaces the Phase 1 script entirely (full code below)
  ```

  Replace the Phase 1 script with the Phase 2 script below.

### Task 4.2: Phase-2 Python script (complete code)

**Files:** Modify: `base-apps/crossplane-compositions/composition-smoke-test.yaml` (the inline `script:` block)

The script extends Phase 1 by also emitting the cluster Secret and workload Application when the vcluster kubeconfig is available. Until it's available (first reconcile), only Phase-1 resources are emitted and `status.phase` stays `Provisioning`.

- [ ] Replace the `script: |` content with this:

  ```python
  import base64
  import json
  import yaml
  from crossplane.function import resource

  def find_extra(req, into_name):
      """Return the first resource fetched by function-extra-resources under
      the given 'into' name, or None if not present (first-pass reconcile)."""
      ers = req.context.get("apiextensions.crossplane.io/extra-resources") or {}
      bucket = ers.get(into_name) or []
      return bucket[0] if bucket else None

  def parse_vcluster_kubeconfig(secret_obj):
      """Given a K8s Secret object whose data.config is a base64-encoded
      kubeconfig YAML, return (ca_b64, cert_b64, key_b64) — each is the
      base64-encoded PEM (same encoding the ArgoCD cluster Secret JSON wants)."""
      data = secret_obj.get("data", {})
      kubeconfig_b64 = data.get("config")
      if not kubeconfig_b64:
          return None, None, None
      kubeconfig_yaml = base64.b64decode(kubeconfig_b64).decode("utf-8")
      kc = yaml.safe_load(kubeconfig_yaml)
      cluster = kc["clusters"][0]["cluster"]
      user = kc["users"][0]["user"]
      return (
          cluster.get("certificate-authority-data"),
          user.get("client-certificate-data"),
          user.get("client-key-data"),
      )

  def compose(req, rsp):
      spec = req.observed.composite.resource.get("spec", {})
      name = req.observed.composite.resource.get("metadata", {}).get("name", "unknown")
      ns_name = f"vcluster-{name}"
      host = spec.get("host", "")
      ingress_host = f"{host}.smoke.arigsela.com"

      # ---------- Phase 1 resources (always emitted) ----------
      resource.update(rsp.desired.resources["namespace"], {
          "apiVersion": "v1",
          "kind": "Namespace",
          "metadata": {
              "name": ns_name,
              "labels": {
                  "app.kubernetes.io/managed-by": "crossplane",
                  "smoketest.platform.arigsela.com/owner": name,
              },
          },
      })

      vcluster_app = {
          "apiVersion": "argoproj.io/v1alpha1",
          "kind": "Application",
          "metadata": {
              "name": f"vcluster-{name}",
              "namespace": "argo-cd",
              "labels": {"smoketest.platform.arigsela.com/owner": name},
          },
          "spec": {
              "project": "default",
              "destination": {
                  "server": "https://kubernetes.default.svc",
                  "namespace": ns_name,
              },
              "source": {
                  "repoURL": "https://charts.loft.sh",
                  "chart": "vcluster",
                  "targetRevision": "0.34.0",
                  "helm": {
                      "releaseName": "vcluster",
                      "valuesObject": {
                          "controlPlane": {
                              "backingStore": {"etcd": {"embedded": {"enabled": False}}},
                              "statefulSet": {
                                  "persistence": {"volumeClaim": {"enabled": False}},
                                  "resources": {
                                      "limits": {"cpu": 1, "memory": "512Mi"},
                                      "requests": {"cpu": "100m", "memory": "256Mi"},
                                  },
                              },
                              "service": {"spec": {"type": "ClusterIP"}},
                          },
                          "sync": {
                              "toHost": {
                                  "ingresses": {"enabled": True},
                                  "services":  {"enabled": True},
                              },
                              "fromHost": {"storageClasses": {"enabled": True}},
                          },
                      },
                  },
              },
              "syncPolicy": {
                  "automated": {"prune": True, "selfHeal": True},
                  "syncOptions": ["CreateNamespace=true", "ServerSideApply=true"],
              },
          },
      }
      resource.update(rsp.desired.resources["vcluster-application"], vcluster_app)

      # ---------- Phase 2 resources (emitted only when vcluster secret exists) ----------
      vcluster_secret = find_extra(req, "vcluster-kubeconfig")
      phase = "Provisioning"

      if vcluster_secret:
          ca_b64, cert_b64, key_b64 = parse_vcluster_kubeconfig(vcluster_secret)
          if ca_b64 and cert_b64 and key_b64:
              # 3. ArgoCD cluster Secret
              cluster_name = f"smoke-{name}"
              config_json = json.dumps({
                  "tlsClientConfig": {
                      "insecure": False,
                      "caData":   ca_b64,
                      "certData": cert_b64,
                      "keyData":  key_b64,
                  },
              })
              cluster_secret = {
                  "apiVersion": "v1",
                  "kind": "Secret",
                  "metadata": {
                      "name": f"cluster-{name}",
                      "namespace": "argo-cd",
                      "labels": {
                          "argocd.argoproj.io/secret-type": "cluster",
                          "smoketest.platform.arigsela.com/owner": name,
                      },
                  },
                  "type": "Opaque",
                  "stringData": {
                      "name": cluster_name,
                      "server": f"https://vcluster.{ns_name}.svc:443",
                      "config": config_json,
                  },
              }
              resource.update(rsp.desired.resources["argocd-cluster-secret"], cluster_secret)

              # 4. ArgoCD Application for the workload Helm chart
              workload_app = {
                  "apiVersion": "argoproj.io/v1alpha1",
                  "kind": "Application",
                  "metadata": {
                      "name": f"smoke-{name}-workload",
                      "namespace": "argo-cd",
                      "labels": {"smoketest.platform.arigsela.com/owner": name},
                  },
                  "spec": {
                      "project": "default",
                      "destination": {
                          "name": cluster_name,
                          "namespace": "default",
                      },
                      "source": {
                          "repoURL": "https://github.com/arigsela/kubernetes",
                          "targetRevision": "main",
                          "path": "charts/smoke-test-workload",
                          "helm": {
                              "releaseName": "smoke",
                              "valuesObject": {
                                  "image": spec.get("image", ""),
                                  "port": spec.get("port", 80),
                                  "host": ingress_host,
                                  "replicas": spec.get("replicas", 1),
                              },
                          },
                      },
                      "syncPolicy": {
                          "automated": {"prune": True, "selfHeal": True},
                          "syncOptions": ["CreateNamespace=true", "ServerSideApply=true"],
                      },
                  },
              }
              resource.update(rsp.desired.resources["workload-application"], workload_app)
              phase = "Ready"

      # ---------- Status ----------
      rsp.desired.composite.resource.setdefault("status", {}).update({
          "phase": phase,
          "vclusterURL": f"https://vcluster.{ns_name}.svc:443",
          "ingressURL": f"http://{ingress_host}",
      })
  ```

### Task 4.3: Add render-test cases

**Files:** Create:
- `tests/composition/xr-smoke-test-minimal.yaml` (XR input)
- `tests/composition/expected-smoke-test-phase1.yaml` (expected output when extra-resources finds no secret)
- `tests/composition/expected-smoke-test-phase2.yaml` (expected output when extra-resources finds a secret)
- `tests/composition/observed-vcluster-secret.yaml` (the fake "observed" Secret to pass to crossplane render)

**Steps:**

- [ ] Sample XR (`xr-smoke-test-minimal.yaml`):

  ```yaml
  apiVersion: platform.arigsela.com/v1alpha1
  kind: XSmokeTestApp
  metadata:
    name: render-test
  spec:
    image: nginx
    host: render-test
    port: 80
    replicas: 1
  ```

- [ ] Generate a sample vcluster kubeconfig secret. The cert data can be dummy — just needs to parse:

  ```yaml
  # tests/composition/observed-vcluster-secret.yaml
  apiVersion: v1
  kind: Secret
  metadata:
    name: vc-vcluster
    namespace: vcluster-render-test
  type: Opaque
  data:
    # base64-encoded fake kubeconfig (decoded below for reference)
    # apiVersion: v1
    # kind: Config
    # clusters:
    # - cluster:
    #     certificate-authority-data: ZmFrZS1jYQ==
    #     server: https://vcluster.vcluster-render-test:443
    #   name: vcluster
    # users:
    # - user:
    #     client-certificate-data: ZmFrZS1jZXJ0
    #     client-key-data: ZmFrZS1rZXk=
    #   name: vcluster
    # contexts:
    # - context: { cluster: vcluster, user: vcluster }
    #   name: vcluster
    # current-context: vcluster
    config: YXBpVmVyc2lvbjogdjEKa2luZDogQ29uZmlnCmNsdXN0ZXJzOgotIGNsdXN0ZXI6CiAgICBjZXJ0aWZpY2F0ZS1hdXRob3JpdHktZGF0YTogWm1Gclpf
  ```

  Easier: write the kubeconfig file as plain YAML, then base64 it inline:

  ```bash
  KUBECONFIG_YAML='apiVersion: v1
  kind: Config
  clusters:
  - cluster:
      certificate-authority-data: ZmFrZS1jYQ==
      server: https://vcluster.vcluster-render-test:443
    name: vcluster
  users:
  - user:
      client-certificate-data: ZmFrZS1jZXJ0
      client-key-data: ZmFrZS1rZXk=
    name: vcluster
  contexts:
  - context: { cluster: vcluster, user: vcluster }
    name: vcluster
  current-context: vcluster'
  echo "$KUBECONFIG_YAML" | base64 | tr -d '\n'
  # Use the output as the `data.config` value
  ```

- [ ] Update `tests/composition/functions.yaml` to add function-extra-resources:

  ```yaml
  # append:
  ---
  apiVersion: pkg.crossplane.io/v1
  kind: Function
  metadata:
    name: function-extra-resources
    annotations:
      render.crossplane.io/runtime: Docker
  spec:
    package: xpkg.upbound.io/crossplane-contrib/function-extra-resources:v0.3.0
  ```

- [ ] Run the render test:

  ```bash
  crossplane render \
    tests/composition/xr-smoke-test-minimal.yaml \
    base-apps/crossplane-compositions/composition-smoke-test.yaml \
    tests/composition/functions.yaml \
    --observed-resources=tests/composition/observed-vcluster-secret.yaml
  # Expected: emits all 4 resources (Namespace, vcluster App, ArgoCD cluster Secret, workload App)
  ```

- [ ] Capture the output as `expected-smoke-test-phase2.yaml`. Then re-run without `--observed-resources` to get phase-1 only output → `expected-smoke-test-phase1.yaml`.

### Task 4.4: Commit + PR + verify

**Steps:**

- [ ] Commit and PR:

  ```bash
  git -C /Users/arisela/git/kubernetes add base-apps/crossplane-compositions/composition-smoke-test.yaml tests/composition/
  git -C /Users/arisela/git/kubernetes commit -m "feat(smoke-test): add Composition phase-2 (cluster registration + workload)"
  git -C /Users/arisela/git/kubernetes push origin feat/smoke-test-phase2
  ```

- [ ] Merge after review.

### Task 4.5: End-to-end test with a real claim

**Steps:**

- [ ] Apply a test claim:

  ```bash
  cat <<'EOF' | kubectl apply -f -
  apiVersion: platform.arigsela.com/v1alpha1
  kind: SmokeTestApp
  metadata:
    name: phase4-test
    namespace: default
  spec:
    image: nginx
    host: phase4-test
    port: 80
  EOF
  ```

- [ ] Add the LAN DNS entry for testing:

  ```bash
  sudo sh -c "echo '10.0.1.50 phase4-test.smoke.arigsela.com' >> /etc/hosts"
  ```

- [ ] Watch the phases:

  ```bash
  for i in {1..30}; do
    phase=$(kubectl get xsmoketestapp phase4-test -o jsonpath='{.status.phase}' 2>/dev/null)
    echo "${i}*5s: phase=$phase"
    [ "$phase" = "Ready" ] && break
    sleep 5
  done
  kubectl -n argo-cd get app | grep phase4-test
  # Expected: vcluster-phase4-test Synced Healthy
  # Expected: smoke-phase4-test-workload Synced Healthy
  ```

- [ ] Verify ArgoCD sees the new cluster:

  ```bash
  kubectl -n argo-cd get secret cluster-phase4-test
  # Expected: secret exists with label argocd.argoproj.io/secret-type=cluster
  ```

- [ ] Curl the workload:

  ```bash
  curl -sS http://phase4-test.smoke.arigsela.com/ | head -3
  # Expected: <!DOCTYPE html> ... (nginx welcome page)
  ```

- [ ] Clean up:

  ```bash
  kubectl delete smoketestapp phase4-test
  sudo sed -i '' '/phase4-test.smoke.arigsela.com/d' /etc/hosts  # macOS; on Linux drop the ''
  ```

- [ ] Verify cascade-delete: all 4 managed resources gone after ~60s:

  ```bash
  kubectl -n argo-cd get app | grep phase4-test
  # Expected: no output
  kubectl -n argo-cd get secret | grep cluster-phase4-test
  # Expected: no output
  kubectl get ns vcluster-phase4-test 2>&1 | head -2
  # Eventually: Error from server (NotFound)
  ```

---

## Phase 5: Backstage scaffolder template

### Task 5.1: Create the template directory

**Files:** Create:
- `docs/reference/backstage/examples/templates/smoke-test/template.yaml`
- `docs/reference/backstage/examples/templates/smoke-test/content/base-apps/smoke-test-${{ values.name }}.yaml`
- `docs/reference/backstage/examples/templates/smoke-test/content/base-apps/smoke-test-${{ values.name }}/claim.yaml`

**Steps:**

- [ ] New branch:

  ```bash
  git -C /Users/arisela/git/kubernetes checkout main && git -C /Users/arisela/git/kubernetes pull --ff-only origin main
  git -C /Users/arisela/git/kubernetes checkout -b feat/smoke-test-backstage-template
  mkdir -p "docs/reference/backstage/examples/templates/smoke-test/content/base-apps"
  ```

- [ ] `docs/reference/backstage/examples/templates/smoke-test/template.yaml`:

  ```yaml
  apiVersion: scaffolder.backstage.io/v1beta3
  kind: Template
  metadata:
    name: smoke-test-app
    title: Smoke-test app
    description: Spin up an isolated vcluster + hello-world workload for smoke testing
    tags: [kubernetes, vcluster, smoke-test]
  spec:
    owner: platform
    type: service
    parameters:
      - title: Identity
        required: [name, owner]
        properties:
          name:
            title: Name
            type: string
            description: Lower-case, kebab-case (e.g. payment-api-experiment)
            pattern: '^[a-z]([-a-z0-9]*[a-z0-9])?$'
            maxLength: 30
          owner:
            title: Owner
            type: string
            ui:field: OwnerPicker
            ui:options:
              catalogFilter:
                kind: [Group, User]
      - title: Workload
        required: [image, host]
        properties:
          image:
            title: Container image
            type: string
            description: e.g. nginx, ghcr.io/me/my-app:v1
          host:
            title: Hostname label
            type: string
            description: Reachable at <label>.smoke.arigsela.com
            pattern: '^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'
            maxLength: 30
          port:
            title: Container port
            type: integer
            default: 80
          replicas:
            title: Replicas
            type: integer
            default: 1
            minimum: 1
            maximum: 3
    steps:
      - id: template
        name: Render manifests
        action: fetch:template
        input:
          url: ./content
          values:
            name: ${{ parameters.name }}
            owner: ${{ parameters.owner }}
            image: ${{ parameters.image }}
            host: ${{ parameters.host }}
            port: ${{ parameters.port }}
            replicas: ${{ parameters.replicas }}
      - id: publish
        name: Open PR
        action: publish:github:pull-request
        input:
          repoUrl: github.com?owner=arigsela&repo=kubernetes
          branchName: smoke-test/${{ parameters.name }}
          title: 'feat(smoke-test): provision ${{ parameters.name }}'
          description: |
            Smoke-test app `${{ parameters.name }}`
              * image: `${{ parameters.image }}`
              * URL: `http://${{ parameters.host }}.smoke.arigsela.com`
            Crossplane will provision a dedicated vcluster + ArgoCD Application
            pointing at `charts/smoke-test-workload`. Merge to deploy.
    output:
      links:
        - title: PR
          url: ${{ steps.publish.output.remoteUrl }}
  ```

- [ ] Content file 1 — `docs/reference/backstage/examples/templates/smoke-test/content/base-apps/smoke-test-${{ values.name }}.yaml`:

  ```yaml
  apiVersion: argoproj.io/v1alpha1
  kind: Application
  metadata:
    name: smoke-test-${{ values.name }}
    namespace: argo-cd
  spec:
    project: default
    source:
      repoURL: https://github.com/arigsela/kubernetes
      targetRevision: main
      path: base-apps/smoke-test-${{ values.name }}
    destination:
      server: https://kubernetes.default.svc
      namespace: default
    syncPolicy:
      automated:
        prune: true
        selfHeal: true
      syncOptions:
        - CreateNamespace=true
  ```

- [ ] Content file 2 — `docs/reference/backstage/examples/templates/smoke-test/content/base-apps/smoke-test-${{ values.name }}/claim.yaml`:

  ```yaml
  apiVersion: platform.arigsela.com/v1alpha1
  kind: SmokeTestApp
  metadata:
    name: ${{ values.name }}
    namespace: default
    annotations:
      backstage.io/kubernetes-id: ${{ values.name }}
      terasky.backstage.io/owner: ${{ values.owner }}
  spec:
    image: ${{ values.image }}
    host: ${{ values.host }}
    port: ${{ values.port }}
    replicas: ${{ values.replicas }}
  ```

### Task 5.2: Register the template in app-config

**Files:** Modify: Backstage `app-config.yaml` (likely in a separate `arigsela/backstage` repo — confirm with the application template registration as a reference)

**Steps:**

- [ ] Look up the existing application template registration in the Backstage repo's `app-config.yaml`:

  ```bash
  # In the Backstage repo (NOT the kubernetes repo)
  cd ~/git/backstage  # path may differ
  grep -A2 "examples/templates/application" app-config.yaml
  ```

- [ ] Add a similar entry for the smoke-test template:

  ```yaml
  catalog:
    locations:
      # ... existing ...
      - type: file
        target: /examples/templates/smoke-test/template.yaml
        rules:
          - allow: [Template]
  ```

- [ ] Commit in the Backstage repo, rebuild the image, redeploy.

### Task 5.3: Commit kubernetes-repo changes + PR

**Steps:**

- [ ] Commit and PR the template files in the kubernetes repo:

  ```bash
  git -C /Users/arisela/git/kubernetes add docs/reference/backstage/examples/templates/smoke-test/
  git -C /Users/arisela/git/kubernetes commit -m "feat(backstage): add smoke-test-app scaffolder template"
  git -C /Users/arisela/git/kubernetes push origin feat/smoke-test-backstage-template
  ```

- [ ] Merge after review.

### Task 5.4: End-to-end UI test

**Steps:**

- [ ] In Backstage UI, navigate to "Create" → confirm "Smoke-test app" template appears

- [ ] Fill the form:
  - name: `ui-test`
  - owner: (any group)
  - image: `nginx`
  - host: `ui-test`
  - port: 80
  - replicas: 1

- [ ] Submit, follow the PR link, verify the PR contains:
  - `base-apps/smoke-test-ui-test.yaml` (Application)
  - `base-apps/smoke-test-ui-test/claim.yaml` (XR claim)

- [ ] Add `10.0.1.50 ui-test.smoke.arigsela.com` to /etc/hosts

- [ ] Merge the PR. Within ~90s:

  ```bash
  kubectl get xsmoketestapp ui-test -o jsonpath='{.status.phase}'
  # Expected: Ready

  curl -sS http://ui-test.smoke.arigsela.com/ | head -3
  # Expected: nginx welcome
  ```

- [ ] Leave it running for now — Phase 6 uses it for decommission testing.

---

## Phase 6: Decommission action

### Task 6.1: Read existing decommission action source

**Files:** Read: `~/git/backstage/packages/backend/src/modules/scaffolder/<decommission-action-file>`

The exact file name and registration are in the Backstage repo per CLAUDE.md. The action is registered as `crossplane:teardown:open-decommission-pr`.

**Steps:**

- [ ] Locate the file:

  ```bash
  cd ~/git/backstage
  grep -rl "crossplane:teardown" packages/backend/src/
  # Note the file path
  ```

- [ ] Open the file and identify what XR kind it operates on. Two possibilities:
  - **Generic:** takes `kind` as an input parameter — no code change needed
  - **Hardcoded** to `XApplication` — fork to a new action or parameterize

### Task 6.2 (a): If action is generic — add a new template only

**Files:** Create: `docs/reference/backstage/examples/templates/smoke-test-decommission/template.yaml`

**Steps:**

- [ ] Mirror the existing `decommission/template.yaml` shape but pass `XSmokeTestApp` as the kind input. Read the existing decommission template at `docs/reference/backstage/examples/templates/decommission/template.yaml` as a starting point.

### Task 6.2 (b): If action is hardcoded — parameterize or fork

**Files:** Modify (Backstage repo): the action source file

**Steps:**

- [ ] Add a `kind` parameter to the action's input schema; thread it through to the PR-content generation logic.

- [ ] Rebuild + redeploy the Backstage image.

- [ ] Then proceed with 6.2 (a).

### Task 6.3: Verify decommission flow

**Steps:**

- [ ] In Backstage UI, find the `ui-test` smoke test (created in Task 5.4), trigger the decommission action.

- [ ] Verify the PR removes:
  - `base-apps/smoke-test-ui-test.yaml`
  - `base-apps/smoke-test-ui-test/claim.yaml`

- [ ] Merge the PR. Within ~60s verify cascade-delete:

  ```bash
  kubectl -n argo-cd get app | grep ui-test           # expected: empty
  kubectl -n argo-cd get secret cluster-ui-test       # expected: NotFound
  kubectl get ns vcluster-ui-test 2>&1 | head -2      # eventually: NotFound
  ```

---

## Phase 7: Docs + plan closeout

### Task 7.1: Create the runbook

**Files:** Create: `docs/reference/smoke-test/README.md`

**Steps:**

- [ ] Write the runbook with these sections:

  1. **What this is** — Backstage-fronted vcluster + hello-world for smoke testing
  2. **Prerequisites** — vcluster CLI optional; `/etc/hosts` entry for `*.smoke.arigsela.com → 10.0.1.50` (or LAN DNS wildcard)
  3. **Creating a smoke test** — Backstage UI flow, expected wall-clock (~60-90s after PR merge)
  4. **Verifying it works** — curl command, ArgoCD UI cluster list, status.phase check
  5. **Deleting** — decommission template flow
  6. **Troubleshooting**:
     - `phase: Provisioning` for >2 minutes → check function-extra-resources logs in `crossplane-system`
     - Workload Application can't reach cluster → check `cluster-<name>` Secret JSON config validity
     - Ingress 404 → confirm `sync.toHost.ingresses.enabled: true` in vcluster Helm values; check synced Ingress exists in host `vcluster-<name>` namespace
     - PolicyReports failures → audit-only Kyverno policies, informational
  7. **Limits** — capped replicas, fixed resource limits, no TLS, no persistence
  8. **Cost model** — ~200-400 MiB host memory per smoke test

### Task 7.2: Update the plan status + open closeout PR

**Files:** Modify: `docs/plans/smoke-test-app-implementation-plan.md` (this file)

**Steps:**

- [ ] At the top of this file change `Status: Phase 0 (not started)` → `Status: ✅ All phases complete (7/7)`. Update `Last Updated` to today.

- [ ] Add a "Post-execution notes" section at the bottom capturing any deviations from this plan (e.g. function-extra-resources version bumps, decommission action fork details, kubeconfig parsing surprises).

- [ ] Commit and PR:

  ```bash
  git -C /Users/arisela/git/kubernetes checkout -b docs/smoke-test-closeout
  git -C /Users/arisela/git/kubernetes add docs/reference/smoke-test/ docs/plans/smoke-test-app-implementation-plan.md
  git -C /Users/arisela/git/kubernetes commit -m "docs(smoke-test): add runbook and mark implementation complete"
  git -C /Users/arisela/git/kubernetes push origin docs/smoke-test-closeout
  ```

---

## Appendix A — Useful one-liners

```bash
# List all running smoke tests
kubectl get smoketestapp -A

# Check a smoke test's phase + URLs
kubectl get smoketestapp <name> -o jsonpath='{.status}{"\n"}' | jq

# Find the underlying vcluster pod
kubectl -n vcluster-<name> get pods -l app=vcluster

# Inspect Crossplane Composition reconcile state
kubectl describe xsmoketestapp <name>

# Find the workload pods on the host (synced down from the vcluster)
kubectl -n vcluster-<name> get pods -l vcluster.loft.sh/managed-by=vcluster
```

## Appendix B — Rollback procedure

If Phase 4 goes badly wrong:

1. Revert PR #(Phase 4 PR) — restores Composition to Phase 1 only.
2. Existing claims keep their Phase 1 resources (namespace + vcluster); status drops to Provisioning.
3. Manually delete affected smoketestapp claims if you want to clean up.

If Phase 1 goes wrong (function-extra-resources fails to install or breaks something):

1. Revert PR #(Phase 1 PR).
2. function-extra-resources is removed from the cluster. function-python is untouched. Existing XApplication composition continues working.

To remove the entire feature:

1. Decommission all smoketestapp claims first.
2. Revert PRs in reverse order (Phase 7 → Phase 1).
3. Crossplane cleans up XRD + Composition; ArgoCD prunes the chart directory.
