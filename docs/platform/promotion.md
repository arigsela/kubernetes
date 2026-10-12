# Promotion policy

What merges without a human, and why that list is short. Spec:
`docs/superpowers/specs/2026-10-12-signal-dispatch-and-promotion-design.md`.

## The one automatic class
A pull request merges by itself when **all** of these hold (`scripts/promote.py`, policy
`.github/promotion-policy.yaml`):
- opened by the Renovate app (`arigsela-renovate[bot]`), from this repository, not a draft;
- labelled `review:skip` by triage (docs-only, or an image-tag bump of at most 4 lines);
- at most 2 changed files, none under a deny prefix (Terraform, `.github/`, appsets, Ansible,
  node-config, Vault, PostgreSQL, kagent, Substrate, admission policies, Istio, Dex,
  cluster-rbac, the dispatcher);
- every check run on the head commit completed green or skipped, none pending;
- the policy read at the PR's **base** commit parsed. A PR cannot loosen its own promotion.

Everything else is `owner`: the triage label says how closely to read, and the owner merges.

## How it runs
`.github/workflows/promote.yaml` triggers on `workflow_run` of `validate` and `pr-triage`
(so the `main` version of the workflow always runs), mints a token from the
`arigsela-promotion` GitHub App, and runs the script. On `auto` it merges by rebase pinned to
the evaluated head SHA and leaves one `<!-- promotion -->` comment. On `owner` it logs only.

## Identity
The app is a bypass actor on the `main` ruleset in pull-request mode: it can merge without
the one required approval and cannot push to `main`. It is not Renovate's app: author and
promoter are different principals, and the merge commit's committer shows it.

## Changing the policy
Edit `.github/promotion-policy.yaml` in a PR. It is `review:read` under triage and under a
deny prefix here, so a policy change is always read by a human and never auto-merged.

## Observability
`gh run list --workflow promote` lists decisions; each run logs one JSON line with `class`
and `reasons`. `gh pr list --search "commenter:app/arigsela-promotion"` lists what was
promoted.

## Widening it later
Candidates, each its own policy PR: `review:skim` Renovate PRs for non-stateful apps after
a month of clean `auto` merges; docs-only PRs by the owner's coding agent once it has its own
identity (path 3).
