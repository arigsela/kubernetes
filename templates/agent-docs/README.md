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

## Rules
- Structured facts live only in `catalog-info.yaml`; prose only in markdown.
- The atlas and docs are a navigation/summary layer. `sources:` files remain authoritative — when a summary looks wrong, go to the source.
- Adding an app to the contract: copy the three templates, fill them in, add the app name to `scripts/agent-docs-scope.txt`, and add a row to `base-apps/_INDEX.md`.
