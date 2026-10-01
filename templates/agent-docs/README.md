# Agent-Docs Contract

Every in-scope `base-apps/<app>/` directory carries three hand-written files:

| File | Layer | Authoritative for |
|---|---|---|
| `catalog-info.yaml` | Structured (Backstage entity) | owner, dependencies, namespace, lifecycle |
| `docs.md` | Narrative | architecture, config locations, tribal knowledge |
| `runbook.md` | Operational | failure modes (symptom → check → fix), how-to |

`scripts/gen-techdocs.py` also generates a TechDocs mirror next to them, for Backstage: `mkdocs.yml`, `docs/index.md` (a copy of `docs.md`) and `docs/runbook.md` (a copy of `runbook.md`). Never edit the mirror; edit `docs.md`/`runbook.md` and re-run the generator (CI runs it with `--check`).

## OKF conformance

These docs are [Open Knowledge Format](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md) (OKF v0.2) concept documents: markdown + YAML frontmatter, in git, beside the thing they describe. Any OKF-speaking tool or agent can read this repo's knowledge without a bespoke parser. The repo root `index.md` is the bundle root (it carries `okf_version`, checked by `gen-okf.py --check`). The OKF spec moved out of `knowledge-catalog/okf/` (now a frozen copy) into its own repository in August 2026.

Scope note: it is the *knowledge documents* that follow OKF, not the repository as a whole. `README.md`, `CLAUDE.md`, and the specs and plans under `docs/` have no frontmatter, and the in-repo `index.md` files carry frontmatter and tables that OKF §8 doesn't allow. `scripts/gen-okf.py --export <dir>` is what emits a strictly conformant bundle: it moves each index file's prose to an `overview.md` and writes a §8 listing in its place.

### Trust and lifecycle (OKF v0.2 §5)

OKF v0.2 adds frontmatter that says who wrote a document, who checked it, and when it goes stale. Only `last_reviewed` is stored here; the rest would go stale on every commit, so `--export` derives it:

| OKF field | Source | Exported as |
|---|---|---|
| `status` | stored | `draft`, `stable` or `deprecated` |
| `sources` | stored | each entry becomes `{id, resource, last_modified}`; a repo path becomes a GitHub URL at `HEAD`, and `last_modified` is the source file's last commit |
| `generated` | git: the last commit to the doc | `by` is `claude-code/<model>` when the commit has a Claude `Co-Authored-By:` trailer, `process:<bot>` for a GitHub bot, otherwise `human:<email user>` |
| `verified` | `last_reviewed` + the commit that last changed that line | `at` is `last_reviewed`. `by` is `human:<user>` only if that commit has a `Reviewed-by:` trailer or no Claude co-author; otherwise it is the agent |
| `stale_after` | `last_reviewed` + 180 days | the same window as the validator's staleness warning |

`human:` matters because OKF consumers rank a document **human-reviewed** only when a `human:` actor verified it. A `last_reviewed` bump in an agent-written commit counts as **machine-confirmed**. To record that you read a doc, bump `last_reviewed` in a commit carrying `Reviewed-by: Your Name <you@example.com>`.

### Per-claim citations (OKF v0.2 §5.1)

To tie one claim to one source, give that source the mapping form with an `id` and cite it with a markdown footnote whose label is the `id`:

```markdown
sources:
  - base-apps/vault/services.yaml
  - id: vault-sts
    resource: base-apps/vault/statefulsets.yaml
    title: Vault StatefulSet
---
`vault-0` is pinned to `k3s-worker-01`.[^vault-sts]

[^vault-sts]: Vault StatefulSet
```

Use it in new and edited docs where a claim is easy to get wrong; there is no backfill. The validator checks that every footnote label matches a `sources` id and has a definition.

## Frontmatter schema (docs.md / runbook.md)

| Key | Type | Rule |
|---|---|---|
| `type` | enum | OKF concept type: `Kubernetes App Guide` (kind `docs`) or `Kubernetes App Runbook` (kind `runbook`) |
| `title` | string | human display name, e.g. `Weather Kitchen Backend` |
| `description` | string | one line, no newlines; the single source for the generated `base-apps/index.md` |
| `app` | string | matches the `base-apps/<app>` directory name |
| `catalog_entity` | string | equals `metadata.name` in the sibling `catalog-info.yaml` |
| `kind` | enum | `docs` or `runbook` |
| `namespace` | string | Kubernetes namespace |
| `last_reviewed` | date | ISO `YYYY-MM-DD`; drives the 180-day staleness check and OKF's `verified`/`stale_after` on export |
| `status` | enum | OKF lifecycle: `stable`, `draft`, or `deprecated` |
| `tags` | list | short lowercase tokens |
| `sources` | list | authoritative files. Each entry is a repo-relative path, or a mapping `{id, resource, title?}` whose `resource` is a path or an `https://` URL; paths must exist, ids must be unique |

## GitOps safety (important, load-bearing)
`catalog-info.yaml` is a **Backstage** entity (`apiVersion: backstage.io/v1alpha1`), **not** a Kubernetes manifest. Because it is co-located inside an Argo CD-synced app directory (`base-apps/<app>/`), Argo CD would otherwise try to apply it and **fail sync** (no `backstage.io` CRD exists in the cluster).

**The mechanism: per-app `directory.exclude` (required, in-band).** Every app whose directory carries a `catalog-info.yaml` MUST set `spec.source.directory.exclude: '{catalog-info.yaml,mkdocs.yml}'` on its Argo CD `Application` (the manifest whose `spec.source.path` is `base-apps/<app>`). Because the `Application` spec and the `catalog-info.yaml` land in the same commit, Argo CD honors the exclude at render time and never applies the file. The validator (`scripts/validate-agent-docs.py`) enforces the `catalog-info.yaml` part per app and CI fails if it is missing. `mkdocs.yml` (generated TechDocs config, below) is not a Kubernetes manifest either, so exclude it too; the validator doesn't check that part.

**Why not a global `resource.exclusions`?** A global `backstage.io` exclusion in `argocd.tf` was tried but found **ineffective**: the argocd Terraform module writes config under the deprecated Helm `server.config.*` path, while the chart reads `configs.cm.*`, so the live `argocd-cm` uses the chart's own default exclusions and never picks ours up. Re-specifying `resource.exclusions` under `configs.cm` would replace the chart's default exclusion list, and since chart 10.5 `resourceExclusionsAdditional` can append instead; either way it is a cluster-wide behavior change that hasn't been made. The per-app guard works today and is enforced by CI, so the framework relies on it. See the note in `terraform/roots/asela-cluster/argocd.tf` (corrected 2026-08-12).

## Rules
- Structured facts live only in `catalog-info.yaml`; prose only in markdown.
- The bundle root and docs are a navigation/summary layer. `sources:` files remain authoritative — when a summary looks wrong, go to the source.
- `base-apps/index.md` is **generated** from doc frontmatter — never hand-edit it. Change a `description:` and re-run the generator.
- Adding an app to the contract: copy the three templates, fill them in, add the app name to `scripts/agent-docs-scope.txt`, run `python3 scripts/gen-okf.py --repo-root .` and `python3 scripts/gen-techdocs.py --repo-root .`, **and add `spec.source.directory.exclude: '{catalog-info.yaml,mkdocs.yml}'` to the app's Argo CD `Application`** (the validator requires the `catalog-info.yaml` part).
