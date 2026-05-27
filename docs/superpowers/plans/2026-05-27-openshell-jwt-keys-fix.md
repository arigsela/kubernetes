# OpenShell JWT Keys Bootstrap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unblock the new kagent v0.9.4 controller (`CrashLoopBackOff`) and the openshell-0 pod (`ContainerCreating`) by provisioning the missing `Secret/openshell-jwt-keys` in the `openshell` namespace via Vault + External Secrets Operator.

**Architecture:** Generate an Ed25519 JWT keypair once via a committed local bootstrap script, store it in Vault at `k8s-secrets/openshell/jwt`, and sync it into the cluster as `Secret/openshell-jwt-keys` via a new ESO `SecretStore` + `ExternalSecret`. Wire up a new ArgoCD `Application` at sync-wave `-2` so the secret lands before the openshell Helm chart's StatefulSet tries to mount it.

**Tech Stack:** ArgoCD, External Secrets Operator (v1beta1), HashiCorp Vault (KV v2 + Kubernetes auth), OpenSSL (Ed25519 keypair generation), Bash, kubectl, openshell Helm chart v0.0.49.

**Spec:** `docs/superpowers/specs/2026-05-27-openshell-jwt-keys-fix-design.md`

**Branch:** `feat/phase2-openshell-agentharness`

---

## Important execution notes

- **User commit policy:** The user's CLAUDE.md says "NEVER commit changes unless the user explicitly asks you to." Treat every `git commit` step as: stage the files, show the proposed commit message, and **ask before committing**. If the user replies "go ahead" or "commit", proceed; otherwise leave staged.
- **No auto-push:** Pushing to remote triggers ArgoCD auto-sync, which is a multi-namespace cluster mutation. Ask explicitly before `git push`.
- **Vault prerequisites for Task 5 (executed by user):** User needs `vault` CLI on PATH with `VAULT_ADDR` exported and a valid non-root token. If the user normally accesses Vault via `kubectl port-forward svc/vault -n vault 8200:8200`, they should run that in a separate terminal first.
- **kubectl --dry-run=server:** Used throughout for manifest validation because the cluster has the relevant CRDs installed (ESO, ArgoCD). This catches schema errors that `--dry-run=client` misses.

---

## File structure

```
base-apps/
├── openshell-secrets.yaml          # NEW: ArgoCD Application (sync-wave -2)
├── openshell.yaml                  # UNCHANGED: existing chart Application
└── openshell/                      # NEW directory
    ├── secret-store.yaml           # NEW: ESO SecretStore (Vault backend)
    └── external-secret.yaml        # NEW: ExternalSecret → openshell-jwt-keys

scripts/
└── bootstrap-openshell-jwt.sh      # NEW: idempotent local bootstrap

docs/superpowers/
├── specs/2026-05-27-openshell-jwt-keys-fix-design.md  # STAGED (not committed yet)
└── plans/2026-05-27-openshell-jwt-keys-fix.md         # this file
```

**File responsibilities:**

- `scripts/bootstrap-openshell-jwt.sh` — one job: get the keypair into Vault. Handles keypair generation, Vault role/policy/KV bootstrap, idempotency, rotation.
- `base-apps/openshell/secret-store.yaml` — Vault-auth wiring for the `openshell` namespace. Single resource.
- `base-apps/openshell/external-secret.yaml` — declares which Vault fields → which Secret data keys. Single resource.
- `base-apps/openshell-secrets.yaml` — ArgoCD Application binding the directory above to the master-app pattern at sync-wave `-2`.

Each file has one concern. Manifests live next to each other in `base-apps/openshell/` so they're discoverable.

---

## Task 1: Bootstrap script — `scripts/bootstrap-openshell-jwt.sh`

**Files:**
- Create: `scripts/bootstrap-openshell-jwt.sh`

This task produces the full script. We build it up incrementally, validating with `bash -n` (syntax check) and `--dry-run` (semantic check) along the way.

- [ ] **Step 1: Create skeleton with shebang, strict mode, usage text**

Write to `scripts/bootstrap-openshell-jwt.sh`:

```bash
#!/usr/bin/env bash
#
# bootstrap-openshell-jwt.sh — one-time provisioning of the openshell-jwt-keys
# secret material into Vault. Idempotent by default; pass --rotate to regenerate.
#
# Spec: docs/superpowers/specs/2026-05-27-openshell-jwt-keys-fix-design.md

set -euo pipefail

VAULT_PATH="k8s-secrets/openshell/jwt"
VAULT_POLICY="openshell-read"
VAULT_ROLE="openshell"
K8S_NAMESPACE="openshell"
K8S_SA="default"

ROTATE=false
DRY_RUN=false
ALLOW_ROOT=false

usage() {
  cat <<EOF
Usage: $0 [--rotate] [--dry-run] [--allow-root]

  --rotate       Regenerate keypair even if Vault already has one.
                 Existing minted tokens fail validation on TTL expiry.
  --dry-run      Print every vault/kubectl command without executing.
  --allow-root   Permit running with a Vault root token (off by default).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rotate)      ROTATE=true; shift ;;
    --dry-run)     DRY_RUN=true; shift ;;
    --allow-root)  ALLOW_ROOT=true; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) echo "unknown flag: $1" >&2; usage; exit 2 ;;
  esac
done

main() {
  echo "TODO: implement"
}

main "$@"
```

- [ ] **Step 2: Verify script parses**

Run: `bash -n scripts/bootstrap-openshell-jwt.sh`
Expected: no output, exit 0.

Run: `chmod +x scripts/bootstrap-openshell-jwt.sh && ./scripts/bootstrap-openshell-jwt.sh --help`
Expected: prints the usage block above and exits 0.

- [ ] **Step 3: Add preconditions check**

Replace the `main()` stub and add a `preconditions()` function above it. Insert these functions between the flag parsing block and `main "$@"`:

```bash
log() { echo "[bootstrap-openshell-jwt] $*"; }
die() { echo "[bootstrap-openshell-jwt] ERROR: $*" >&2; exit 1; }

run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "DRY-RUN: $*"
  else
    eval "$@"
  fi
}

preconditions() {
  for bin in vault openssl jq kubectl; do
    command -v "$bin" >/dev/null 2>&1 || die "$bin not found on PATH"
  done
  [[ -n "${VAULT_ADDR:-}" ]] || die "VAULT_ADDR not set"
  vault token lookup >/dev/null 2>&1 || die "vault token lookup failed (no active token?)"
  if [[ "$ALLOW_ROOT" != "true" ]]; then
    if vault token lookup -format=json | jq -e '.data.policies | index("root")' >/dev/null 2>&1; then
      die "refusing to run with a root token; pass --allow-root to override"
    fi
  fi
  log "preconditions OK"
}

main() {
  preconditions
}
```

- [ ] **Step 4: Verify preconditions logic**

Run: `bash -n scripts/bootstrap-openshell-jwt.sh`
Expected: no output, exit 0.

Note: actually running `main` requires `vault` and `VAULT_ADDR` — we'll do that in Task 5. For now syntax-check is enough.

- [ ] **Step 5: Add Vault role/policy bootstrap**

Append a `bootstrap_vault_auth()` function above `main()`:

```bash
bootstrap_vault_auth() {
  if ! vault policy read "$VAULT_POLICY" >/dev/null 2>&1; then
    log "creating Vault policy $VAULT_POLICY"
    local policy_hcl
    policy_hcl=$(cat <<EOF
path "k8s-secrets/data/openshell/*" {
  capabilities = ["read"]
}
EOF
)
    if [[ "$DRY_RUN" == "true" ]]; then
      echo "DRY-RUN: vault policy write $VAULT_POLICY <<EOF"
      echo "$policy_hcl"
      echo "EOF"
    else
      echo "$policy_hcl" | vault policy write "$VAULT_POLICY" -
    fi
  else
    log "Vault policy $VAULT_POLICY already exists"
  fi

  if ! vault read "auth/kubernetes/role/$VAULT_ROLE" >/dev/null 2>&1; then
    log "creating Vault Kubernetes auth role $VAULT_ROLE"
    run "vault write auth/kubernetes/role/$VAULT_ROLE \
      bound_service_account_names=$K8S_SA \
      bound_service_account_namespaces=$K8S_NAMESPACE \
      policies=$VAULT_POLICY \
      ttl=24h"
  else
    log "Vault role $VAULT_ROLE already exists"
  fi
}
```

Update `main()`:

```bash
main() {
  preconditions
  bootstrap_vault_auth
}
```

- [ ] **Step 6: Add keypair generation + idempotency + KV write**

Append three functions above `main()`:

```bash
secret_exists() {
  vault kv get "$VAULT_PATH" >/dev/null 2>&1
}

generate_keypair() {
  local outdir="$1"
  openssl genpkey -algorithm ED25519 -out "$outdir/signing.pem" 2>/dev/null
  openssl pkey -in "$outdir/signing.pem" -pubout -out "$outdir/public.pem" 2>/dev/null
  # kid = first 16 chars of base64url(sha256(SPKI DER))
  openssl pkey -pubin -in "$outdir/public.pem" -outform der \
    | openssl dgst -sha256 -binary \
    | base64 \
    | tr '+/' '-_' \
    | tr -d '=' \
    | cut -c1-16 > "$outdir/kid"
}

write_vault_secret() {
  local outdir="$1"
  local kid
  kid=$(cat "$outdir/kid")
  run "vault kv put $VAULT_PATH \
    signing_pem=@$outdir/signing.pem \
    public_pem=@$outdir/public.pem \
    kid=$kid"
  log "wrote Vault secret at $VAULT_PATH (kid=$kid)"
}
```

Update `main()`:

```bash
main() {
  preconditions
  bootstrap_vault_auth

  if secret_exists && [[ "$ROTATE" != "true" ]]; then
    log "already bootstrapped at $VAULT_PATH — pass --rotate to regenerate"
    exit 0
  fi

  local tmp
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT

  generate_keypair "$tmp"
  write_vault_secret "$tmp"

  if [[ "$ROTATE" == "true" ]]; then
    log "ROTATED. Tokens minted under the previous kid will fail validation on TTL expiry."
    log "To pick up the new kid immediately: kubectl delete pod -n openshell openshell-0"
  fi

  cat <<EOF

Bootstrap complete.

  kid: $(cat "$tmp/kid")

To force ESO sync immediately rather than waiting on refreshInterval:
  kubectl annotate externalsecret/openshell-jwt-keys -n openshell \\
    force-sync=\$(date +%s) --overwrite

Public key (safe to share):
$(cat "$tmp/public.pem")
EOF
}
```

- [ ] **Step 7: Final syntax check**

Run: `bash -n scripts/bootstrap-openshell-jwt.sh`
Expected: no output, exit 0.

Run: `./scripts/bootstrap-openshell-jwt.sh --help`
Expected: usage block prints, exit 0.

- [ ] **Step 8: Verify executable bit + line count**

Run: `ls -la scripts/bootstrap-openshell-jwt.sh && wc -l scripts/bootstrap-openshell-jwt.sh`
Expected: `-rwxr-xr-x`, between 130 and 180 lines.

- [ ] **Step 9: Stage and ask before committing**

Run: `git add scripts/bootstrap-openshell-jwt.sh && git status --short scripts/`

Proposed commit message (ASK USER BEFORE COMMITTING):

```
feat(openshell): add JWT keypair bootstrap script

Idempotent script that generates an Ed25519 keypair, configures the
Vault `openshell` role + `openshell-read` policy, and writes the
keypair material to `k8s-secrets/openshell/jwt`. Supports --rotate,
--dry-run, and --allow-root flags. Spec:
docs/superpowers/specs/2026-05-27-openshell-jwt-keys-fix-design.md.
```

If approved, commit with that message.

---

## Task 2: SecretStore manifest

**Files:**
- Create: `base-apps/openshell/secret-store.yaml`

- [ ] **Step 1: Create the file**

Write to `base-apps/openshell/secret-store.yaml`:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: openshell
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "openshell"
          serviceAccountRef:
            name: "default"
```

This mirrors `base-apps/kagent/secret-store.yaml` exactly, swapping `namespace` and `role` to `openshell`.

- [ ] **Step 2: Validate against the live cluster (server-side dry-run)**

Run: `kubectl apply --dry-run=server -f base-apps/openshell/secret-store.yaml`
Expected: `secretstore.external-secrets.io/vault-backend created (server dry run)` — no schema errors.

If you get `error validating "...": ... no matches for kind "SecretStore"`, ESO CRDs aren't installed. That would be a separate problem; stop and surface it.

- [ ] **Step 3: Stage**

Run: `git add base-apps/openshell/secret-store.yaml`

Do NOT commit yet — bundling with the rest of the manifests in Task 4.

---

## Task 3: ExternalSecret manifest

**Files:**
- Create: `base-apps/openshell/external-secret.yaml`

- [ ] **Step 1: Create the file**

Write to `base-apps/openshell/external-secret.yaml`:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: openshell-jwt-keys
  namespace: openshell
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: openshell-jwt-keys
    creationPolicy: Owner
  data:
    # The chart's StatefulSet mounts this Secret as files at /etc/openshell-jwt/
    # with filenames signing.pem, public.pem, kid. We store the Vault fields
    # under dot-free names (signing_pem, public_pem) because ESO's Vault
    # provider parses remoteRef.property with gjson syntax, where `.` is a path
    # separator. The secretKey renaming produces the dotted filenames the
    # chart expects.
    - secretKey: signing.pem
      remoteRef:
        key: openshell/jwt
        property: signing_pem
    - secretKey: public.pem
      remoteRef:
        key: openshell/jwt
        property: public_pem
    - secretKey: kid
      remoteRef:
        key: openshell/jwt
        property: kid
```

- [ ] **Step 2: Validate against the live cluster**

Run: `kubectl apply --dry-run=server -f base-apps/openshell/external-secret.yaml`
Expected: `externalsecret.external-secrets.io/openshell-jwt-keys created (server dry run)`.

- [ ] **Step 3: Stage**

Run: `git add base-apps/openshell/external-secret.yaml`

Do NOT commit yet.

---

## Task 4: ArgoCD Application + manifest commit

**Files:**
- Create: `base-apps/openshell-secrets.yaml`

- [ ] **Step 1: Create the ArgoCD Application**

Write to `base-apps/openshell-secrets.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: openshell-secrets
  namespace: argo-cd
  annotations:
    # Sync-wave -2: must land before the openshell chart Application (-1)
    # so the StatefulSet's sandbox-jwt volume finds Secret/openshell-jwt-keys
    # on first mount attempt.
    argocd.argoproj.io/sync-wave: "-2"
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/openshell
    directory:
      recurse: false
  destination:
    server: https://kubernetes.default.svc
    namespace: openshell
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

- [ ] **Step 2: Validate**

Run: `kubectl apply --dry-run=server -f base-apps/openshell-secrets.yaml`
Expected: `application.argoproj.io/openshell-secrets created (server dry run)`.

- [ ] **Step 3: Stage all manifests**

Run: `git add base-apps/openshell-secrets.yaml base-apps/openshell/ && git status --short base-apps/`

You should see exactly three new files staged:
- `base-apps/openshell-secrets.yaml`
- `base-apps/openshell/external-secret.yaml`
- `base-apps/openshell/secret-store.yaml`

- [ ] **Step 4: Verify master-app pattern picks up the new Application file**

The master-app watches `base-apps/*.yaml`. Confirm by reading: `cat base-apps/master-app.yaml | grep -A2 path:`

Expected: `path: base-apps` (or equivalent recursive directory source). The new `base-apps/openshell-secrets.yaml` will be auto-discovered on next sync.

- [ ] **Step 5: Stage the spec doc too**

The spec was written but not yet committed. Run: `git add docs/superpowers/specs/2026-05-27-openshell-jwt-keys-fix-design.md docs/superpowers/plans/2026-05-27-openshell-jwt-keys-fix.md`

- [ ] **Step 6: Ask before committing**

Proposed commit message (ASK USER):

```
fix(openshell): provision openshell-jwt-keys via Vault + ESO

Phase 2 of openshell rollout unblocks the new kagent v0.9.4 controller
that's been CrashLooping because openshell-0 was stuck in
ContainerCreating waiting on Secret/openshell-jwt-keys.

The chart bundles JWT-keypair generation inside its pkiInitJob, which
we disabled to avoid duplicating cert-manager TLS work — this PR
replaces the missing key generation with a Vault-backed ExternalSecret
mirroring our existing kagent secrets pattern.

- scripts/bootstrap-openshell-jwt.sh: one-time local keypair gen + Vault write
- base-apps/openshell/secret-store.yaml: Vault SecretStore (role openshell)
- base-apps/openshell/external-secret.yaml: ExternalSecret -> openshell-jwt-keys
- base-apps/openshell-secrets.yaml: ArgoCD Application at sync-wave -2

Spec: docs/superpowers/specs/2026-05-27-openshell-jwt-keys-fix-design.md
Plan: docs/superpowers/plans/2026-05-27-openshell-jwt-keys-fix.md
```

If approved, commit. (If Task 1 was already committed separately, drop the `scripts/...` line from the body.)

---

## Task 5: Bootstrap the Vault keypair (USER-EXECUTED, local)

This task is run by the user from their local machine after the code is staged/committed. The cluster is not yet touched.

- [ ] **Step 1: Confirm Vault prerequisites are in place**

User runs in their shell:

```bash
which vault openssl jq kubectl
echo "VAULT_ADDR=$VAULT_ADDR"
vault token lookup -format=json | jq '.data.policies'
```

Expected: all four binaries resolved, `VAULT_ADDR` non-empty, token policies do NOT include `"root"`. If running root token is the only option, the user must add `--allow-root` in step 3.

If `vault` is not on PATH because Vault is only reachable in-cluster, run in a separate terminal:
```bash
kubectl port-forward -n vault svc/vault 8200:8200
```
Then `export VAULT_ADDR=http://127.0.0.1:8200` and `vault login ...` in this terminal.

- [ ] **Step 2: Run the script in dry-run mode first**

```bash
./scripts/bootstrap-openshell-jwt.sh --dry-run
```

Expected output includes:
- `[bootstrap-openshell-jwt] preconditions OK`
- A `DRY-RUN: vault policy write openshell-read <<EOF` block with the read policy HCL
- A `DRY-RUN: vault write auth/kubernetes/role/openshell ...` line
- A `DRY-RUN: vault kv put k8s-secrets/openshell/jwt signing_pem=@... public_pem=@... kid=...` line
- The "Bootstrap complete." block with a `kid` and a public key PEM

If any line looks wrong (e.g., wrong namespace, wrong SA, wrong path), stop and fix the script before continuing.

- [ ] **Step 3: Run for real**

```bash
./scripts/bootstrap-openshell-jwt.sh
```

Expected: same flow as dry-run, but Vault writes actually happen. Final block prints the `kid` and public key. Save the `kid` somewhere (not secret, but useful for debugging later).

- [ ] **Step 4: Verify Vault state**

```bash
vault kv get k8s-secrets/openshell/jwt
```
Expected: three fields shown — `signing_pem`, `public_pem`, `kid`. Each non-empty.

```bash
vault read auth/kubernetes/role/openshell
```
Expected:
```
bound_service_account_names      [default]
bound_service_account_namespaces [openshell]
policies                         [openshell-read]
ttl                              24h
```

```bash
vault policy read openshell-read
```
Expected: the policy HCL granting `read` on `k8s-secrets/data/openshell/*`.

- [ ] **Step 5: (Idempotency check, optional)**

```bash
./scripts/bootstrap-openshell-jwt.sh
```
Expected: prints `already bootstrapped at k8s-secrets/openshell/jwt — pass --rotate to regenerate` and exits 0. Vault state is unchanged.

---

## Task 6: Push branch and watch ArgoCD sync

- [ ] **Step 1: Verify branch state**

Run: `git log --oneline -5 && git status`

Expected: the Task 1 + Task 4 commits are present; working tree is clean. No unstaged or untracked files in `base-apps/openshell*` or `scripts/`.

- [ ] **Step 2: Ask before pushing**

Pushing triggers ArgoCD auto-sync as soon as the PR merges to `main`. ASK USER explicitly before running:

```bash
git push origin feat/phase2-openshell-agentharness
```

If the user wants to open a PR first (standard GitOps flow), use `gh pr create` per `docs/pr_template.md` — but that's outside the scope of this plan. The plan ends at push; merging is a user gate.

- [ ] **Step 3: After merge to main, watch ArgoCD reconcile (USER-EXECUTED)**

The user watches sync progress:

```bash
kubectl get applications -n argo-cd openshell-secrets openshell -w
```

Expected ordering (Status / Sync columns):
1. `openshell-secrets` reaches `Synced / Healthy` first (sync-wave -2).
2. `openshell` reconciles next; its StatefulSet pod should leave `ContainerCreating` once the Secret materializes.

Stop watching once both are `Synced / Healthy`. If `openshell-secrets` stalls at `OutOfSync`, run `argocd app sync openshell-secrets` (or use the UI).

---

## Task 7: Cleanup stuck state + end-to-end verification

All steps in this task are USER-EXECUTED against the live cluster.

- [ ] **Step 1: Force the stuck openshell-0 to re-mount immediately**

Without this, kubelet's mount backoff can hold the pod in `ContainerCreating` for up to ~2 minutes after the Secret appears.

```bash
kubectl delete pod -n openshell openshell-0
```

Expected: pod deletes; the StatefulSet immediately recreates it. The new pod should reach `1/1 Running` within ~30s once the Secret is in place.

- [ ] **Step 2: Confirm ExternalSecret synced**

```bash
kubectl get externalsecret -n openshell openshell-jwt-keys \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{"\n"}'
```
Expected: `True`

```bash
kubectl get secret -n openshell openshell-jwt-keys -o jsonpath='{.data}' | jq 'keys'
```
Expected: `["kid", "public.pem", "signing.pem"]` (sorted).

- [ ] **Step 3: Confirm openshell-0 is Ready**

```bash
kubectl get pod -n openshell openshell-0
```
Expected: `READY 1/1`, `STATUS Running`, `RESTARTS` low (0–2). Age may be a few minutes.

```bash
kubectl logs -n openshell openshell-0 --tail=30
```
Expected: gateway-startup log lines. No `signing key`, `jwt`, or `secret not found` errors.

- [ ] **Step 4: Confirm gateway is reachable on cluster network**

```bash
kubectl run -n openshell --rm -i --restart=Never --image=busybox:1.36 net-test -- \
  wget -qO- --no-check-certificate https://openshell.openshell.svc.cluster.local:8080/healthz
```
Expected: a 200-OK style response (e.g., `ok` or `{"status":"ok"}`).

If this fails with TLS errors, the gateway cert may not be reconciled yet — wait 30s and retry. If it fails with connection refused, openshell-0 isn't actually listening; check `kubectl logs`.

- [ ] **Step 5: Confirm the new kagent controller exited CrashLoop**

```bash
kubectl get pods -n kagent -l app.kubernetes.io/component=controller
```
Expected: exactly **one** controller pod, on ReplicaSet `568c7b55fb` (the new hash), STATUS `Running`, RESTARTS low. The old ReplicaSet (`857c56b6`) should be scaled to 0 by the rollout.

```bash
kubectl logs -n kagent -l app.kubernetes.io/component=controller --tail=30 | grep -iE 'openshell|sandbox|error'
```
Expected: NO `unable to build openshell sandbox backends` line. Expected to see a benign `openshell sandbox backends initialized` (or chart's equivalent successful-init log).

- [ ] **Step 6: End-to-end smoke (sample AgentHarness from commit 5fa23e7)**

```bash
kubectl get agentharness -n kagent
```
Expected: the `homelab-harness` resource exists. Check whatever READY/SYNCED column is shown — should not be `Failed`.

Optional manual smoke: from the kagent UI (`kagent.arigsela.com`), invoke the sample harness with a trivial command (e.g., `echo hello`). Confirm a response comes back and that the kagent-controller logs do not show `OPENSHELL_GATEWAY_URL`-related errors during the request.

- [ ] **Step 7: Mark verification complete**

If steps 1–6 all pass, the rollout is done. Update the task list to mark Task 7 complete.

---

## Self-review notes

Cross-checked against spec sections:

- **Problem & root cause** → captured in plan header (Goal/Architecture).
- **Architecture** → Task 4 (ArgoCD Application) + Tasks 2/3 (SecretStore/ExternalSecret) + Task 1 (bootstrap script).
- **Files added/changed** → Tasks 1–4 cover every file in the spec's file table.
- **Bootstrap script behavior (preconditions, default flow, --rotate, --dry-run, safety)** → Task 1, steps 3–6.
- **ExternalSecret field naming (gjson pitfall)** → Task 3 step 1 includes the inline comment in the manifest itself for the implementer.
- **Argo ordering & rollout sequence** → Task 4 step 1 sets sync-wave -2 explicitly; Task 6 step 3 watches the wave order.
- **Cleanup of stuck state** → Task 7 step 1.
- **Verification (5 checks)** → Task 7 steps 2–6 implement all five spec checks plus an explicit pod-replacement check (step 5).
- **Out of scope items** → respected (no changes to `base-apps/openshell.yaml`, `base-apps/kagent.yaml`, or controller mTLS).

No placeholders ("TODO", "TBD", "implement later") remain. Type/name consistency checked: `openshell-jwt-keys` appears identically in spec, ExternalSecret target, chart expectation, and verification commands.
