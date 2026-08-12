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
  - base-apps/n8n/workflows-configmap.yaml
  - tests/wan_ip
---

# wan-ip-monitor

## What it is
A CronJob (`0 */12 * * *`) that keeps the home WAN address's two dependents in
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

## Two independent halves
`main()` does exactly two things per run, in this order, and **neither is a
precondition for the other**:

1. **Route 53** (`reconcile_route53`) — list the zone; for every hostname in
   `MANAGED_HOSTNAMES` whose single-value A record is not already the address
   `detect_wan_ip()` just returned, UPSERT it to that address. This half never
   touches GitHub: not the annotation, not the PR, not the token.
2. **The allow-list** (`reconcile_allowlist`) — read the policy from `main`,
   compare its `arigsela.com/wan-ip` annotation to the same detected address,
   and open (or find) a PR if they differ.

Then, only if records actually moved *or* a PR was newly created, notify.

**Why the independence is load-bearing.** An earlier version keyed both halves
off `declared == current` and returned early when they matched — making the
annotation both the change signal *and* the selector for which Route 53
records to move. Those two things move on completely different clocks: DNS
moves in seconds, the annotation moves only when a human merges the PR. Once
they diverge the code cannot notice. The proven failure: WAN goes A→B (records
move to B, PR opened), then flaps back to A before the merge. Now
`detected == declared == A`, the early return fires, the job prints `in sync,
nothing to do` — and all 21 records stay stranded on B, permanently and
silently, because every subsequent run draws the same conclusion.
`tests/wan_ip/test_main.py::test_dns_follows_a_flap_back_to_the_previous_address`
drives that exact three-run sequence; it needs multiple runs to express, which
is why every single-shot `main()` test missed it.

The second consequence of the independence: a GitHub outage or an expired
`GITHUB_TOKEN` can no longer prevent the automatic half from running. Step 1
completes first; if step 2 then raises, the job prints an explicit "route53
was reconciled onto X, but the allow-list PR could NOT be opened" line before
re-raising, because those two failures need very different responses and a
bare traceback does not distinguish them.

Both halves are still reconcilers, not change-detectors — nothing is persisted
between runs, and the steady state (two runs a day) re-derives the same
conclusion each time. Between rotation and merge, `open_allowlist_pr`'s
existing-PR lookup finds the same PR rather than opening a new one.

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
  — the code never calls a merge endpoint.

  **What actually stops a self-merge is a GitHub branch ruleset on `main`, not
  the token's scope.** Do not read the token scope as the control: the pair
  `contents:write` + `pull_requests:write` is exactly what GitHub's `PUT
  /pulls/{n}/merge` requires, and `contents:write` alone is sufficient to push
  straight to `main` via `PUT /contents/{path}` with `"branch": "main"`. If
  the *code* changed to call either of those, the token would let it. None of
  this changes what the code actually does today — `open_allowlist_pr` never
  calls a merge endpoint, so the automation cannot and does not merge
  anything right now. The rest of this paragraph is about what the credential
  could theoretically permit if that ever changed, and separates what is
  verified from what is not.

  **Verified:** the ruleset on `main` is active and requires
  `required_approving_review_count: 1` with `require_last_push_approval:
  true`, so the identity that pushed a commit cannot supply the approval its
  own PR needs to merge. That control is real and stands. **Also verified —
  and not fully closed:** the ruleset carries a bypass actor (the Admin
  repository role, `bypass_mode: "pull_request"`), and the GitHub account
  that mints `GITHUB_TOKEN` holds admin on this repo. **Not verified:**
  whether a fine-grained PAT lacking the `administration` permission still
  inherits that role-based bypass at merge time — GitHub does not document
  this either way, so it is not asserted as closed here. If this needs to be
  closed deterministically rather than resting on undocumented behavior, the
  operator's choices are: remove the Admin bypass actor from the ruleset, or
  mint `GITHUB_TOKEN` on a machine account that does not hold admin — neither
  has been done as of this writing. **The reconciler must never be able to
  merge its own PR; today it structurally cannot, because the code never
  calls a merge endpoint.**

Until the PR merges, DNS is already correct but the allow-list still trusts
the old address, so the protected hosts resolve and return `403` — this is
expected, not a bug (see runbook: "PR opened but nothing merged").

## Which Route 53 records it owns
An **explicit list**, in `cronjob.yaml`'s `MANAGED_HOSTNAMES` env var
(comma-separated; trailing dots and case are normalised on both sides before
comparing, so `Argocd.arigsela.com.` and `argocd.arigsela.com` are the same
entry). Seeded with the 21 A records in zone `Z0524483LR4JCFNLS7N0` that point
at the WAN address:

`agent`, `argo-workflows`, `argocd`, `atlantis`, `backstage`, `chores`,
`coroot`, `dex`, `grafana`, `home`, `kagent-mcp`, `kagent`, `langflow`, `n8n`,
`oncall-crewai`, `oncall`, `overseerr`, `rollouts`, `vault`,
`weather-kitchen`, `whoami` — each `<name>.arigsela.com`.

**Adding a host to the homelab means adding it here.** A hostname absent from
the list is simply never touched: after a rotation it keeps pointing at the
old address and stops resolving to home. That is the deliberate failure mode —
visible and self-inflicted, rather than the job guessing at records it does
not own. `tests/wan_ip/test_route53.py::test_cronjob_manages_every_hostname_the_allow_list_protects`
fails CI if a host gains an entry in the Istio allow-list without gaining one
here.

**Why a list and not "whatever currently points at the old address".** The only
value the job can read back as "the old address" is the annotation, which lags
by a merge — so a value-based selector stops matching the moment DNS and the
annotation diverge, and stops correcting anything (see "Two independent
halves"). A name is knowable without asking anything that lags.

Two hard safety rules survive from the original selector and are what keep a
wrong answer from being destructive — `records_needing_update` skips anything
that is **not type `A`**, and anything whose value list does not have
**exactly one entry**. The single-value rule covers two distinct hazards: a
multi-value record is not ours to rewrite from a single-address signal, and an
**alias** record (`AliasTarget`, no `ResourceRecords` at all) would otherwise
read as "zero values, therefore not current" and get flattened into a plain A
record.

## The `arigsela.com/wan-ip` annotation contract
`base-apps/istio-ingress/authorizationpolicy.yaml`'s `metadata.annotations`
carries:
```yaml
arigsela.com/wan-ip: "76.97.4.210"
```
This is the **single source of truth** for which of the several `/32`s
allow-listed in that file is the home WAN address (the file also allow-lists
three other, unrelated, non-rotating remote addresses per rule).
`read_declared_wan_ip` reads it, and `reconcile_allowlist` compares it to the
detected address. **That is its only job.** It is deliberately *not* consulted
by the Route 53 half — it is a value that lags reality by however long the PR
sits unmerged, so anything that has to be correct within seconds cannot be
derived from it (see "Two independent halves"). `rewrite_policy`
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
the few seconds of install time are immaterial twice a day, and
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
- **Records it owns**: `cronjob.yaml`'s `MANAGED_HOSTNAMES` env var (see
  "Which Route 53 records it owns").
- **n8n notification**: best-effort POST to
  `http://n8n.n8n.svc.cluster.local:5678/webhook/wan-ip-rotated`; a failed
  notification never fails the run (`notify` swallows and logs). **That
  webhook is defined in `base-apps/n8n/workflows-configmap.yaml`** as the
  `wan-ip-rotated.json` workflow (id `wan-ip-rotated`), imported and activated
  by the n8n Deployment's `import-workflows` initContainer. It previously did
  not exist anywhere — n8n 404'd every POST and `notify()` swallowed it, so no
  notification had ever actually been delivered. Editing or adding a workflow
  there requires bumping `checksum/workflows` in
  `base-apps/n8n/deployments.yaml`, otherwise the pod does not roll and n8n
  never registers the route.

## What gets notified
`notify()` fires on exactly two occasions, and never in the steady state:

- **`"event": "wan-ip-reconciled"`** — a rotation was actually acted on:
  records moved, or a PR was newly created (`records_updated`, `new`,
  `pr_url`). It deliberately does *not* fire on the runs between the rotation
  and the merge, when DNS is already fixed and the PR is already open —
  repeating that alert every run would train the operator to ignore it. The
  guard was written when the schedule was `*/5` and 288 such alerts a day was
  the concrete risk; it matters less at the current twice-daily cadence, but it
  is what makes the cadence a free choice rather than one constrained by how
  noisy the alerting gets.
- **`"event": "wan-ip-monitor-failed"`** — `reconcile()` raised. Carries
  `error_type` and `error`. `main()` wraps the whole body for this reason:
  without it, the only failure signal is a non-zero pod exit in a namespace
  nobody watches, and a broken run stays invisible until a protected host
  starts timing out. The original exception is always re-raised (so the pod's
  exit status stays honest), and `notify()` is best-effort by construction, so
  an n8n that is *also* down cannot mask the real cause.

## DRY_RUN
Ships with `DRY_RUN: "true"` on the CronJob deliberately. **`DRY_RUN` only
gates the write path — it does not gate the reads.** Every run, dry or not,
steady-state or not, performs both:

- a live Route 53 `list-resource-record-sets` on the zone
  (`reconcile_route53` always lists — that is what makes it a reconciler), and
- a live GitHub `contents` read of the policy on `main`
  (`read_policy_from_main`).

Only the writes are skipped when `DRY_RUN` is true:
`change-resource-record-sets` and `open_allowlist_pr`. Instead the job logs
what it *would* have done, naming the specific records:
`DRY_RUN: would move 21 Route 53 record(s) to <ip>: ...`.

Because both reads now happen on **every** run, a steady-state dry run
exercises **both** credentials — unlike the previous design, where the Route 53
call only happened if a rotation was already detected, so a clean steady-state
dry run proved nothing about AWS. A dry run that gets to
`route53: 21 managed hostname(s) already on <ip>` has proven the AWS half, and
one that gets to `allow-list: declared=... detected=...` has proven the GitHub
half.

Arming it (`DRY_RUN: "false"`) is a separate, deliberately reviewable commit,
not part of the initial deployment (see runbook: "Disabling it" for the
reverse operation).

## Known limitations (deferred, not part of this task)
- **`pip install` at container start is version-pinned but not hash-pinned.**
  `boto3==1.35.99` and `pyyaml==6.0.2` are exact versions, but nothing stops
  PyPI (or a MITM on the way to it) from serving a different artifact for the
  same version string. Hash-pinning (`--require-hashes` with a lockfile) or
  moving to a baked, digest-pinned custom image would close this gap. Neither
  is done here — deliberately out of scope for this task; both are real,
  tracked follow-ups.
- **No custom/baked image.** Installing dependencies at every run start
  (see runbook: "PyPI unreachable") trades a small, recurring startup cost and
  a runtime dependency on PyPI's availability for not having to build,
  publish, and patch a bespoke image. A baked image would remove both the
  PyPI dependency and open the door to hash-pinning in one move — also
  deferred.

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
- **`is_valid_public_ipv4` is `addr.is_global and not addr.is_multicast`, not
  an enumeration of `is_private`/`is_loopback`/`is_reserved`/…** The
  enumeration it replaced silently accepted RFC 6598 CGNAT space
  (`100.64.0.0/10`): `IPv4Address("100.64.1.5").is_private` is `False`, and so
  is every other flag in that list. CGNAT is a live possibility on this
  residential line (see runbook, "Detected address flapping"), and
  allow-listing a carrier-NAT address would hand the security boundary to
  every other subscriber behind that NAT. The explicit `not is_multicast` is
  **not** redundant — CPython does not count `224.0.0.0/4` among its private
  networks, so a bare `is_global` returns `True` for `224.0.0.1`; removing that
  clause re-admits multicast, and
  `test_still_rejects_multicast_which_is_global_does_not_cover` exists to make
  that a CI failure.
- `open_allowlist_pr` is idempotent per rotation: it looks up an existing open
  PR for branch `automation/wan-ip-<new_ip>` before doing anything else, so a
  rotation that sits unmerged for a day does not open a second PR five minutes
  later.
- **`open_allowlist_pr` reads the file's blob SHA from the *branch* when the
  branch already exists**, not from `main`. `PUT /contents` needs the SHA of
  the blob it replaces on the target branch; committing `main`'s SHA onto a
  branch that already carries its own commit to that path is a **409
  Conflict** — and because that leaves the PR uncreated, the existing-PR
  short-circuit never engages, so the next run repeats it, forever, with no PR
  ever opened. Two real ways to land there: a partial failure (branch and
  commit succeed, `POST /pulls` fails) and a closed-but-not-merged PR whose
  branch survives until the ISP hands the same address back. If the branch
  already carries exactly this rotation, there is nothing to commit and the
  code goes straight to opening the PR.
