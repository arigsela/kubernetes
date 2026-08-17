# JupyterLab Workspace — Design

- **Date:** 2026-08-17
- **Status:** Draft for review
- **Goal:** A JupyterLab workspace at `jupyter.arigsela.com`, usable from a browser by a human and from `/api/kernels` by Claude Code running on the operator's laptop.
- **Related:** [Managed Apps ApplicationSet](2026-08-13-managed-apps-appset-design.md), [ADP Remaining Pillars](2026-07-14-adp-remaining-pillars-roadmap.md) (the Execution pillar this deliberately does *not* close).

## 1. Problem

There is no interactive Python surface in the cluster. Ad-hoc analysis — of Loki
exports, of the agent action record, of anything else — currently happens on a
laptop, against whatever Python environment that laptop happens to have. Batch
Python has a home (Argo Workflows); interactive Python does not.

Two consumers, established during design:

1. **A human, in a browser.** JupyterLab UI, persistent notebooks.
2. **Claude Code, from the operator's laptop.** Programmatic execution against
   `/api/kernels` so analysis can run without a local Python environment.

Both are **the same principal** — the operator. This is the single most
load-bearing fact in this design, and section 3 explains what it buys.

## 2. Scope

**In scope.** A single-workspace JupyterLab deployment; token authentication;
a dedicated S3 scratch bucket with a scoped IAM user; notebooks version-controlled
in a separate git repository; network isolation from the rest of the cluster.

**Explicitly out of scope.** Multi-user JupyterHub. kagent tool integration
(no taxonomy entry, no `requireApproval` wiring, no Kyverno capability class —
none of it is needed, see 3.2). Ephemeral `Sandbox`-backed execution. GPU.
Scheduled notebook execution.

**Deliberately not fixed here.** `templates/new-app/skeleton-ingress/` still
emits `nginx-ingress.yaml`, which nothing has served since the Istio cutover.
This design hand-writes its manifests instead of scaffolding them, and logs the
staleness as a finding for a separate change (§9).

## 3. Key decisions

### 3.1 One long-lived Deployment, not JupyterHub and not `agent-sandbox`

Three approaches were considered.

| | Approach | Verdict |
|---|---|---|
| 1 | Single JupyterLab `Deployment` | **Chosen** |
| 2 | JupyterHub via Helm | Rejected — right for a team, disproportionate for one operator |
| 3 | Ephemeral pods on the installed `agent-sandbox` CRDs | Rejected *for now* — best security story, no prior art, slowest to a usable notebook |

Approach 2 was rejected because it buys per-session isolation and OIDC that this
deployment does not need, at the cost of a Helm chart, hub RBAC to spawn pods, a
hub database — and it would *still* need a static-token side-door, because Claude
Code cannot drive an interactive OAuth flow. The multi-tenancy machinery would be
carrying exactly one tenant.

Approach 3 is where the *agent* half of this should eventually live, and the
`Sandbox` CRDs (`kubernetes-sigs/agent-sandbox` v0.4.6) are already installed via
`base-apps/agent-sandbox-crds` with **zero** `Sandbox` CRs anywhere in the repo.
It is rejected now only on sequencing: first-adopting a v0.4.x API and writing a
driver shim is a bigger project than this one, and interactive browser use fits
it awkwardly. This design should not make that migration harder — §8 records the
seam.

### 3.2 One endpoint, one token — because the human and the agent are one principal

The two "subsystems" identified at the start of design (a browser workspace, and
an agent execution surface) collapse into one deployment. Jupyter Server serves
the Lab UI and `/api/kernels` from the same process on the same port, and both
clients authenticate with the same token.

This is not a shortcut. The alternative considered — Dex OIDC for the browser
plus a static-token bypass path for the agent — was rejected because **the bypass
path becomes the entire security boundary** while looking like a footnote in the
config. One path, one credential, one thing to get right.

It also means the agent half needs nothing from the kagent guardrail stack: no
entry in `agent-capability-taxonomy.yaml`, no capability class, no HITL approval
wiring. Those controls govern *kagent agents* binding *kagent tools*. Claude Code
authenticating as the operator over HTTPS is not that, and pretending otherwise
would add ceremony without adding control.

### 3.3 The blast-radius argument for tolerating a long-lived pod

A persistent pod that executes arbitrary Python and holds AWS credentials is
exactly the shape `agent-capability-taxonomy.yaml` classes as `destructive` —
"an arbitrary-code escape hatch whose blast radius is not bounded by its name."
That objection is answered by bounding the blast radius structurally, not by
trusting the workload.

Requirements gathering established the notebooks need **local compute, a scratch
bucket, and PyPI — and nothing in-cluster**. That constraint is what makes the
following enforceable:

| Control | Mechanism | Effect |
|---|---|---|
| No cluster access | `automountServiceAccountToken: false` | No ServiceAccount token in the pod. The kernel cannot reach the Kubernetes API at all. |
| No lateral movement | `NetworkPolicy` — egress to `0.0.0.0/0` **except** RFC1918, DNS excepted | Vault, PostgreSQL, Loki, Ollama unreachable. Follows `base-apps/atlantis/network-policy.yaml`. |
| Bounded AWS | IAM user scoped to `arn:aws:s3:::asela-jupyter-scratch/*` | Total compromise yields one throwaway bucket. |
| Bounded ingress | Gateway allow-list (4 × /32) **and** Jupyter token from Vault | Two independent controls must fail. |

Plus `runAsNonRoot` (uid 1000, fsGroup 100) and CPU/memory limits so a runaway
cell cannot evict neighbours.

**The NetworkPolicy is the load-bearing control**, and it is the one thing here
whose enforcement is not already proven in this cluster. `base-apps/atlantis`
carries the repo's only other NetworkPolicy. Verification that k3s's policy
controller enforces egress rules alongside ztunnel is an explicit implementation
step (§7, test 6), not an assumption.

### 3.4 Upstream image, PVC mounted at `/home/jovyan`

Image: `quay.io/jupyter/scipy-notebook`, **pinned by digest**. The repo pins
upstream images and builds custom ones only for real applications
(`backstage-portal`, `homelab-agent`); introducing an image build pipeline to
`pip install boto3` is disproportionate.

Instead the PVC mounts at **`/home/jovyan`** — the whole home directory, not the
conventional `work/` subdirectory. This makes `~/.local` persistent, so
`pip install --user -r requirements.txt` survives pod restarts. The trade is that
the mount shadows the image's home contents, which for `scipy-notebook` is
effectively just an empty `work/`.

### 3.5 Notebooks in git, PVC holds nothing precious

`local-path` pins a `ReadWriteOnce` volume to whichever node binds it first —
the single-node exposure already documented for `postgresql`. Rather than fight
that, the design ensures the PVC contains nothing irreplaceable.

| Lives in | Contents | Recovery if the node dies |
|---|---|---|
| `arigsela/notebooks` (git) | `.ipynb` files, `requirements.txt` | `git clone` — nothing lost |
| PVC (20Gi) | scratch data, `~/.local`, Lab UI state | re-clone + re-`pip install` — minutes |
| S3 `asela-jupyter-scratch` | datasets, outputs too large for git | unaffected |

The PVC still carries `argocd.argoproj.io/sync-options: Prune=false` per repo
convention — a manifest rename must not destroy a working environment even when
rebuilding it is cheap.

Git authentication uses a fine-grained PAT scoped to the notebooks repository,
delivered by ESO from `k8s-secrets/jupyter` and mounted read-only at
`/etc/jupyter-secrets/github-token` — **outside** the home directory. A git
credential helper reads it at each invocation. It must not be written to
`~/.git-credentials`, because the home is the PVC and a copy there would survive
rotation in Vault and outlive the secret it came from. Path-scoped per consumer,
per the agent-identity contract.

### 3.6 Two Applications, split by lifecycle

- **`jupyter`** — hand-written `base-apps/jupyter.yaml`. It requires
  `directory.exclude: '{catalog-info.yaml,mkdocs.yml}'`, which disqualifies it
  from the `managed-apps` ApplicationSet.
- **`jupyter-aws-infrastructure`** — via `appsets/managed-apps/`, matching
  `agent-audit-aws-infrastructure`.

Split because the bucket and IAM user must survive teardown and rebuild of the
workspace, and because Crossplane reconciles against AWS on a different cadence
than a `Deployment`.

## 4. Architecture

```
Browser ─┐
         ├→ jupyter.arigsela.com:443 → Istio Gateway `main`
Claude ──┘        │                    (AuthorizationPolicy: 4 × /32)
   Authorization: token <…>
                  └→ HTTPRoute → Service:8888 → jupyter pod (uid 1000)
                                                   ├→ S3 asela-jupyter-scratch
                                                   ├→ PyPI / internet
                                                   └→ GitHub (notebooks repo)
                                       ✗ blocked: all RFC1918 (NetworkPolicy)
                                       ✗ blocked: Kubernetes API (no SA token)
```

## 5. File inventory

### New — `base-apps/jupyter/`

| File | Notes |
|---|---|
| `deployments.yaml` | digest-pinned image, `automountServiceAccountToken: false`, uid 1000 / fsGroup 100, limits |
| `services.yaml` | ClusterIP :8888 |
| `pvc.yaml` | 20Gi `local-path`, RWO, `Prune=false` |
| `httproute.yaml` | `parentRefs` → `main`/`istio-ingress`, `sectionName: https-jupyter` |
| `certificate.yaml` | issuer **`letsencrypt-route53`** — see §6 |
| `reference-grant.yaml` | required: the Gateway is in `istio-ingress`, the TLS Secret in `jupyter`; Gateway API forbids that cross-namespace read without a grant |
| `secret-store.yaml` | Vault role `jupyter` |
| `external-secret.yaml` | `token`, `github-token` from `k8s-secrets/jupyter` |
| `network-policy.yaml` | modelled on `base-apps/atlantis/network-policy.yaml` |
| `catalog-info.yaml`, `docs.md`, `runbook.md`, `mkdocs.yml`, `docs/` | agent-docs contract |

### New — elsewhere

- `base-apps/jupyter.yaml` — Application with the `directory.exclude` guard.
- `base-apps/jupyter-aws-infrastructure/` — `s3-bucket.yaml`, `iam-user.yaml`,
  `iam-policy.yaml`, `iam-policy-attachment.yaml`, `access-key.yaml`. Mirrors
  `agent-audit-aws-infrastructure`, but **read/write on one bucket** rather than
  write-only. `access-key.yaml` writes its connection secret into the `jupyter`
  namespace (keys: `username` → access key ID, `attribute.secret` → secret key —
  there is no `attribute.id`).
- `appsets/managed-apps/jupyter-aws-infrastructure.yaml`
- `tests/appset/golden/jupyter-aws-infrastructure.yaml`

### Edited

- `base-apps/istio-ingress/gateway.yaml` — add the `https-jupyter` listener.
- `base-apps/istio-ingress/authorizationpolicy.yaml` — add a restricted rule for
  `jupyter.arigsela.com` and `jupyter.arigsela.com:*`, 4 × /32. **Verified safe
  under IP rotation:** `rewrite_policy()` in
  `base-apps/wan-ip-monitor/configmap-reconcile.yaml` rewrites every line whose
  stripped form is exactly `- <old_ip>/32`, so a fourth occurrence is handled.
- `tests/appset/test_managed_apps.py` — add to `EXPECTED_APPS`.
- `scripts/agent-docs-scope.txt` — add `jupyter`.
- Regenerate `base-apps/index.md` (`gen-okf.py`) and techdocs (`gen-techdocs.py`).

## 6. Out-of-band steps (not in git)

These are not manifests and will not happen by syncing.

1. **Route 53** — create the `jupyter.arigsela.com` A record by hand. The 21
   existing records are hand-edited via the AWS API, not Terraform-managed.
2. **Vault** — write `k8s-secrets/jupyter` with `token` (a generated Jupyter
   token) and `github-token` (a PAT scoped to the notebooks repo); create the
   `jupyter` Kubernetes auth role bound to the `jupyter` namespace's `default`
   ServiceAccount.
3. **GitHub** — create the `arigsela/notebooks` repository.

`letsencrypt-prod` **must not** be used for this host: its only solver is
`http01.ingress.class=nginx`, which nothing has satisfied since the Istio
cutover. Use `letsencrypt-route53` (as `base-apps/donetick/certificate.yaml`
documents).

## 7. Verification

**Automated** — all existing gates, no new harness:

```
python -m pytest tests/appset/ -q
python3 scripts/validate-agent-docs.py --repo-root .
python3 scripts/gen-okf.py --check
python -m pytest tests/wan_ip/ -q
```

All four pass on `main` as of 2026-08-17 (`gen-okf.py --check`: "OKF bundle in
sync (35 app directories)"; `validate-agent-docs.py`: "21 apps in scope, 0
warning(s)"), so any failure after this change is attributable to it.

**Manual acceptance, in order:**

1. `Certificate/jupyter-tls` reaches `Ready`. The most likely first failure.
2. Browser at `jupyter.arigsela.com` prompts for a token; the correct token
   loads JupyterLab.
3. No token → 403. Request from a non-allow-listed source → 403 at the gateway.
4. `POST /api/kernels` with the token returns a kernel id; executing `1+1` over
   the websocket returns `2`. **This is the acceptance test for the agent half.**
5. `boto3` writes and reads an object in `asela-jupyter-scratch`.
6. **Negative test, pass/fail on the whole design:** from inside the pod,
   connecting to `postgresql.postgresql.svc.cluster.local:5432` must **fail**,
   and `kubectl`-equivalent calls to the API server must fail. If either
   succeeds, the central control of §3.3 is absent and the deployment should not
   be considered done.

## 8. The seam to a future `Sandbox`-backed design

If the agent half later moves to ephemeral `Sandbox` pods (§3.1, approach 3),
what changes and what does not:

- **Unchanged:** the S3 bucket and IAM user (separate Application, by design);
  the notebooks git repo; the NetworkPolicy shape.
- **Changes:** the agent stops calling `jupyter.arigsela.com/api/kernels` and
  starts creating `Sandbox` CRs. The token stops being shared between two
  clients and becomes browser-only.
- **The thing to avoid meanwhile:** adding a *second* authentication path or a
  kagent tool pointing at this deployment. Either would make the migration a
  breaking change for callers rather than an additive one.

## 9. Findings logged, not fixed

- `templates/new-app/skeleton-ingress/` emits `nginx-ingress.yaml`; nothing
  serves it post-Istio-cutover. The scaffolder's golden path produces a
  non-functional ingress for any new app that requests one.
- `base-apps/agent-sandbox-crds` installs CRDs that no manifest in the repo
  uses. Either adopt them (§8) or remove the Application.

## 10. Open questions

None blocking. Two judgement calls worth a second look at review:

1. **PVC at `/home/jovyan` vs `/home/jovyan/work`.** The whole-home mount buys
   persistent `pip install --user` at the cost of shadowing image home contents.
   If that proves awkward, the fallback is a thin custom image with a pinned
   `requirements.txt`, at the cost of a build pipeline.
2. **20Gi PVC.** A guess. `local-path` on a single node; resizing is not free.
