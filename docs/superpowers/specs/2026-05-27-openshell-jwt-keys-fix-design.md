# OpenShell JWT Keys Bootstrap — Design

**Date:** 2026-05-27
**Branch:** `feat/phase2-openshell-agentharness`
**Status:** Spec — awaiting user review before implementation plan

## Problem

The kagent v0.9.4 controller deployed in commit `5fa23e7` is in `CrashLoopBackOff` (12 restarts over 40 minutes at the time of diagnosis). Its startup logs show:

```
unable to build openshell sandbox backends:
openshell: dial https://openshell.openshell.svc.cluster.local:8080: context deadline exceeded
```

The gateway it's dialing — pod `openshell-0` in the `openshell` namespace — has been stuck in `ContainerCreating` for the same window. Kubelet events show:

```
MountVolume.SetUp failed for volume "sandbox-jwt":
secret "openshell-jwt-keys" not found
```

### Root cause

OpenShell Helm chart `ghcr.io/nvidia/openshell/helm-chart:0.0.49` couples **JWT signing key generation** to the **PKI bootstrap Job**. From `templates/certgen.yaml`:

```yaml
{{- if .Values.pkiInitJob.enabled }}
# ... single Job whose args include:
#   - --server-secret-name=...
#   - --client-secret-name=...
#   - --jwt-secret-name=openshell-jwt-keys
{{- end }}
```

`base-apps/openshell.yaml` correctly set `pkiInitJob.enabled: false` (because `certManager.enabled: true` is providing TLS — the chart explicitly fails if both are true). That decision unintentionally also removed JWT-keypair generation, because the chart bundles the two responsibilities into one hook. Cert-manager produces `openshell-server-tls`, `openshell-client-tls`, and `openshell-ca-tls` correctly, but nothing produces the JWT keypair Secret the StatefulSet mounts at `/etc/openshell-jwt`.

The Secret must contain three keys:
- `signing.pem` — Ed25519 private key, PKCS#8 PEM
- `public.pem` — Ed25519 public key, SPKI PEM
- `kid` — key identifier, plain text

## Architecture

Materialize `Secret/openshell-jwt-keys` in the `openshell` namespace via **External Secrets Operator** syncing from Vault. This mirrors the existing `kagent` namespace pattern (`base-apps/kagent/secret-store.yaml`, `base-apps/kagent/external-secrets.yaml`).

```
Vault (k8s-secrets/openshell/jwt)
        │
        ▼
ExternalSecret/openshell-jwt-keys (ESO reconciles)
        │
        ▼
Secret/openshell-jwt-keys ───▶ openshell-0 StatefulSet (mount: /etc/openshell-jwt)
                                        │
                                        ▼
                       kagent-controller dials openshell:8080 ✓
```

A new ArgoCD Application **`openshell-secrets`** at sync-wave `-2` provisions the SecretStore + ExternalSecret **before** the existing `openshell` chart Application (sync-wave `-1`) reconciles, so the StatefulSet finds its Secret on first mount attempt.

The keypair is generated **once, locally**, by `scripts/bootstrap-openshell-jwt.sh` and written to Vault. The script is idempotent — re-running with the secret already present is a no-op unless `--rotate` is passed.

## Files added / changed

### New files

| Path | Purpose |
|---|---|
| `base-apps/openshell-secrets.yaml` | ArgoCD `Application` at sync-wave `-2` pointing at `base-apps/openshell/`. Auto-discovered by the master app. |
| `base-apps/openshell/secret-store.yaml` | `SecretStore` in `openshell` ns, Vault Kubernetes auth, role `openshell`. Copy of `base-apps/kagent/secret-store.yaml` with namespace + role swapped. |
| `base-apps/openshell/external-secret.yaml` | `ExternalSecret` whose `target.name: openshell-jwt-keys`. Three `data` entries map dot-free Vault field names to the dotted filenames the chart expects: `secretKey: signing.pem` ← `remoteRef.property: signing_pem`; `secretKey: public.pem` ← `public_pem`; `secretKey: kid` ← `kid`. All entries `remoteRef.key: openshell/jwt`. `refreshInterval: 1h`. |
| `scripts/bootstrap-openshell-jwt.sh` | One-time bootstrap. Generates keypair, writes to Vault, configures Vault role + policy. Committed so it's reviewable and reproducible. |

### Unchanged

- `base-apps/openshell.yaml` — chart values stay as-is. The chart still emits the StatefulSet that mounts `openshell-jwt-keys`; we just supply that Secret from a different source.
- `base-apps/kagent.yaml` — unchanged. The controller will heal itself once the gateway is reachable.
- `base-apps/agent-sandbox-crds.yaml` — unchanged.

### Vault side (provisioned by the script)

- Role `openshell` under `auth/kubernetes/role/openshell`, bound to `system:serviceaccount:openshell:default`, attached to policy `openshell-read`.
- Policy `openshell-read` granting `read` on `k8s-secrets/data/openshell/*`.
- KV-v2 entry at `k8s-secrets/openshell/jwt` with fields `signing_pem`, `public_pem`, `kid`. (Dot-free Vault keys avoid an ESO gjson-syntax pitfall — see "ExternalSecret field naming" note below. The chart-facing Secret keys keep the dotted form via `secretKey` renaming.)

## Bootstrap script — `scripts/bootstrap-openshell-jwt.sh`

**Invocation:** `./scripts/bootstrap-openshell-jwt.sh [--rotate] [--dry-run] [--allow-root]`

### Preconditions

Script exits non-zero with a clear message if any of these fail:
- `vault`, `openssl`, `jq`, `kubectl` on `PATH`
- `VAULT_ADDR` env var is set
- `vault token lookup` succeeds (active token)
- Token is not a root token unless `--allow-root` is passed

### Default flow (no flags)

1. Check Vault for `k8s-secrets/openshell/jwt`. If present, log `already bootstrapped — pass --rotate to regenerate` and exit 0. **Idempotent.**
2. Create a temp dir via `mktemp -d`, register `trap cleanup EXIT` so private key material is removed on any exit path (including signal/error).
3. Generate the keypair entirely within the temp dir:
   - `openssl genpkey -algorithm ED25519 -out $tmp/signing.pem` → PKCS#8 private key
   - `openssl pkey -in $tmp/signing.pem -pubout -out $tmp/public.pem` → SPKI public key
   - `kid` = first 16 chars of `base64url(sha256(public-key-DER))` — deterministic from public key, matches common JWT/JWK `kid` conventions.
4. Configure Vault if not already configured:
   - If policy `openshell-read` doesn't exist: write it with `path "k8s-secrets/data/openshell/*" { capabilities = ["read"] }`.
   - If role `auth/kubernetes/role/openshell` doesn't exist: create it bound to SA `default` in ns `openshell`, policy `openshell-read`, TTL 24h.
5. `vault kv put k8s-secrets/openshell/jwt signing_pem=@$tmp/signing.pem public_pem=@$tmp/public.pem kid=<kid>`. (Underscored field names; the dotted filenames the chart wants are produced by the ExternalSecret's `secretKey` mapping.)
6. Print to stdout: the `kid`, the contents of `public.pem` (safe to share), and a one-liner to force ESO sync immediately rather than waiting on `refreshInterval`:
   ```bash
   kubectl annotate externalsecret/openshell-jwt-keys -n openshell \
     force-sync=$(date +%s) --overwrite
   ```

### `--rotate` flow

Same as default but **skips** the idempotency check in step 1. After Vault write, prints a clearly-formatted warning:

```
ROTATED. Tokens minted under the previous kid will fail validation on TTL
expiry (default 3600s). To pick up the new kid immediately:
  kubectl delete pod -n openshell openshell-0
```

### `--dry-run` flow

Prints every `vault` and `kubectl` command the script would execute, including the policy HCL body, then exits 0 without mutating anything. Useful for reviewing the Vault role/policy before committing to a write.

### Safety

- All private key material lives only inside `$tmp/` and is removed by the `trap`.
- `vault kv` (KV-v2 aware) — not `vault write` directly — to avoid the common `data/data/` path mistake.
- Root token guard (`--allow-root` required) encourages running with a scoped admin token.
- No `set -x`; only `set -euo pipefail`.

## ExternalSecret field naming (implementer note)

ESO's Vault provider evaluates `remoteRef.property` with [gjson](https://github.com/tidwall/gjson) syntax, where `.` is a path separator. A Vault field literally named `signing.pem` would be interpreted as `signing → pem` (nested lookup), not the literal key, and the sync would fail with a key-not-found error.

We dodge this by storing under dot-free Vault keys (`signing_pem`, `public_pem`, `kid`) and using ESO's `secretKey` to rename to the dotted filenames the chart's StatefulSet mounts:

```yaml
# base-apps/openshell/external-secret.yaml (sketch)
data:
  - secretKey: signing.pem          # becomes file in mounted Secret
    remoteRef:
      key: openshell/jwt
      property: signing_pem          # field name in Vault
  - secretKey: public.pem
    remoteRef:
      key: openshell/jwt
      property: public_pem
  - secretKey: kid
    remoteRef:
      key: openshell/jwt
      property: kid
```

The resulting `Secret/openshell-jwt-keys` has data keys `signing.pem`, `public.pem`, `kid` — exactly what the chart's `sandbox-jwt` volume mounts.

## Argo ordering & rollout sequence

### Pre-merge (manual, once)

1. Pull the branch locally.
2. Run `./scripts/bootstrap-openshell-jwt.sh`. Vault now has the keypair + role + policy.
3. Verify:
   ```bash
   vault kv get k8s-secrets/openshell/jwt        # three fields: signing_pem, public_pem, kid
   vault read auth/kubernetes/role/openshell     # binding shown
   ```

### Post-merge (ArgoCD auto-sync)

ArgoCD reconciles by sync-wave, lowest first:

| Wave | Application | Resources | Notes |
|---|---|---|---|
| `-2` | `agent-sandbox-crds` (existing) | sigs.k8s.io agent-sandbox CRDs | Unchanged. |
| `-2` | `openshell-secrets` **(new)** | `SecretStore`, `ExternalSecret` → `Secret/openshell-jwt-keys` | Must land before the chart so the StatefulSet's volume mount sees the Secret. |
| `-1` | `openshell` (existing) | Chart 0.0.49: Issuer, Certificates, StatefulSet, Service, GRPCRoute, NetworkPolicy | Chart unchanged. StatefulSet mount succeeds because the Secret already exists. |
| `0` | `kagent` (existing) | Controller, agents, UI, etc. | Controller dials gateway, succeeds, new ReplicaSet rolls out, old ReplicaSet scales to 0. |

### Race window

ESO reconciles asynchronously — the `ExternalSecret` resource exists at wave `-2`, but the actual `Secret` materializes seconds later when ESO polls Vault. The openshell StatefulSet retries `MountVolume.SetUp` on a kubelet backoff (capped at ~2 minutes). Worst case is a slow first start, not a deadlock. **No explicit sync wait/hook needed.**

### One-time cleanup of current stuck state

```bash
# Force the stuck openshell-0 to re-attempt mount immediately, rather than
# wait out kubelet's mount backoff:
kubectl delete pod -n openshell openshell-0

# The kagent-controller CrashLoopBackOff pod will self-heal on next restart
# once openshell-0 is Ready — no manual delete needed.
```

### Rollback

- Revert the merge commit. ArgoCD prunes the `openshell-secrets` Application → ExternalSecret + Secret deleted. openshell-0 returns to `ContainerCreating` (the failure mode we're already in — no worse off).
- Vault entry stays (Vault is intentionally not pruned by ArgoCD). Safe to leave; or `vault kv delete k8s-secrets/openshell/jwt` manually.

## Verification

Run after the merge + bootstrap. Each step has a clear pass/fail signal.

### 1. ExternalSecret reached `SecretSynced`

```bash
kubectl get externalsecret -n openshell openshell-jwt-keys \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
# expect: True

kubectl get secret -n openshell openshell-jwt-keys -o jsonpath='{.data}' | jq 'keys'
# expect: ["kid","public.pem","signing.pem"]
```

### 2. openshell-0 mounts and goes Ready

```bash
kubectl get pod -n openshell openshell-0
# expect: 1/1 Running, RESTARTS small (<3)

kubectl logs -n openshell openshell-0 --tail=30
# expect: gateway-startup log lines, no "signing key" / "jwt" errors
```

### 3. Gateway reachable on cluster network

```bash
kubectl run -n openshell --rm -it --image=busybox:1.36 net-test --restart=Never -- \
  wget -qO- --no-check-certificate https://openshell.openshell.svc.cluster.local:8080/healthz
# expect: 200 OK / healthy response
```

### 4. New kagent controller exits CrashLoop

```bash
kubectl get pods -n kagent -l app.kubernetes.io/component=controller
# expect: exactly one Running pod on ReplicaSet 568c7b55fb (the new hash)

kubectl logs -n kagent -l app.kubernetes.io/component=controller --tail=20 | grep -i openshell
# expect: NO "unable to build openshell sandbox backends" line
# expect: a benign "openshell sandbox backends initialized" or equivalent
```

### 5. End-to-end smoke

```bash
kubectl get agentharness -n kagent
# expect: homelab-harness (from commit 5fa23e7) shows Ready/Synced
```

Run one trivial sandbox-backed task through the kagent UI (e.g., ask the harness to `echo hello`). Pass = response without `OPENSHELL_GATEWAY_URL`-related errors in controller logs.

### Failure-mode triage

| Symptom | First check |
|---|---|
| ExternalSecret stuck `SecretSyncError` | `kubectl describe externalsecret -n openshell openshell-jwt-keys` — most likely Vault role/policy mismatch. Re-run script with `--dry-run` to inspect role/policy definition. |
| openshell-0 Running but readinessProbe failing | `kubectl logs -n openshell openshell-0` — chart may complain about kid/public.pem mismatch if rotated mid-run. |
| New kagent controller still crashlooping after openshell-0 is Ready | Likely a TLS or `OPENSHELL_INSECURE` issue, **not** JWT. Out of scope for this fix — see below. |

## Out of scope

- **Controller mTLS to the gateway.** `OPENSHELL_INSECURE=true` stays. The TODO in `base-apps/kagent.yaml:44-47` already tracks mounting `openshell-ca-tls` + flipping to `false`. Independent of the JWT-keys bug.
- **Upstream chart fix.** The real chart bug is `templates/certgen.yaml` gating JWT key generation behind `pkiInitJob.enabled`. Worth an upstream issue/PR to `nvidia/openshell`; unblocking ourselves first. Tracked separately.
- **JWT key rotation policy.** Script supports `--rotate`, but no automated rotation cadence (cron, Vault auto-rotate) is wired up. Phase-2 keys are bootstrap-only.
- **Per-tenant / multi-gateway JWT keys.** Single keypair, single gateway, single `kid`.
- **OIDC gateway configuration.** Chart `server.oidc.*` stays at defaults (disabled). Not needed for the controller→gateway path.
- **Existing `agent-sandbox-crds` Application.** Unchanged.

## References

- Failing pod logs (kagent controller): `unable to build openshell sandbox backends ... context deadline exceeded`
- Failing event (openshell-0): `MountVolume.SetUp failed for volume "sandbox-jwt" : secret "openshell-jwt-keys" not found`
- Chart template: `ghcr.io/nvidia/openshell/helm-chart:0.0.49`, `templates/certgen.yaml` (JWT gen behind `pkiInitJob.enabled`), `templates/statefulset.yaml:137-140` (sandbox-jwt volume), `templates/gateway-config.yaml:72-77` (signing/public/kid paths).
- Pattern to mirror: `base-apps/kagent/secret-store.yaml`, `base-apps/kagent/external-secrets.yaml`.
- Phase 2 context commit: `5fa23e7 feat(kagent): Phase 2 — OpenShell gateway + sample AgentHarness`.
