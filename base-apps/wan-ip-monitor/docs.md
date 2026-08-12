---
type: "Kubernetes App Guide"
title: "wan-ip-monitor"
description: "CronJob that reconciles the home WAN address into Route 53 and the Istio allow-list, opening a PR for the security-sensitive half"
app: wan-ip-monitor
catalog_entity: wan-ip-monitor
kind: docs
namespace: wan-ip-monitor
last_reviewed: 2026-08-12
status: current
tags: [automation, dns, route53, istio, github-actions]
sources:
  - base-apps/wan-ip-monitor/configmap-reconcile.yaml
  - base-apps/wan-ip-monitor/cronjob.yaml
  - base-apps/wan-ip-monitor/external-secret.yaml
  - base-apps/wan-ip-monitor/secret-store.yaml
  - base-apps/wan-ip-monitor/namespace.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - tests/wan_ip
---

# wan-ip-monitor

## What it is
A CronJob (`*/5 * * * *`) that keeps the home WAN address's two dependents in
sync with reality: the Route 53 A records that point at it, and the Istio
`AuthorizationPolicy` allow-list that trusts it. This is the ISP-facing
counterpart to `base-apps/istio-ingress/authorizationpolicy.yaml`'s access
control — that file is the security boundary; this job is what keeps it
pointed at the right address when the ISP reassigns one (already observed
once: `73.7.190.154 -> 76.97.4.210`, 2026-08-12).

The script lives in a single ConfigMap key
(`base-apps/wan-ip-monitor/configmap-reconcile.yaml`, key `reconcile.py`) —
that file is its only home; `tests/wan_ip/conftest.py` loads `reconcile.py`
directly out of the ConfigMap, so there is no second copy that can drift from
what actually runs.

## Reconciler, not change-detector
Every run independently compares reality against two desired states and fixes
whichever has drifted — it never assumes anything from a previous run. This
distinction matters concretely: a change-detector that updated Route 53 first
would see "no change" on its next run and never re-open an allow-list PR that
a human hadn't merged. Because this is a reconciler, the sequence "rotation
detected, Route 53 fixed, PR opened, PR sits unmerged for a day" produces 288
runs (every 5 minutes) that each independently re-derive the same conclusion —
DNS is already correct, the allow-list PR is already open — and, via
`open_allowlist_pr`'s existing-PR lookup, find the same PR rather than opening
a new one or going silent.

The comparison is always `declared == current`: the `arigsela.com/wan-ip`
annotation (below) versus what `detect_wan_ip` observes right now — never a
locally persisted "last known" value.

## Why Route 53 is automatic but the allow-list is a PR
Both halves must move together — correct DNS is useless if the Istio policy
still blocks the resolved address, and vice versa — but they carry very
different blast radii if the job gets detection wrong:

- **Route 53 A records** are corrected directly, no review gate. If detection
  were wrong, the failure mode is *availability*: DNS points somewhere
  unreachable for the protected hosts, which is loud and self-corrects on the
  next successful run.
- **The Istio allow-list** (`base-apps/istio-ingress/authorizationpolicy.yaml`)
  is the security boundary for every protected host on the gateway. Letting
  the reconciler merge its own change there would mean anything holding
  `GITHUB_TOKEN` could grant itself (or an attacker who compromised the
  cluster) unrestricted network access to production simply by lying about the
  detected address. So this half only ever *opens* a PR (`open_allowlist_pr`)
  — it never approves, merges, or auto-merges it, and it is never granted the
  permissions to do so. The `external-secret.yaml`'s GitHub token is scoped to
  `contents:write` + `pull_requests:write` only — deliberately excluding
  anything that could approve, merge, or bypass branch protection. **The
  reconciler must never be able to merge its own PR; the review of that
  security boundary is the entire reason the allow-list half goes through a PR
  at all.**

Until the PR merges, DNS is already correct but the allow-list still trusts
the old address, so the protected hosts resolve and return `403` — this is
expected, not a bug (see runbook: "PR opened but nothing merged").

## The `arigsela.com/wan-ip` annotation contract
`base-apps/istio-ingress/authorizationpolicy.yaml`'s `metadata.annotations`
carries:
```yaml
arigsela.com/wan-ip: "76.97.4.210"
```
This is the **single source of truth** for which of the several `/32`s
allow-listed in that file is the home WAN address (the file also allow-lists
three other, unrelated, non-rotating remote addresses per rule).
`read_declared_wan_ip` reads it; every comparison in `main()` is
`declared == current` against this value and nothing else. `rewrite_policy`
moves the annotation and the matching `ipBlocks` entry together, in the same
commit, so the two can never independently drift —
`tests/wan_ip/test_policy_annotation.py` (Task 1) fails CI if a hand-edit ever
lets them disagree.

`rewrite_policy` is a deliberately line-precise text substitution — not a YAML
round-trip and not a blanket string replace. It rewrites a line only when its
*stripped* form is exactly `- <ip>/32` or exactly the annotation line.
**Comments are never touched, no matter what they say.** This file is heavily
commented, and some of those comments are a dated historical record of past
rotations (e.g. "73.7.190.154 -> 76.97.4.210 (2026-08-12)"). A blanket
substring replace would silently corrupt that record into a lie, or rewrite a
comment that merely mentions the address in prose (e.g. "76.97.4.210/32 covers
the hairpin path").

Because comments are never touched, they can go **stale** — still naming the
old address after a rotation, with nothing in the diff to flag it.
`stale_comment_lines(policy_yaml, old_ip)` finds every comment line still
mentioning `old_ip` after a rewrite, and `open_allowlist_pr` surfaces those
line numbers in the PR body under a "Heads up" note. **This function does not,
and must not, judge which stale comments are safe to update and which are a
dated historical record that must be preserved — that call belongs to whoever
reviews the PR, not to automation.** Never wire up an auto-rewrite of these
flagged comment lines; the asymmetry (rewrite the operative line, only *flag*
the comment) is deliberate, not a gap to close.

## Container image choice
Task 5's deployment brief required verifying, before committing to an image,
that it carries both the AWS CLI and a working `python3`. Live check against
`amazon/aws-cli:2.17.0`:
```
$ kubectl run imgcheck --rm -i --restart=Never --image=amazon/aws-cli:2.17.0 \
    --command -- sh -c 'aws --version; python3 -c "import yaml"'
aws-cli/2.17.0 Python/3.11.8 Linux/6.8.0-134-generic docker/x86_64.amzn.2
sh: python3: command not found
```
The `aws` CLI works, but the image exposes no `python3` on `PATH` to run
`reconcile.py` under — the CLI bundles its own internal interpreter without
exposing it for general scripting.

**Chosen fallback: `python:3.12-slim`.** `reconcile.py`'s `aws_json` was
rewritten from shelling out to the `aws` CLI (`subprocess.run`) to boto3
(`client.list_resource_record_sets` / `client.change_resource_record_sets`);
`records_needing_update` and `build_change_batch` were unaffected, since both
operate on plain dicts regardless of how the JSON was obtained. `aws_json`
still accepts the same argv-shaped `args` list the CLI-backed version did, so
`route53_json`, `main()`'s two call sites, and the test suite's `aws_json`
monkeypatches needed no changes — only what happens *inside* the function
changed. `boto3` is imported lazily inside `aws_json` (not at module level) so
importing `reconcile.py` — which every test in `tests/wan_ip/` does — never
requires `boto3` to be installed in the environment running the tests; only
the real AWS-touching code path needs it, and every test that reaches that
path replaces `aws_json` first via `monkeypatch`.

The CronJob's container `command` installs `boto3==1.35.99` and
`pyyaml==6.0.2` at start (`pip install --quiet ... && python3
/app/reconcile.py`, see `cronjob.yaml`) rather than baking a custom image —
the few seconds of install time are immaterial on a 5-minute schedule, and
there is then no bespoke image to build, publish, or keep patched.

## Where config lives
- **Script**: `base-apps/wan-ip-monitor/configmap-reconcile.yaml` (ConfigMap
  `wan-ip-reconcile`, key `reconcile.py`) — the only copy.
- **Schedule/runtime**: `base-apps/wan-ip-monitor/cronjob.yaml`.
- **Secrets**: `base-apps/wan-ip-monitor/external-secret.yaml` resolves
  `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `GITHUB_TOKEN` from Vault
  (`k8s-secrets` KV v2, key `wan-ip-monitor`) via the namespace `SecretStore`
  (`secret-store.yaml`, Vault kubernetes-auth role `wan-ip-monitor`). The
  Vault policy, role, and secret values are a **human-run, out-of-band
  prerequisite** — not managed by this repo (see runbook: "Job failing on AWS
  auth" / "Job failing on GitHub auth").
- **Allow-list it edits**: `base-apps/istio-ingress/authorizationpolicy.yaml`
  — always via a PR, never a direct commit.
- **Route 53 zone**: `Z0524483LR4JCFNLS7N0` (hardcoded default in
  `reconcile.py`'s `HOSTED_ZONE_ID`, overridable via the `HOSTED_ZONE_ID` env
  var, which the CronJob does not set).
- **n8n notification**: best-effort POST to
  `http://n8n.n8n.svc.cluster.local:5678/webhook/wan-ip-rotated`; a failed
  notification never fails the run (`notify` swallows and logs).

## DRY_RUN
Ships with `DRY_RUN: "true"` on the CronJob deliberately. In dry-run the job
still detects the address, reads the declared one, and logs what it *would*
do — it never calls Route 53's `change-resource-record-sets` and never opens a
PR. Arming it (`DRY_RUN: "false"`) is a separate, deliberately reviewable
commit, not part of the initial deployment (see runbook: "Disabling it" for
the reverse operation).

## Gotchas & tribal knowledge
- The allow-list policy is read from `main` via the GitHub API
  (`read_policy_from_main`), not from a mounted ConfigMap copy of it — `main`
  is the exact revision Argo CD syncs and the PR targets, so the comparison
  and the eventual commit stay anchored to the same source of truth. This also
  avoids granting the job RBAC to read the live Istio
  `AuthorizationPolicy` object.
- `detect_wan_ip` tries two independent sources
  (`https://checkip.amazonaws.com`, `https://ifconfig.me/ip`) and returns the
  first one that yields a usable public address — this exists so one detector
  having a bad day (timeout, captive-portal HTML) doesn't get mistaken for a
  real rotation.
- `is_valid_public_ipv4` rejects the RFC 5737 documentation ranges
  (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) as `is_private` under
  Python's `ipaddress` module — real fixtures use genuinely public addresses.
- `open_allowlist_pr` is idempotent per rotation: it looks up an existing open
  PR for branch `automation/wan-ip-<new_ip>` before doing anything else, so a
  rotation that sits unmerged for a day does not open a second PR five minutes
  later.
