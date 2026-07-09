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

## GitOps safety (important, load-bearing)
`catalog-info.yaml` is a **Backstage** entity (`apiVersion: backstage.io/v1alpha1`), **not** a Kubernetes manifest. Because it is co-located inside an Argo CD-synced app directory (`base-apps/<app>/`), Argo CD would otherwise try to apply it and **fail sync** (no `backstage.io` CRD exists in the cluster).

**The mechanism: per-app `directory.exclude` (required, in-band).** Every app whose directory carries a `catalog-info.yaml` MUST set `spec.source.directory.exclude: catalog-info.yaml` on its Argo CD `Application` (the manifest whose `spec.source.path` is `base-apps/<app>`). Because the `Application` spec and the `catalog-info.yaml` land in the same commit, Argo CD honors the exclude at render time and never applies the file. The validator (`scripts/validate-agent-docs.py`) enforces this per app and CI fails if it is missing.

**Why not a global `resource.exclusions`?** A global `backstage.io` exclusion in `argocd.tf` was tried but found **ineffective**: the argocd Terraform module writes config under the deprecated Helm `server.config.*` path, while the chart reads `configs.cm.*`, so the live `argocd-cm` uses the chart's own default exclusions and never picks ours up. Migrating to `configs.cm` would clobber those chart defaults (the value replaces rather than merges), so the framework relies on the per-app guard instead. See the note in `terraform/roots/asela-cluster/argocd.tf`.

## Rules
- Structured facts live only in `catalog-info.yaml`; prose only in markdown.
- The atlas and docs are a navigation/summary layer. `sources:` files remain authoritative — when a summary looks wrong, go to the source.
- Adding an app to the contract: copy the three templates, fill them in, add the app name to `scripts/agent-docs-scope.txt`, add a row to `base-apps/_INDEX.md`, **and add `spec.source.directory.exclude: catalog-info.yaml` to the app's Argo CD `Application`** (the validator requires it).
