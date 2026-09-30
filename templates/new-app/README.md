# New Application template (the golden path)

A Backstage scaffolder template, **New Application** (`metadata.name: new-application`), that
renders a complete `base-apps/<app>/` GitOps app and opens a PR to `arigsela/kubernetes`.
Nothing reaches the cluster until the PR merges and Argo CD syncs it. The one exception is
`vault:setup`, which writes to Vault while the wizard runs.

- `template.yaml` is the form and the steps. The `skeleton*/` directories are what gets rendered.
- The portal loads `template.yaml` from this repo's `main` through a url catalog location in the
  `arigsela/backstage` app-config. Template and skeleton changes therefore go live on the next
  catalog refresh after merge, with no image rebuild. The two custom actions,
  `newapp:validate-name` and `vault:setup`, live in the Backstage image.
- Scope: one stateless HTTP workload (Deployment + Service). No StatefulSet, CronJob or Job, no
  database, and the System must already exist in the catalog.

## Known issues (read before using it)

1. **"Expose via nginx Ingress" produces an app with no route and no certificate** (SPEC T76).
   `skeleton-ingress/` still renders an nginx `Ingress` with
   `cert-manager.io/cluster-issuer: letsencrypt-prod`. ingress-nginx was replaced by the Istio
   Gateway on 2026-07-31, and the `letsencrypt-prod` ClusterIssuer was deleted on 2026-09-24
   (SPEC T75). Nothing serves the Ingress and nothing issues its cert. The Crossplane
   `XApplication` Composition has the same defect.
   **Workaround:** leave the toggle off and add the host by hand in the same PR, following
   `base-apps/istio-ingress/runbook.md` ("Adding a host"): a listener in
   `base-apps/istio-ingress/gateway.yaml`; an `HTTPRoute`, a `ReferenceGrant` and a `Certificate`
   (issuer `letsencrypt-route53`) in the app namespace; and a rule in
   `base-apps/istio-ingress/authorizationpolicy.yaml`. Copy `base-apps/backstage/httproute.yaml`,
   `certificate.yaml` and `reference-grant.yaml`. Also add the hostname to wan-ip-monitor's
   `MANAGED_HOSTNAMES` with a Route 53 A record (`base-apps/wan-ip-monitor/docs.md`). A host
   without an IP allow-list must also join the WAF scope (`base-apps/istio-waf/docs.md`). CI
   enforces both.
2. **The IP allow-list field does nothing.** `whitelist` is rendered only into the
   `nginx.ingress.kubernetes.io/whitelist-source-range` annotation, which nothing reads now.
   Allow-lists live in `base-apps/istio-ingress/authorizationpolicy.yaml`.
3. **A Vault failure loses the PR.** `vault-setup` runs *before* `publish`. If it fails, the
   wizard stops and no PR is opened; the usual cause is an expired scaffolder `VAULT_TOKEN`.
   PR #407 would have moved `publish` first; it was closed unmerged on 2026-07-31. To re-mint the
   token, see the "Provision Vault role + secrets" 403 symptom in `base-apps/backstage/runbook.md`.
4. **The secrets skeleton uses an unserved API.** `skeleton-secrets/` renders
   `external-secrets.io/v1beta1`. ESO stopped serving v1beta1 on 2026-08-03 (SPEC T32), so the
   API server rejects both objects and the Argo sync fails. Change `apiVersion` to
   `external-secrets.io/v1` in the PR; nothing else differs. CI does not catch this: its
   kubeconform schema catalog still publishes the v1beta1 schemas.
5. **`last_reviewed` is hard-coded.** All four skeleton docs (`docs.md`, `runbook.md`,
   `docs/index.md`, `docs/runbook.md`) carry `last_reviewed: 2026-07-20`. The PR body says the
   scaffolder bakes in its render date, which is wrong. Set the merge date in `docs.md` and
   `runbook.md`, then run `python3 scripts/gen-techdocs.py` so the `docs/` copies match (CI runs
   it with `--check`).
6. **A custom namespace breaks Vault auth.** The rendered SecretStore authenticates as Vault role
   `<namespace>`, but `vault:setup` is given `vaultRole: <name>`. The two only match when the
   namespace is left at its default, the app name.

## What it produces

| File | When | What |
|---|---|---|
| `base-apps/<name>.yaml` | always | Argo CD `Application` in `argo-cd`, path `base-apps/<name>`, `automated` prune + selfHeal, `CreateNamespace=true`, `directory.exclude: '{catalog-info.yaml,mkdocs.yml}'` |
| `deployments.yaml` | always | Deployment (`app: <name>`), image, port, replicas, requests/limits, TCP readiness probe; `envFrom` the ConfigMap if `needsConfig` |
| `services.yaml` | always | ClusterIP Service, port 80 → `containerPort` |
| `catalog-info.yaml` | always | Backstage `Component` (`agent-docs/path`, `techdocs-ref: dir:.`, kubernetes label selector + namespace; `lifecycle: experimental`; `dependsOn: resource:vault/vault` if `needsSecrets`) |
| `docs.md`, `runbook.md` | always | agent-docs contract frontmatter + stub sections (`templates/agent-docs/README.md`) |
| `mkdocs.yml`, `docs/index.md`, `docs/runbook.md` | always | TechDocs site; the `docs/` copies are byte-identical to `docs.md`/`runbook.md` |
| `nginx-ingress.yaml` | `exposeIngress` | nginx Ingress at `<host>.arigsela.com` (broken: known issue 1) |
| `secret-store.yaml`, `external-secret.yaml` | `needsSecrets` | SecretStore `vault-backend` (`k8s-secrets` KV v2, Kubernetes auth, role `<namespace>`, SA `default`) + ExternalSecret `<name>-secrets` extracting every property of Vault key `k8s-secrets/<name>` |
| `configmap.yaml` | `needsConfig` | ConfigMap `<name>-config` from `configData` |

After the PR opens, `.github/workflows/okf-autosync.yaml` regenerates `base-apps/index.md` and adds
the app to `scripts/agent-docs-scope.txt`, pushing to the PR branch (same-repo PRs only).

## Parameters

| Step | Field | Default | Notes |
|---|---|---|---|
| Identity | `name` | — | required; `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`; directory, app and default namespace |
| | `description` | — | required; becomes the docs `description:` and the `base-apps/index.md` row |
| | `system` | — | required; EntityPicker, kind System |
| | `owner` | `group:default/platform` | EntityPicker, Group or User |
| | `tags` | — | string array |
| Workload | `image` | — | required |
| | `containerPort` | `8080` | required |
| | `replicas` | `1` | |
| | `namespace` | the name | see known issue 6 |
| Networking & Config | `exposeIngress`, `host` | `false` | see known issue 1 |
| | `whitelist` | the admin allow-list | see known issue 2 |
| | `needsConfig`, `configData` | `false` | key/value list → ConfigMap |
| | `needsSecrets` | `false` | renders the secret skeleton and runs `vault:setup` |
| | `seedOpenaiKey` | `false` | also seed an `openai-api-key` placeholder |
| Resources | `cpuRequest` / `cpuLimit` / `memRequest` / `memLimit` | `100m` / `500m` / `128Mi` / `256Mi` | |

## Steps

1. **`validate-name`** (`newapp:validate-name`): fails the wizard if `base-apps/<name>/catalog-info.yaml`
   or `base-apps/<name>.yaml` already exists on `main`, checked through the GitHub API. Without
   it, reusing a name silently merged into the old directory. It cannot see a directory in an
   open, unmerged PR.
2. **`fetch-core`**, then **`fetch-ingress`** / **`fetch-secrets`** / **`fetch-config`** when the
   toggle is on (`fetch:template`).
3. **`vault-setup`** (`vault:setup`, if `needsSecrets`): creates the policy `<name>-read` and a
   Kubernetes-auth role bound to the namespace's `default` ServiceAccount. It also seeds
   placeholder keys at `k8s-secrets/<name>` if they are missing: always `jwt-secret`, so the
   ExternalSecret is green from the first sync, and `openai-api-key` when `seedOpenaiKey` is set.
   Put real values in Vault afterwards; any property added there syncs through the `dataFrom`
   extract. Real secrets never go through the form.
4. **`publish`** (`publish:github:pull-request`): branch `new-app/<name>`, title
   `feat(<name>): onboard new application`. The wizard links to the PR. There is no
   `catalog:register`; the catalog discovers the Component after merge.

## Testing and editing

```bash
# CI job new-app-template-validate: renders every skeleton into a copy of the tracked repo and runs
# gen-okf --check, yamllint, validate-agent-docs.py, validate-catalog-refs.py and kubeconform
python -m pytest tests/new-app-template/ -q

# ad hoc render
python3 scripts/render-new-app.py --template templates/new-app \
  --values '{"name":"sample-app","description":"x","image":"nginx:1.27","containerPort":8080,"replicas":1,"namespace":"sample-app","system":"default/platform-tooling","owner":"group:default/platform","tags":["nginx"],"exposeIngress":false,"host":"","whitelist":"","needsConfig":false,"configData":[],"needsSecrets":true,"cpuRequest":"100m","cpuLimit":"500m","memRequest":"128Mi","memLimit":"256Mi"}' \
  --out /tmp/na --secrets        # add --ingress / --config to render those skeletons
```

- `render-new-app.py` uses Jinja2 configured like Backstage's Nunjucks (`${{ }}`, `{% if %}`,
  `{% for %}`, templated file names, the `dump` filter). Keep the skeletons inside that shared
  subset, or the harness stops being a faithful proxy. The authoritative check is a dry-run in
  Backstage's template editor.
- The `docs/` copies must stay byte-identical to `docs.md` and `runbook.md`; the tests check this.
- Changing either custom action means a Backstage image build in `arigsela/backstage`.
