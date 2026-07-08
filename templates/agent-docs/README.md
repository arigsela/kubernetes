# Agent-Docs Contract

Every in-scope `base-apps/<app>/` directory carries three files:

| File | Layer | Authoritative for |
|---|---|---|
| `catalog-info.yaml` | Structured (Backstage entity) | owner, dependencies, namespace, lifecycle |
| `docs.md` | Narrative | architecture, config locations, tribal knowledge |
| `runbook.md` | Operational | failure modes (symptom → check → fix), how-to |

## Frontmatter schema (docs.md / runbook.md)

| Key | Type | Rule |
|---|---|---|
| `app` | string | matches the `base-apps/<app>` directory name |
| `catalog_entity` | string | equals `metadata.name` in the sibling `catalog-info.yaml` |
| `kind` | enum | `docs` or `runbook` |
| `namespace` | string | Kubernetes namespace |
| `last_reviewed` | date | ISO `YYYY-MM-DD`; drives the 180-day staleness check |
| `status` | enum | `current`, `wip`, or `deprecated` |
| `tags` | list | short lowercase tokens |
| `sources` | list | repo-relative paths to authoritative files; each must exist |

## GitOps safety (important)
`catalog-info.yaml` is a **Backstage** entity (`apiVersion: backstage.io/v1alpha1`), **not** a Kubernetes manifest. Because it is co-located inside an Argo CD-synced app directory (`base-apps/<app>/`), Argo CD would otherwise try to apply it and **fail sync** (no `backstage.io` CRD exists in the cluster).

This is prevented two ways:

1. **Globally** — the Argo CD config excludes the `backstage.io` `Component`/`Resource` kinds via `resource.exclusions` in `terraform/roots/asela-cluster/argocd.tf`, so Argo CD ignores every `catalog-info.yaml` object cluster-wide. The validator (`scripts/validate-agent-docs.py`) parses that config and fails CI if the exclusion is missing whenever any `catalog-info.yaml` is present. This is applied out-of-band via Terraform, so it must be live before the docs sync.
2. **Per app (in-band)** — each app whose directory carries a `catalog-info.yaml` also sets `spec.source.directory.exclude: catalog-info.yaml` on its Argo CD `Application`. Because the `Application` spec and the `catalog-info.yaml` land in the same commit, Argo CD never renders the file — making a merge safe regardless of when the Terraform change is applied.

Do not remove either guard while co-located `catalog-info.yaml` files exist.

## Rules
- Structured facts live only in `catalog-info.yaml`; prose only in markdown.
- The atlas and docs are a navigation/summary layer. `sources:` files remain authoritative — when a summary looks wrong, go to the source.
- Adding an app to the contract: copy the three templates, fill them in, add the app name to `scripts/agent-docs-scope.txt`, and add a row to `base-apps/_INDEX.md`. Once the global `backstage.io` exclusion is live it already covers the new `catalog-info.yaml`; adding `spec.source.directory.exclude: catalog-info.yaml` to the app's `Application` is recommended as in-band defense-in-depth.
