# vcluster on the homelab — design spec

**Date:** 2026-05-12
**Status:** Approved for implementation planning
**Author:** Ari Sela (with Claude)

## Summary

Add [vcluster](https://www.vcluster.com/) (OSS) to the GitOps repo so I can spin up ephemeral, fully-isolated Kubernetes test environments on top of the existing host cluster. The host cluster's platform (Vault, Istio ambient, Kyverno, cert-manager, nginx-ingress) stays untouched; vclusters are lightweight (k3s + sqlite, no PVC), accessed via ingress with TLS passthrough on a dedicated subdomain (`*.vcluster.arigsela.com`), and created/destroyed primarily through the vcluster CLI. One reference vcluster (`sandbox-1`) is committed to `base-apps/` to validate the end-to-end pattern.

## Goals

- Run ephemeral Kubernetes API servers as sandboxes — quick to create, cheap to destroy, isolated from production workloads
- Test Helm charts, operators, and CRDs that would otherwise pollute the host cluster
- Reuse existing platform infrastructure (cert-manager, nginx-ingress, GitOps) rather than introduce parallel tooling
- Have one committed reference instance proving the pattern works end-to-end

## Non-goals (v1)

The following are deliberately out of scope. Each can be added later without redesigning anything in this spec.

- **Vault/ESO from inside vclusters.** Test workloads use stub data, not real secrets.
- **Istio ambient enrollment** of vcluster namespaces. Avoids ztunnel intercepting the vcluster API server traffic during early debugging.
- **Per-vcluster host-cluster RBAC.** Single user (me) — no need to scope tenant access yet.
- **Sleep/resume automation.** Manual `vcluster pause`/`resume` is fine for CLI-created vclusters; the GitOps sample is always-on.
- **Backup/restore of vcluster state.** Ephemeral by definition.
- **vCluster Platform (loft.sh) UI.** Possible follow-up if the CLI-only workflow feels limiting.
- **Multi-tenant policy enforcement** (Capsule, etc.). Not needed for solo homelab use.

## Use case driving the design

Ephemeral dev/test sandboxes. Optimized for fast create/destroy, not long-lived isolation or multi-tenancy. This is the single design input that shaped every choice below.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          Host Cluster                            │
│                                                                  │
│  ┌─────────────────┐    ┌────────────────────────────────────┐   │
│  │  nginx-ingress  │◀── │  ClusterIssuer: letsencrypt-route53│   │
│  │  (ssl-pass-     │    │  Per-vcluster cert via DNS-01      │   │
│  │   through ON)   │    │  (Route 53)                        │   │
│  └────────┬────────┘    └────────────────────────────────────┘   │
│           │ TLS passthrough on host header                       │
│           ▼                                                      │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  Namespace: vcluster-sandbox-1                           │    │
│  │  ┌──────────────────────────────────────────────────┐    │    │
│  │  │  StatefulSet: vcluster-sandbox-1                 │    │    │
│  │  │  (k3s API server + syncer + sqlite, 1 pod)       │    │    │
│  │  └──────────────────────────────────────────────────┘    │    │
│  │  Service: vcluster (ClusterIP, 443)                      │    │
│  │  Ingress: sandbox-1.vcluster.arigsela.com (passthrough)  │    │
│  │  Certificate: vcluster-tls (cert-manager)                │    │
│  │                                                          │    │
│  │  Workloads created in the vcluster sync DOWN as real     │    │
│  │  pods in THIS namespace (Kyverno policies still apply).  │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

### Key choices

| Aspect | Choice | Why |
|---|---|---|
| Virtualization tech | vcluster OSS Helm chart | Lightest option, matches existing Helm-via-ArgoCD pattern, no extra operator |
| Control-plane distro | k3s (vcluster default) | Smallest footprint for ephemeral use |
| Backing store | sqlite, no PVC | Ephemeral by design; state loss on pod restart is acceptable |
| One namespace per vcluster | Yes | vcluster convention; gives Kyverno + cert-manager natural scoping |
| API access | nginx-ingress with `ssl-passthrough` | Stable hostnames; kubectl talks to real K8s API with proper TLS |
| TLS | Per-vcluster cert-manager Certificate | Cleanest GitOps story; no secret replication across namespaces |
| DNS | LAN-resolved `*.vcluster.arigsela.com` → node IP | Cloudflare Tunnel terminates TLS — cannot be used for passthrough |
| Istio ambient | Vcluster namespaces NOT enrolled | Keeps API server traffic out of ztunnel for simpler debugging |
| Kyverno | Existing policies apply unchanged | Synced pods are real pods on the host; ECR pull-secret injection works for free |
| ArgoCD Application shape | Multi-source (Helm + git path) | One Application per vcluster; requires ArgoCD ≥ 2.6 |
| Lifecycle | Platform via GitOps; instances imperative (CLI) | Per the user's stated preference for ephemeral sandboxes |

## Prerequisites (one-time platform changes)

1. **Enable SSL passthrough in nginx-ingress.** Edit `base-apps/nginx-ingress/nginx-ingress-controller.yaml` to add `enable-ssl-passthrough: "true"` under `controller.extraArgs` in the Helm `valuesContent`. Cluster-wide change; nominal CPU cost.

2. **Confirm LAN DNS for `*.vcluster.arigsela.com`.** Must resolve to a host-cluster node IP, bypassing Cloudflare Tunnel (which would terminate TLS and break passthrough). Pi-hole/router/`/etc/hosts` — outside the repo. Implementation plan will include a verification step.

3. **Confirm ArgoCD ≥ 2.6** for multi-source Application support. If not, fall back to two Applications per vcluster (chart Application + extras Application).

4. **vcluster CLI installed on the workstation** (`brew install vcluster` or equivalent). Not a cluster change.

No new operator, no new cluster-wide CRDs.

## File layout

```
base-apps/
├── nginx-ingress/
│   └── nginx-ingress-controller.yaml          (modified: add enable-ssl-passthrough)
├── vcluster-sandbox-1.yaml                    (NEW: multi-source ArgoCD Application)
└── vcluster-sandbox-1/                        (NEW: directory)
    ├── certificate.yaml                        (cert-manager Certificate for sandbox-1)
    └── ingress.yaml                            (Ingress with ssl-passthrough annotation)

docs/
├── plans/
│   └── vcluster-implementation-plan.md        (NEW: produced by writing-plans skill)
└── reference/
    └── vcluster/                               (NEW)
        ├── README.md                           (create/destroy/connect runbook)
        ├── values-template.yaml                (default Helm values for ad-hoc vclusters)
        ├── certificate.tmpl.yaml               (envsubst template for ad-hoc Certificate)
        └── ingress.tmpl.yaml                   (envsubst template for ad-hoc Ingress)
```

Ad-hoc vclusters created by CLI do **not** appear in `base-apps/`. They live only in the cluster and are torn down with `vcluster delete`.

## Sample vcluster: `sandbox-1`

This vcluster is always-on, GitOps-managed, and exists to prove the pattern. It is NOT representative of typical ephemeral usage; it exists for validation.

### `base-apps/vcluster-sandbox-1.yaml`

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: vcluster-sandbox-1
  namespace: argo-cd
spec:
  project: default
  destination:
    server: https://kubernetes.default.svc
    namespace: vcluster-sandbox-1
  sources:
    - repoURL: https://charts.loft.sh
      chart: vcluster
      targetRevision: 0.20.x         # pin to specific minor at plan time
      helm:
        releaseName: vcluster
        valuesObject:
          controlPlane:
            backingStore:
              etcd:
                embedded:
                  enabled: false      # use sqlite
            statefulSet:
              persistence:
                volumeClaim:
                  enabled: false      # ephemeral
              resources:
                limits:   { cpu: 1, memory: 512Mi }
                requests: { cpu: 100m, memory: 256Mi }
            service:
              spec:
                type: ClusterIP
            proxy:
              extraSANs:
                - sandbox-1.vcluster.arigsela.com
          sync:
            toHost:
              ingresses: { enabled: false }
            fromHost:
              storageClasses: { enabled: true }
    - repoURL: https://github.com/arigsela/kubernetes
      targetRevision: main
      path: base-apps/vcluster-sandbox-1
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

### `base-apps/vcluster-sandbox-1/certificate.yaml`

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: vcluster-tls
  namespace: vcluster-sandbox-1
spec:
  secretName: vcluster-tls
  issuerRef:
    name: letsencrypt-route53
    kind: ClusterIssuer
  commonName: sandbox-1.vcluster.arigsela.com
  dnsNames:
    - sandbox-1.vcluster.arigsela.com
```

### `base-apps/vcluster-sandbox-1/ingress.yaml`

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: vcluster
  namespace: vcluster-sandbox-1
  annotations:
    nginx.ingress.kubernetes.io/ssl-passthrough: "true"
    nginx.ingress.kubernetes.io/backend-protocol: "HTTPS"
spec:
  ingressClassName: nginx
  rules:
    - host: sandbox-1.vcluster.arigsela.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: vcluster
                port: { number: 443 }
```

### TLS wiring (the trickiest part)

Because nginx uses ssl-passthrough, **the vcluster pod itself must serve a cert matching `sandbox-1.vcluster.arigsela.com`**. The vcluster Helm chart supports loading a custom TLS cert via the control-plane proxy settings (the exact values key needs verification during implementation — likely `controlPlane.proxy.extraSANs` plus a custom certificate volume mount).

**Risk mitigation:** the implementation plan must validate this end-to-end (cert-manager issues secret → vcluster pod mounts it → kubectl trusts the chain) before declaring success. If the chart's native TLS injection turns out to be awkward, the fallback is to skip ssl-passthrough and let nginx terminate TLS, accepting that kubectl needs `--insecure-skip-tls-verify` (acceptable for homelab; not for anything production-like).

## Lifecycle & operations

### Ad-hoc vcluster (CLI, not in git) — the common case

```bash
# Port-forward style (fastest):
vcluster create my-test --namespace vcluster-my-test \
  --values docs/reference/vcluster/values-template.yaml
vcluster connect my-test --namespace vcluster-my-test
# kubeconfig written; localhost port-forward active for this session

# Tear down:
vcluster delete my-test --namespace vcluster-my-test
```

### Ad-hoc vcluster with stable ingress URL

```bash
# 1) Create vcluster
vcluster create my-test --namespace vcluster-my-test \
  --values docs/reference/vcluster/values-template.yaml

# 2) Apply Certificate + Ingress templates with hostname substituted
NAME=my-test envsubst < docs/reference/vcluster/certificate.tmpl.yaml | kubectl apply -f -
NAME=my-test envsubst < docs/reference/vcluster/ingress.tmpl.yaml | kubectl apply -f -

# 3) Connect via stable URL
vcluster connect my-test --namespace vcluster-my-test \
  --server https://my-test.vcluster.arigsela.com
```

### The GitOps `sandbox-1` instance

Always-on. Used to validate that platform changes (cert renewal, ingress controller upgrades, chart bumps) don't break the pattern. `selfHeal: true` means `vcluster pause sandbox-1` won't actually pause it (ArgoCD scales it back up) — that's fine; sleep/resume is a CLI-only feature in this design.

### Versioning

- vcluster Helm chart `targetRevision` pinned to a specific minor (e.g. `0.20.x`)
- Chart upgrades: bump version on `sandbox-1` first → verify API reachable, kubectl works → bump `docs/reference/vcluster/values-template.yaml` defaults if values keys changed

### Observability

vcluster control-plane logs are standard k3s API server output, picked up by the existing `logging` stack via namespace selectors. No new wiring.

## How this interacts with existing platform components

| Component | Interaction | Action needed |
|---|---|---|
| ArgoCD | Hosts the `vcluster-sandbox-1` Application; auto-discovered by master-app | None beyond confirming multi-source support |
| nginx-ingress | Routes API traffic via ssl-passthrough | One-time flag addition |
| cert-manager | Issues per-vcluster certs via `letsencrypt-route53` | None — ClusterIssuer already exists |
| Kyverno | Policies run against synced pods in vcluster namespaces (ECR injection works) | None |
| Vault / ESO | Not used inside vclusters in v1 | None (deferred) |
| Istio ambient | Vcluster namespaces not enrolled | None — relies on absence of `istio.io/dataplane-mode` label |
| Master-app | Auto-discovers `vcluster-sandbox-1.yaml` | None — `FORBIDDEN FROM TOUCHING THE MASTER APP` rule respected |
| Logging stack | Picks up vcluster control-plane logs | None |

## Open questions / risks for the implementation plan to resolve

1. **vcluster Helm values key for TLS cert mounting.** The exact path under `controlPlane.proxy` (or equivalent) for injecting a user-provided cert needs verification against the pinned chart version. Highest-risk item.
2. **ArgoCD version confirmation.** Verify ≥ 2.6 for multi-source; otherwise fall back to two-Application pattern.
3. **LAN DNS resolution.** Document the user's actual DNS setup and add a verification command to the plan.
4. **nginx-ingress restart impact.** Adding `enable-ssl-passthrough` requires controller restart. Plan should sequence this carefully (off-hours, or accept ~30s ingress blip).
5. **ECR pull-secret behavior on vcluster pod.** The vcluster control-plane image comes from `ghcr.io/loft-sh`, not ECR — Kyverno mutation should no-op. Confirm during implementation.

## Success criteria

A successful implementation means all of the following are true:

- `kubectl get applications -n argo-cd vcluster-sandbox-1` shows `Synced` / `Healthy`
- `kubectl get pods -n vcluster-sandbox-1` shows the vcluster StatefulSet pod `Running`
- `vcluster connect sandbox-1 --namespace vcluster-sandbox-1 --server https://sandbox-1.vcluster.arigsela.com` succeeds **without** `--insecure-skip-tls-verify`
- `kubectl get nodes` inside the vcluster shows the (synthetic) node
- `kubectl create deployment nginx --image=nginx` inside the vcluster succeeds, and the synced pod appears in the host's `vcluster-sandbox-1` namespace
- Repeating the flow ad-hoc (`vcluster create my-test ...`) produces a working vcluster reachable on its own subdomain
- `vcluster delete my-test` cleanly removes the namespace and resources

## Implementation phasing (high-level, for the writing-plans skill)

1. **Platform prerequisites:** ssl-passthrough flag, DNS verification, ArgoCD version check, vcluster CLI install
2. **First sample:** GitOps `sandbox-1` deployment (chart Application + extras path), TLS cert wiring resolution
3. **End-to-end validation:** kubectl access, workload sync verification, kyverno policy interaction check
4. **Docs:** values template + README runbook in `docs/reference/vcluster/`
5. **Ad-hoc workflow validation:** create a second vcluster purely via CLI, confirm it works, tear it down
