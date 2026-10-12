# From intent to running system

Seven infographics for engineering leadership and AI pathfinders, dated 2026-10-12. This companion to the ADP concept deck concerns agents changing the platform, rather than agents running on it.

The source explicitly permits the seven-panel sequence used here: original screens 02, 03, 06, 07, 09, 11 and 14. The illustrated notebook style matches the previous deck. Diagrams are explanatory artwork, not screenshots. The recorded comment is a supplied historical excerpt, not a newly executed triage.

## 01 — The bottleneck moved from writing to deciding

**Text alternative:** Human attention is the scarce resource. Four stat cards: 80% — 562 of 701 commits carry an agent co-author trailer, since July 2026, measured 2026-10-12. Zero GitHub reviews in 100 merged PRs, baseline 2026-09-30. 32% — 63 of 200 merged PRs followed by a fix within 72 hours, baseline 2026-09-30. One human owner: a solo approval gap. Two schematic lanes compare uniform attention with attention allocated among skip, skim and read. These lanes are illustrative, not measured proportions. Decide how much to read, and which lines. The follow-up-fix rate has not been re-measured after triage.

**Say:** When an agent writes most changes, the constraint shifts from typing to decisions and review attention. These are the baseline measurements that motivated the workflow. The owner cannot read every change equally, so the first pass should identify risk and point to the relevant lines.

**Interpret carefully:** An agent co-author trailer is provenance, not a measurement of how many lines the agent wrote. Zero GitHub reviews means zero recorded review events in that sample, not proof that the owner read nothing. A follow-up fix within 72 hours is a proxy, not a causal defect rate. The 100-PR review sample and 200-PR fix sample are distinct. 63/200 is 31.5%, rounded to 32%. No post-triage improvement is claimed.

**Source/status:** source brief screen 02 and Data provenance. Supplied historical measurements; no GitHub API refresh was performed for this artifact.

## 02 — One change, start to finish

**Text alternative:** Six stages, two human decision points. 1 Decide — HUMAN — intent to spec to plan. 2 Build — AGENT — written contract to branch to pull request. 3 Triage — MACHINE — skip, skim, read plus reasons. 4 Check — MACHINE — 22 validation jobs. 5 Merge & apply — HUMAN + MACHINE — human decides, machine applies. 6 Observe & record — MACHINE + AGENT — ledger, docs, memory and next PRs. The loop returns from stage 6 to stage 1. Infrastructure changes apply before merge. Lessons go where the next change reads them.

**Say:** The loop is the product. The human supplies intent and decides whether to release; the agent executes under a written repository contract; machines sort attention and run checks. Human authorization may be a dated, scoped grant for a named plan, rather than an individual button press on every PR. That does not give an agent standing permission to merge arbitrary work.

**Decide first:** The source describes a dated design document, then an implementation plan with checkpoints, then a ledger of constraints, research, invariants, tasks and bugs. The agent asks open questions before executing. The ledger carries decisions beyond a chat session.

**Build under a contract:** The coding agent changes the repository in a branch. Rules cover Git-based changes, infrastructure apply order, secrets and useful PR descriptions. Commit trailers and session provenance explain where a change came from. A Markdown rule is guidance; later checks provide enforcement.

**The docs hinge with the ADP deck:** The coding workflow produces service catalog metadata, an architecture page and a runbook. Validators check the contract, references and freshness. People read the rendered portal pages; in-cluster agents retrieve the same source. Agent-generated provenance and human verification are distinct. The brief describes a 180-day stale threshold and human-signed verification.

**Close the loop:** Record causes and fixes in the ledger, turn corrections into memory rules, calibrate triage against follow-up fixes, and open dependency PRs. The dependency bot does not auto-merge its proposals.

**Source/status:** brief screens 03–05, 08 and 10; repository instructions; `.github/workflows/validate.yaml`. The workflow currently defines 22 jobs, including its changed-file detection job. This count is not a promise that every job runs on every PR.

## 03 — Triage guides attention

**Text alternative:** Labels and explanations, no merge veto. MACHINE. Rules inspect paths, kinds and signals. Undecided cases reach a small classifier for skip, skim and read probabilities. Uncertain cases reach a frontier model. A decision from any stage produces a review:skip, review:skim or review:read label plus why, files and hunks, and next commands. Policy comes from the base commit. CI changes route to read. Failures fall back to read. Triage never blocks. Scope: ready, same-repository PRs; drafts wait and forks are skipped. The human still decides.

**Say:** This is a cascade, not three reviewers approving the same change. Rules settle clear cases cheaply. The classifier handles the remainder; the frontier model handles uncertainty. The policy lives in Git and is read at the PR's base commit, preventing the PR from weakening the policy used on itself.

**Reference examples:** Access-control, admission-policy, networking, storage, infrastructure and CI changes can force read. Documentation-only changes and narrowly defined image-tag bumps can skip when no stronger rule fires. Hot components and thresholds reflect this repository's historical follow-up fixes; they are not universal risk probabilities.

**Scope correction from local evidence:** The brief says every PR, but the installed workflow explicitly waits for drafts to become ready and skips fork PRs. The slide states that qualification. Fallback-to-read and the full model cascade are described in the supplied brief; the local workflow delegates implementation to a pinned action, whose remote code was not fetched for this artifact.

**Numeric omission:** The brief reports 46 read + 26 skim + 24 skip among the last 100 merged PRs. These sum to 96, and the remaining four are unexplained. The infographic does not chart these as a complete distribution or infer that the missing entries were unlabelled. The source also reports 30 dependency-bot PRs in that sample; it is not needed for the mechanism diagram.

**Sources/status:** brief screen 06; `.github/workflows/pr-triage.yaml` for events, scope and base SHA; `.github/review-policy.yaml` for rules, thresholds and component calibration. Local configuration plus supplied design description; no fresh runtime test.

## 04 — The wall of checks

**Text alternative:** MACHINE. 22 workflow jobs, four kinds of validation. Lint and schema: syntax, manifests, infrastructure. Contracts: docs, references, identity, tools. Generated-file drift: regenerate and compare. Real-system drills: boot a cluster, test policies, restore backups. Some jobs are conditional. Static tools missed policy behavior twice. Turn known failure modes into executable checks. Checks reduce risk; they do not catch everything.

**Say:** Cheap checks catch simple mistakes quickly. Contract checks catch missing or inconsistent knowledge and agent definitions. Regeneration catches drift in files that generators own. Real-system drills test behaviors that static tools cannot establish, such as what the API server admits or whether a backup can be restored.

**Recorded lesson:** The admission test harness documents that a schema checker skipped a policy kind and the policy CLI mis-evaluated mutating and parameterized admission policies. The harness therefore boots a real single-node cluster and uses server-side fixtures. A successful schema check alone would not have established the behavior.

**Counting:** 22 is the total number of top-level jobs under `jobs:` in the validation workflow at authoring time. It includes orchestration such as changed-file detection. The four rows are conceptual categories; no invented allocation of the 22 jobs across them is shown. Conditional skips are not failures, and a configured job is not necessarily a required branch-protection check.

**Failure retained from the longer briefing:** During the 2026-10-11 upgrade work, a backup script was found to record a truncated dump when transport dropped. The source says it was fixed, drilled and entered in the bug ledger. Later, a runtime event-format change left the audit blind for about 90 minutes; replaying a known gated call became a required operational check. These failures illustrate why green output needs behavioral evidence.

**Sources/status:** brief screens 07 and 12; `.github/workflows/validate.yaml`; `tests/admission-policies/conftest.py` opening documentation; repository build instructions. Configuration and supplied dated incident narrative, not tests run as part of infographic generation.

## 05 — Merge is the deployment

**Text alternative:** HUMAN + MACHINE. Apply order is part of change control. Infrastructure changes while the PR is open: Plan → Approve apply → Apply succeeds → before merge. Cluster changes after merge: Merge → Sync → Admit → Run. Human decision; reconcile main; policy checks; desired state. A policy failure branches to denial. Prune and self-heal remove deleted resources and correct drift. Solo-owner gap: required approval uses an admin override. Infrastructure first, merge next, reconcile the cluster.

**Say:** There are two different apply moments. Infrastructure changes are planned and applied while the PR is open; a successful apply precedes merge. After merge, the GitOps controller reconciles cluster manifests from main. Admission policy evaluates applicable resources at apply time and can reject unsafe state. Merge starts reconciliation; it is not a guarantee that every resource is instantly running.

**Human role and gap:** The brief reports rebase-only merges, no force-push, a required approval and a solo-owner admin override. This is not an independent second-person review. A company must decide who supplies that second principal and which checks become blocking. Repository rules were supplied as historical provenance and were not re-queried from GitHub.

**Operational lesson:** Merging infrastructure code first previously stranded changes. The infrastructure root explicitly documents waiting for the apply check to turn green before merging. Prune and self-heal are configured on the root application. The diagram is the general desired-state path; resource-specific exclusions and policy scope still apply.

**Sources/status:** brief screen 09; the active infrastructure root's `argocd.tf` header (root path anonymized); `base-apps/master-app.yaml`; repository instructions. Apply order and reconciliation settings corroborated locally; branch rules attributed to the supplied brief.

## 06 — A triage comment caught its own mistake

**Text alternative:** RECORDED — 2026-10-12. A version string in a document triggered the rule. The rule fired; the explainer identified a false positive; the human decided. The review:read label remained advisory. The explanation corrected the interpretation, not the label.

**Verbatim supplied transcript, trimmed in the original brief:**

```text
review:read
Why: always_read.signals k3s_version_change
What to look at:
- No k3s version change detected — The signal appears to be a false positive; this PR
  adds documentation and presentation files only, with no changes to infrastructure
  code, manifests, or Kubernetes configuration
- docs/presentations/README.md is the key file — Contains instructions for presenting
  the deck and explains the anonymization, file structure, and regeneration process
- Verify anonymization in presentation files — The large PDF and markdown briefs
  should contain no hostnames, repo names, or personal identifiers
Signals: decided_by: rules · size: large (1082 lines)
```

**Say:** A rule inspecting changed text saw a cluster-version signal in documentation and forced read without consulting the classifier. The model that explained the decision noticed that there was no infrastructure change. It explained the false positive but did not change the rule's label. Advisory triage let the owner use that explanation without treating a bad match as a merge block.

**Editorial choice:** The image shows the label, Why line, first explanatory bullet and Signals line. The two other bullets remain verbatim here. The Why line is annotated rather than struck out, to avoid implying that the rule result was replaced. No policy fix was applied while creating this deck, and the quoted signal remains in the local policy.

**Sources/status:** source brief screen 11 and Data provenance, dated 2026-10-12; `.github/review-policy.yaml` confirms the named signal exists. The historical comment was supplied, not independently fetched.

## 07 — Reuse the mechanisms. Decide the ownership.

**Text alternative:** What maps to a company, and what does not yet. Maps directly: risk policy in Git, advisory triage, docs contracts and provenance, generated-file checks, real-system drills, agent commit provenance. Needs a decision: risk thresholds, who reads a read PR, blocking checks, agent merge authority. Gaps: solo-owner admin override, calibration from one repository, reviewer tools are not a service. A company needs a second principal. Start here: one repository → rules only → one month of labels → calibrate → add classifier. Decide the operating model before scaling agent-written changes.

**Say:** Reuse the mechanisms while making ownership explicit. Begin with rules and observe labels for a month. Calibrate against the new repository's history before adding a classifier. Choose a risk-policy owner, a reviewer rota or team, required checks and a dated scope for any agent merge authority. The reference is a single-owner experience, not evidence of a mature multi-team operating model.

**Discussion prompts retained from the full brief:**

1. Where do design decisions live, and could an agent read them before starting?
2. If agents opened most PRs, what would decide which ones a human reads?
3. Which review comments could become checks, and which checks need a real system?
4. What docs contract would work for one service, and who signs human verification?
5. Who owns the risk policy, and under what written grant may an agent merge?

**Close:** The agent writes the change; the workflow directs review attention. A live reference demo could show triage, a failed check and a blocked apply. No demo, deployment, PR, comment or merge was performed to prepare these visuals.

**Source/status:** brief screens 14–15. Proposed company adoption path and acknowledged reference limitations, not a measured outcome.

## Evidence and delivery notes

- The source is `docs/presentations/dev-workflow-presentation-brief.md`, including its Data provenance and Do not claim sections. It contains the complete 15-screen narrative; this deliverable follows its seven-screen alternative.
- All numbers have dates or local counting scope. The pre-triage follow-up-fix baseline must not be represented as an improvement caused by triage.
- Unneeded ledger/spec/plan counts and the incomplete triage distribution are omitted. No unsupported sample allocation or outcome metric was added.
- Agent workflow diagrams do not grant an agent permission to merge. Any such permission remains explicit, dated and scoped to the owner's chosen plan.
- No claim of an active local judge hook, independent second reviewer, transferable classifier calibration or comprehensive check coverage is made.
- No names, hostnames, company names or repository names appear in the images. Technical tool names and generic relative source paths are retained where useful.
- The slides were generated as images from the brief (one prompt per slide) and assembled into `dev-workflow-infographics.pdf`, seven 16:9 pages. The generation prompts and per-page checksums stayed with the generation output outside this repo.
- The PDF has raster image pages, not selectable text or tagged accessibility. These notes supply text alternatives and reusable narrative. The PNG ZIP contains all seven images plus notes, prompts and verification metadata.
