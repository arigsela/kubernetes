# Infrastructure Atlas

> **For agents:** Start here. Traverse: this atlas → a directory `_INDEX.md` → an app's `docs.md`/`runbook.md` → the `sources:` files listed in that doc. This atlas is a **navigation/summary layer**; the `sources:` files are authoritative. If a summary here looks wrong, go read the source.

## 1. System context
- Kubernetes API: `https://192.168.0.100:6443`
- GitOps: Argo CD watches this repo; `base-apps/master-app.yaml` creates an Application per `.yaml` in `base-apps/`.
- Secrets: HashiCorp Vault at `vault.vault.svc.cluster.local:8200` (KV v2, path `k8s-secrets`), surfaced via External Secrets Operator.
- Terraform state: S3 bucket `asela-terraform-states`.

## 2. Platform topology
- **Argo CD** (`base-apps/argo-cd/`) — control plane; master-app pattern.
- **base-apps/** — one Application per app; each app directory holds its manifests and (in-scope) its agent-docs contract.
- **terraform/** — `roots/asela-cluster` is the active root; reusable `modules/`.
- **Crossplane** — declarative cloud resources.

## 3. GitOps data flow
`git commit` → Argo CD detects drift → syncs manifests to the cluster (`prune: true`, `selfHeal: true`). Secrets resolve at runtime: `SecretStore` + `ExternalSecret` → Vault.

## 4. Cross-cutting concerns
- **Secrets:** Vault + External Secrets Operator; per-namespace `SecretStore`.
- **TLS/certs:** cert-manager (`base-apps/cert-manager/`) with Route 53 DNS-01.
- **Ingress/mesh:** nginx-ingress and Istio ambient mesh.
- **Observability:** logging/Loki/coroot.

## 5. Known gaps
| Gap | Recommendation | Source |
|---|---|---|
| Only 4 apps carry the agent-docs contract | Backfill remaining apps under CI gating | `scripts/agent-docs-scope.txt` |

## 6. Source registry
| Domain | Authoritative location |
|---|---|
| App manifests | `base-apps/<app>/` |
| Argo CD Applications | `base-apps/<app>.yaml`, `base-apps/master-app.yaml` |
| Infrastructure | `terraform/roots/asela-cluster/`, `terraform/modules/` |
| Secret wiring | per-app `secret-store.yaml` / `external-secret*.yaml` |
| Doc contract & index | `templates/agent-docs/README.md`, `base-apps/_INDEX.md` |

## 7. App index
See `base-apps/_INDEX.md` for the per-app index, `terraform/_INDEX.md` and `docs/_INDEX.md` for those trees.
