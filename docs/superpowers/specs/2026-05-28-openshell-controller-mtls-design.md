# OpenShell Controller TLS — Design

**Date:** 2026-05-28
**Branch:** `fix/openshell-controller-mtls`
**Status:** Spec — approved during brainstorming, design pieces validated section-by-section
**Tracking:** GitHub issue #306, follow-up to PR #305

## Problem

After PR #305 merged, `openshell-0` came up healthy but the new `kagent-controller-568c7b55fb-*` pod continued to `CrashLoopBackOff` with the same surface error:

```
unable to build openshell sandbox backends:
openshell: dial https://openshell.openshell.svc.cluster.local:8080: context deadline exceeded
```

Post-merge investigation confirmed a deeper, separate bug:

1. The chart-rendered `openshell-config` ConfigMap sets `[openshell.gateway.tls].client_ca_path = "/etc/openshell-tls/client-ca/ca.crt"`. Whenever `client_ca_path` is set, the gateway switches into **mTLS-required** mode — every connecting client must present a client certificate signed by the chart's CA.

2. The kagent v0.9.4 controller's openshell client (`go/core/pkg/sandboxbackend/openshell/client.go`) has **no support for client certificates**. The `Config` struct fields are `GatewayURL`, `Insecure`, `TLSCAPEM`, `Token`, `TokenFile`, `DialTimeout`, `CallTimeout` — no `CertPEM` / `KeyPEM` / cert-path field anywhere. The Dial function selects one of three credential modes (insecure, TLS-with-CA, TLS-with-system-CAs), none of which present a client cert.

3. `OPENSHELL_INSECURE=true` (currently set) only skips **server-cert verification** on the controller's outbound TLS. It does nothing to make the gateway stop demanding a client cert. The TLS handshake stalls; `DialTimeout=10s` fires; "context deadline exceeded".

Net effect: every connection from the new controller to the gateway is structurally impossible until either the gateway stops requiring client certs OR upstream kagent adds client-cert support.

**Functional impact today:** none. The old (pre-0.9.4) kagent controller (`kagent-controller-857c56b6-*`) remains 1/1 Running. The new ReplicaSet is stuck failing readiness, so the rollout sits idle rather than failing forward.

## Approach

Two changes, applied in the same PR:

1. **Disable the gateway's client-cert requirement** by setting `certManager.clientCaFromServerTlsSecret: false` in `base-apps/openshell.yaml`. This is a single helm value flip. The openshell chart's `templates/gateway-config.yaml` gates the `client_ca_path` line on:

   ```
   {{- if or .Values.server.tls.clientCaSecretName
          .Values.pkiInitJob.enabled
          (and .Values.certManager.enabled .Values.certManager.clientCaFromServerTlsSecret) }}
   client_ca_path = "/etc/openshell-tls/client-ca/ca.crt"
   {{- end }}
   ```

   With all three branches false, `client_ca_path` is omitted; gateway becomes server-TLS-only.

2. **Tighten the controller's outbound TLS** by mounting the chart-issued CA cert and flipping `OPENSHELL_INSECURE` to `false`. The controller will still encrypt the connection (TLS) and now also verify the gateway's server cert. Server-TLS-only, but with proper identity verification on the controller side.

Why this combo: we lose the gateway's verification of client identity (was: cert-manager-signed cert; now: nothing) but keep encryption and add proper server identity verification by the controller. In a single-cluster homelab/lab with NetworkPolicies + RBAC, this is a reasonable posture. When upstream kagent adds client-cert support, we can flip both back.

## Architecture

```
Gateway side (openshell ns)                       Controller side (kagent ns)
──────────────────────────────────                ──────────────────────────────
openshell.yaml chart values:                      kagent.yaml chart values:
  certManager.clientCaFromServerTlsSecret:        controller.env:
    false  ◄── flip                                 OPENSHELL_GATEWAY_URL: …
                                                    OPENSHELL_INSECURE: "false"  ◄── flip
                                                    OPENSHELL_TLS_CA_FILE:
                                                      /etc/openshell-tls/ca/ca.crt
                                                  controller.volumes:
                                                    openshell-ca (Secret-backed)
                                                  controller.volumeMounts:
                                                    /etc/openshell-tls/ca

Cross-namespace CA delivery
──────────────────────────────────────────────────────────────────────────────
openshell ns: Secret/openshell-ca-tls (cert-manager managed; ~yearly rotation)
                  │ tls.crt field
                  ▼
   ClusterSecretStore "kubernetes-cluster-reader"  ◄── new, cluster-scoped
   provider: kubernetes
   auth: SA eso-kubernetes-reader in external-secrets ns
   remoteNamespace: openshell
                  │
                  ▼
kagent ns:    ExternalSecret/kagent-openshell-ca
                  │  pulls openshell-ca-tls.tls.crt
                  ▼
              Secret/kagent-openshell-ca (data: ca.crt)
                  │
                  ▼  mounted readOnly
              kagent-controller container /etc/openshell-tls/ca/ca.crt
                  │
                  ▼  OPENSHELL_TLS_CA_FILE=/etc/openshell-tls/ca/ca.crt
              controller's openshell-client TLS dial verifies gateway server cert
```

## Files changed / added

### Modified

| Path | Change |
|---|---|
| `base-apps/openshell.yaml` | Add `certManager.clientCaFromServerTlsSecret: false` under `spec.source.helm.valuesObject`. |
| `base-apps/kagent.yaml` | Three changes to the `controller` block: (a) `env` — set `OPENSHELL_INSECURE: "false"` and add `OPENSHELL_TLS_CA_FILE: /etc/openshell-tls/ca/ca.crt` (env-var name derived from the `--openshell-tls-ca-file` flag via `app.LoadFromEnv`'s kebab→upper-snake convention); (b) extend `volumes` with `openshell-ca` (Secret-backed, `kagent-openshell-ca`); (c) extend `volumeMounts` with `/etc/openshell-tls/ca` readOnly. Also replace the misleading comment at lines 44–47 with an accurate description of the new posture. |

### New

| Path | Purpose |
|---|---|
| `base-apps/eso-kubernetes-clusterstore.yaml` | ArgoCD `Application` at sync-wave `-3` sourcing the directory below. Auto-discovered by the master-app. Sync-wave `-3` ensures the ClusterSecretStore exists before any ExternalSecret references it (the kagent ExternalSecret materializes at the default wave `0`). |
| `base-apps/eso-kubernetes-clusterstore/` | Directory containing the actual ESO RBAC + ClusterSecretStore manifests. |
| `base-apps/eso-kubernetes-clusterstore/serviceaccount.yaml` | `ServiceAccount/eso-kubernetes-reader` in the `external-secrets` namespace. The identity ESO impersonates when reading Secrets via this store. |
| `base-apps/eso-kubernetes-clusterstore/clusterrole.yaml` | `ClusterRole/eso-kubernetes-reader` — narrow grant: `get/list/watch` on `secrets` with `resourceNames: ["openshell-ca-tls"]` only. |
| `base-apps/eso-kubernetes-clusterstore/rolebinding.yaml` | `RoleBinding` (not ClusterRoleBinding) in the `openshell` namespace, binding the ClusterRole to the SA. Namespace-bounded grant. |
| `base-apps/eso-kubernetes-clusterstore/clustersecretstore.yaml` | `ClusterSecretStore/kubernetes-cluster-reader` using the built-in `kubernetes` provider, authenticating as the SA above, with `remoteNamespace: openshell`. |
| `base-apps/kagent/external-secret-openshell-ca.yaml` | `ExternalSecret/kagent-openshell-ca` in the `kagent` namespace using the new `ClusterSecretStore`. Pulls `openshell-ca-tls.tls.crt` from the openshell ns into `Secret/kagent-openshell-ca` with key `ca.crt`. `refreshInterval: 1h`. |

### Documentation updates (stale from PR #305)

| Path | Change |
|---|---|
| `docs/superpowers/specs/2026-05-27-openshell-jwt-keys-fix-design.md` | Failure-mode triage row for "New kagent controller still crashlooping after openshell-0 is Ready" — replace "Likely a TLS or `OPENSHELL_INSECURE` issue" with a pointer to issue #306 and this spec for the precise diagnosis (gateway-required mTLS + controller lacks client cert support in v0.9.4). |
| `docs/superpowers/plans/2026-05-27-openshell-jwt-keys-fix.md` | Footnote on Task 7 Step 5 acknowledging the actual post-merge outcome (controller continued to fail; second bug; resolved in this follow-up PR). |
| `base-apps/kagent.yaml` (lines 44–47) | Replace the existing misleading comment ("OPENSHELL_INSECURE skips TLS verification ... tighten by mounting that CA and flipping this to 'false' in a follow-up") with an accurate description: the controller now verifies the gateway's server cert via the mounted CA; full mTLS (client + server) is blocked on upstream kagent adding client-cert support. |

### Unchanged

- `scripts/bootstrap-openshell-jwt.sh` — unrelated; stays.
- `base-apps/openshell/{secret-store,external-secret}.yaml` — JWT-keys flow stays as-is.
- `base-apps/openshell-secrets.yaml` — unchanged.

## ClusterSecretStore RBAC (security-sensitive)

The ClusterSecretStore needs RBAC to read Secrets across namespaces. The scoping is **deliberately narrow** to limit blast radius.

**ServiceAccount:** `eso-kubernetes-reader` in `external-secrets` ns (where ESO controllers run).

**ClusterRole:**

```yaml
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    resourceNames: ["openshell-ca-tls"]
    verbs: ["get", "list", "watch"]
```

- `resourceNames` set → limited to one Secret name only.
- `secrets` only.
- Read-only verbs.

**RoleBinding (NOT ClusterRoleBinding):**

Binding lives in the `openshell` namespace, references the cluster-scoped ClusterRole, subject is the SA in `external-secrets`. Because it's a `RoleBinding` (namespace-scoped) rather than a `ClusterRoleBinding`, the effective grant is **bounded to the `openshell` namespace**: the SA can read Secret `openshell-ca-tls` in `openshell` and nothing else, anywhere.

**Net effective permission:** `get/list/watch` on `Secret/openshell-ca-tls` in namespace `openshell`. No other Secrets. No other namespaces. No write/delete.

**ClusterSecretStore defense-in-depth:** `spec.provider.kubernetes.remoteNamespace: openshell` constrains ESO to only consult that namespace when resolving references — even if RBAC were over-permissive, ESO scopes its lookups.

## ArgoCD sync-wave ordering

| Wave | Application | Purpose |
|---|---|---|
| `-3` | `eso-kubernetes-clusterstore` (new) | ClusterSecretStore + RBAC must exist before any ExternalSecret references it. |
| `-2` | `agent-sandbox-crds`, `istio-base`, `openshell-secrets` (existing) | Unchanged. |
| `-1` | `openshell` (existing) | Now reconciles with `clientCaFromServerTlsSecret: false`. Gateway config will lose `client_ca_path` and the `tls-client-ca` volume mount — pod will roll. |
| `0` | `kagent` (existing) | ExternalSecret `kagent-openshell-ca` (in this app's directory) materializes the Secret; controller env + volumes pick up the CA mount; controller dial succeeds; new ReplicaSet becomes Ready; old ReplicaSet is scaled to 0. |

Race window: the `kagent-openshell-ca` ExternalSecret depends on the upstream `openshell-ca-tls` Secret existing and the ClusterSecretStore being healthy. ESO's reconciliation will retry until both are present. Worst case is a slow first roll, not a deadlock.

## Verification

After merge + sync, expect:

1. ClusterSecretStore healthy:
   ```bash
   kubectl get clustersecretstore kubernetes-cluster-reader -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
   # expect: True
   ```

2. ExternalSecret synced:
   ```bash
   kubectl get externalsecret -n kagent kagent-openshell-ca -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
   # expect: True
   kubectl get secret -n kagent kagent-openshell-ca -o jsonpath='{.data}' | jq 'keys'
   # expect: ["ca.crt"]
   ```

3. Gateway pod rolled (`client_ca_path` removed from config triggers ConfigMap checksum change → StatefulSet pod restarts):
   ```bash
   kubectl get pod -n openshell openshell-0
   # expect: 1/1 Running, RESTARTS >= 1 (one restart from this PR's roll)
   kubectl exec -n openshell openshell-0 -- cat /etc/openshell/gateway.toml | grep client_ca_path || echo "absent (expected)"
   ```

4. New kagent controller exits CrashLoop:
   ```bash
   kubectl get pods -n kagent -l app.kubernetes.io/component=controller
   # expect: exactly one Running pod on the new ReplicaSet hash
   kubectl logs -n kagent -l app.kubernetes.io/component=controller --tail=30 \
     | grep -iE 'openshell|sandbox'
   # expect: NO "unable to build openshell sandbox backends"
   # expect: successful init message
   ```

5. End-to-end smoke (sample AgentHarness from PR #305 / commit 5fa23e7):
   ```bash
   kubectl get agentharness -n kagent homelab-harness
   ```

## Rollback

Single commit revert. ArgoCD prunes the new Application + ExternalSecret + RBAC. The `kagent.yaml` env-var changes revert; controller goes back to `INSECURE=true` and stops trying to verify the gateway. `openshell.yaml` reverts and the gateway goes back to mTLS-required mode. Net effect: cluster returns to the pre-PR state (controller CrashLoop, old controller still serving). No data loss.

## Post-merge addendum (after PR #307)

PR #307 implemented the design above and merged. Cluster verification revealed a **second chart limitation** that the design did not catch: openshell chart v0.0.49's `templates/gateway-config.yaml` renders `client_ca_path` **unconditionally** inside the `[openshell.gateway.tls]` block (lines 60-64) whenever TLS is enabled. There is no `{{- if }}` gate. The `clientCaFromServerTlsSecret` flag only affects the StatefulSet's volume mount, not the rendered ConfigMap. So even with the flag flipped, the gateway still demands client certs.

The chart has no clean "encrypted, server-cert-only" mode for v0.0.49. The only escape hatch is `server.disableTls: true`, which drops the entire TLS block.

Resolution shipped in the follow-up PR:

- `base-apps/openshell.yaml` — replace `certManager.clientCaFromServerTlsSecret: false` with `server.disableTls: true`. Gateway runs plain h2c on :8080.
- `base-apps/kagent.yaml` — revert `OPENSHELL_INSECURE` to `"true"`, drop `OPENSHELL_TLS_CA_FILE` env var, drop the `openshell-ca` volume + volumeMount, drop the `https://` scheme from `OPENSHELL_GATEWAY_URL`.
- Delete the ESO infrastructure introduced by PR #307 (no longer used): `base-apps/eso-kubernetes-clusterstore.yaml`, `base-apps/eso-kubernetes-clusterstore/`, `base-apps/kagent/external-secret-openshell-ca.yaml`.

**Trade-off:** lost encryption between controller and gateway. Cluster pod-to-pod traffic stays on the CNI overlay with NetworkPolicy enforcement, so this is an acceptable interim posture but a real regression from the encrypted target.

**Future work:** re-enable encryption when either upstream lands a fix — openshell chart should gate `client_ca_path` rendering, OR kagent should add client-cert config fields to its openshell client. Both tracked in issue #306.

## Out of scope

- **Upstream kagent client-cert support.** Not opening an upstream issue or PR. If/when kagent adds `CertPEM`/`KeyPEM` config fields, a future follow-up can swap our server-TLS-only posture back to full mTLS.
- **Bearer-token / OIDC integration.** Would require running an OIDC issuer in-cluster. Out of scope.
- **Per-tenant gateway certs / SAN tuning.** Out of scope.
- **Migrating other cross-namespace secret needs to this ClusterSecretStore.** The store is intentionally narrow (one Secret name). When the next cross-ns mirroring need arises, add a new RoleBinding (and possibly broaden the ClusterRole's `resourceNames`) — but don't pre-emptively widen it now.
