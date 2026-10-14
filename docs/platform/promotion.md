# Promotion policy

What merges without a human, and why that list is short. Spec:
`docs/superpowers/specs/2026-10-12-signal-dispatch-and-promotion-design.md`.

## The two automatic classes
A pull request merges by itself when it is open, not a draft, from this repository, GitHub
reports it mergeable, every check run on the head commit completed green or skipped with none
pending, every commit status is `success`, and **one** of the two classes below admits it
(`scripts/promote.py`, policy `.github/promotion-policy.yaml`, read at the base ref `main`).
Each run logs the class that admitted the PR as `rule`, or the reasons each class refused it.

### `renovate-bump` (policy section `auto`)
- opened by the Renovate app (`arigsela-renovate[bot]`);
- labelled `review:skip` by triage, **and** shaped like triage's deterministic skip rule when
  re-derived from the diff: every file is docs, or only `image:`/`tag:` value lines changed, at
  most 4 changed lines in total (the classifier can also say `skip`; a model's verdict alone never
  promotes);
- at most 2 changed files, none under a deny prefix (Terraform, `.github/`, appsets, Ansible,
  node-config, Vault, PostgreSQL, kagent, Substrate, admission policies, Istio, Dex,
  cluster-rbac, the dispatcher, Crossplane packages, `renovate.json5`). Renovate's
  `renovate/stability-days` is a commit status, not a check, and must be `success`.

### `docs-only` (policy section `auto_docs`, added 2026-10-13)
- opened by the owner (`arigsela`), which includes the pull requests the coding agent opens
  under the owner's account;
- labelled `review:skip`, and every changed file is documentation: `*.md` anywhere or anything
  under `docs/` (triage's `always_skip.paths`);
- at most 10 changed files, none under a deny prefix: `.github/`, `.claude/`, `CLAUDE.md`,
  `AGENTS.md`, `templates/`. The instruction files and the contracts are documentation that
  steers what the agent does next, so a human reads every change to them.

The policy read at the PR's base **ref** (`main`) must parse, and every class must deny
`.github/` or the loader refuses the file. A PR cannot loosen its own promotion, and a
tightening applies to already-open PRs immediately.

Everything else is `owner`: the triage label says how closely to read, and the owner merges.

## How it runs
`.github/workflows/promote.yaml` triggers on `workflow_run` of `Validate Manifests` and `PR Triage`
(so the `main` version of the workflow always runs), mints a token from the
`arigsela-promotion` GitHub App, and runs the script. On `auto` it merges by rebase pinned to
the evaluated head SHA and leaves one `<!-- promotion -->` comment. On `owner` it logs only.

## Identity
The app is a bypass actor on the `main` ruleset in pull-request mode: it can merge without
the one required approval and cannot push to `main`. It is not Renovate's app: author and
promoter are different principals, and the merge commit's committer shows it.

A class admits a PR only when its author **and every commit on the branch** are by a class
author (committer either the same or GitHub's `web-flow`). A branch is writable by anyone with
Contents write on the repo, including the in-cluster implementer App (class `propose`), so
without that check a commit pushed onto an open Renovate or owner branch would ride that
branch's class into an unread merge (§V.95). Commits not fetched means `owner`.

## Changing the policy
Edit `.github/promotion-policy.yaml` in a PR. It is `review:read` under triage and under a
deny prefix here, so a policy change is always read by a human and never auto-merged.

## Observability
`gh run list --workflow promote` lists decisions; each run logs one JSON line with `class`
and `reasons`. `gh pr list --state merged --search "promotion: auto in:comments"` lists what
was promoted. Live since 2026-10-13: the app, its secret and the ruleset bypass exist, and the
first run with a minted token decided #771 as `owner` for the expected reasons. If the app's
key or installation is ever removed, every run fails at the token step and nothing merges,
which is the safe direction.

## Widening it later
Candidates, each its own policy PR: `review:skim` Renovate PRs for non-stateful apps after
a month of clean `auto` merges. The owner's docs-only class was widened on 2026-10-13 by the
owner's decision, ahead of the coding agent having its own identity (path 3); when it does,
move that class from the owner's login to the agent's.
