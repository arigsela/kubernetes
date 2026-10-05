# Renovate

**Status:** living. Written 2026-10-05 with Phase 2 of `docs/plans/cve-remediation-automation-implementation-plan.md`. Config: `renovate.json5`. Workflow: `.github/workflows/renovate.yaml`.

Renovate keeps this repo's pinned versions current by opening PRs. Most CVEs in upstream images are fixed by upgrading, so this is the main route by which those fixes reach the cluster (`docs/platform/vulnerability-management.md`). It never merges anything; you do.

## How it runs

- **When:** Mondays 05:00 UTC (the morning after the Sunday image scan), plus on demand. The cadence is the workflow's cron, not a `schedule` in the config, so a manual run acts immediately.
- **As whom:** the GitHub App `arigsela-renovate` (owned by the user `arigsela`, installed on this repo only). The workflow mints a short-lived token from it. Repo settings hold `RENOVATE_APP_ID` and `RENOVATE_APP_CLIENT_ID` (Actions variables) and `RENOVATE_APP_PRIVATE_KEY` (Actions secret).
- **Version:** Renovate `44.137.0`, pinned in the workflow and in the `renovate-config-validate` CI job. Bump both together.

## What it updates

| Manager | Where | What |
|---|---|---|
| `kubernetes` | `base-apps/**/*.yaml` | Container images, **pinned by digest** (`tag@sha256:…`) |
| `argocd` | `base-apps/*.yaml` | Helm chart `targetRevision` (HTTP and OCI repos) and git-source tags |
| `crossplane` | `base-apps/crossplane-*/` | Provider and Function packages (plain tags, no digest) |
| `github-actions` | `.github/workflows/` | Actions, pinned to a commit SHA with a version comment |
| `custom.regex` | lines marked `# renovate:` | The Atlantis image tag in `base-apps/atlantis.yaml` (inline Helm values) and the Argo CD chart in `terraform/modules/argocd/variables.tf` |

**Deliberately not updated:**
- **Our own ECR images** (`852893458518.dkr.ecr.*`): no CI rebuilds yet, and looking them up needs AWS credentials. This is a plan follow-up.
- **`base-apps/postgresql/agent-audit-cronjob.yaml`:** generated, so CI fails on drift. Its image comes from `scripts/gen-agent-audit-cronjob.py`.
- **`rancher/k3s-upgrade`:** the system-upgrade-controller appends the k3s version as the tag, so a digest pin would break upgrades. k3s follows `base-apps/system-upgrade-controller/runbook.md`.
- **Terraform providers, Ansible collections, CI Python pins:** manual for now.
- **`tests/**`, `docs/**`, `templates/**`:** fixtures and quoted examples.

## What the PRs look like

- **One PR per dependency.** A single-image bump changes one or two lines, so pr-triage labels it `review:skip`.
- **Groups for things that must move together:** `istio` (base, cni, istiod, ztunnel), `kagent` (chart + CRDs), `crossplane-aws` (provider family + s3 + iam), and Grafana's monorepo.
- **Digest pinning arrives once, in four PRs**, so no single merge restarts everything: workloads (`renovate/pin-dependencies`), logging (`renovate/pin-image-digests-logging`), stateful Vault and PostgreSQL (`renovate/pin-image-digests-stateful`), and GitHub Actions (`renovate/pin-github-actions-digests`). Merge the stateful one in a quiet window. Every pinned workload restarts once.
- **Major versions** don't open a PR until you tick them on the **Dependency Dashboard** issue.
- **Terraform** (`terraform/**`) PRs carry the label `terraform-apply-first` and a note. Run Atlantis apply on the open PR, and merge only after `atlantis/apply` is green.
- Releases younger than 3 days wait (`minimumReleaseAge`). At most 10 Renovate PRs are open at once.
- Every PR is labelled `renovate`, and commits use Conventional Commits (`chore(deps): …`).

## Weekly routine

1. Open the **Dependency Dashboard** issue. Tick any major update you want raised.
2. Work through the new PRs:
   - **`review:skip` image bumps:** skim the release notes, approve and merge.
   - **Chart bumps:** run `/pr-explainer <n>`. It renders the chart at both versions and shows the Kubernetes resources that actually change.
3. **Merging:** Renovate's PRs are authored by the App, so **you can approve them yourself** and rebase-merge, with no `--admin` needed. If the App or the `okf-autosync` bot pushes after your approval, approve again (the ruleset requires approval of the latest push).

## Running it by hand

```bash
gh workflow run renovate.yaml --repo arigsela/kubernetes                               # real run
gh workflow run renovate.yaml --repo arigsela/kubernetes -f dryRun=full -f logLevel=debug   # log only
gh run list --workflow renovate.yaml --repo arigsela/kubernetes --limit 1
```

**Pause:** `gh workflow disable renovate.yaml --repo arigsela/kubernetes`. To resume, run `gh workflow enable` with the same arguments. Open PRs stay open.

## Changing the config

1. Validate: `npx --yes --package renovate@44.137.0 renovate-config-validator --strict` from the repo root (Renovate 44 needs Node 24). CI runs the same check on any PR that touches `renovate.json5`.
2. Dry-run it locally against the checkout, read-only:

   ```bash
   docker run --rm -v "$PWD":/usr/src/app:ro -w /usr/src/app \
     -e LOG_LEVEL=debug -e LOG_FORMAT=json -e RENOVATE_BASE_DIR=/tmp/renovate \
     -e RENOVATE_CONFIG_FILE=/usr/src/app/renovate.json5 \
     -e RENOVATE_GITHUB_COM_TOKEN="$(gh auth token)" \
     ghcr.io/renovatebot/renovate:44.137.0 --platform=local --dry-run=full > /tmp/renovate.jsonl
   ```

   Read the `packageFiles with updates` log entry; each update carries its `branchName`.

**Gotchas found while setting this up:**
- **Local mode loads `renovate.json5` as *global* config.** Preset values (for example `ignorePaths` from `config:recommended`) then beat the file's own values, unlike a real run. Don't depend on overriding a preset. Exclusions are `packageRules` with `enabled: false`, which behave the same either way.
- **`config:best-practices` digest-pins every docker-datasource dependency**, Crossplane packages and regex-managed tags included, and `pinDigests: false` overrides lost to it in the local run. So the config extends `config:recommended` and opts **into** pinning for Kubernetes images and Actions only.
- **`matchPackageNames` globs:** `**` only spans `/` as a whole path segment. `852893458518.dkr.ecr.**` matched nothing; use a regex (`/^852893458518\\.dkr\\.ecr\\./`).
- **`config:recommended` caps new PRs at 2 an hour**, which would mean 2 per weekly run. `prHourlyLimit: 0` lifts that, and `prConcurrentLimit: 10` is the real brake.

## Key rotation

Once a year, or if the key leaks:
1. On the app's settings page, under **Private keys**, generate a new key.
2. Run `gh secret set RENOVATE_APP_PRIVATE_KEY --repo arigsela/kubernetes < /path/to/new.pem`.
3. Delete the old key on the same page.
4. Run the workflow once to confirm.
