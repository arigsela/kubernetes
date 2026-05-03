# Golden POC — Phase 4: Demo Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre-bake the Cluster Health agent for >=1 week so Langfuse accumulates real history; rehearse the 30-minute demo narrative end to end; record a backup video; assemble a demo-day operational checklist; capture cleanup items deferred from earlier phases.

**Architecture:** No new code. This phase is operational rigor. Outputs are documents, scripts, and recordings — no new ArgoCD applications.

**Tech Stack:** None new. Uses what's already running.

**Reference design:** `docs/superpowers/specs/2026-05-02-golden-ai-platform-poc-design.md` Section 2 (Demo narrative), Section 9 (Cuttable scope), Section 10 (Risks).

**Dependencies:** Phases 0, 1, 2, 3 complete and verified.

---

## Task 4.1: Pre-bake Cluster Health Agent (start as early as possible)

**Files:**
- Create: `scripts/heartbeat-cluster-health.sh`
- Create: `base-apps/cronjobs/cluster-health-heartbeat.yaml`

The demo's first beat shows Langfuse with real historical traces. We need at least a week of invocations accumulated by demo day.

- [ ] **Step 1: Write a heartbeat script that exercises the agent**

`scripts/heartbeat-cluster-health.sh`:
```bash
#!/usr/bin/env bash
# Invokes cluster-health agent with rotating inputs so Langfuse accumulates
# realistic-looking historical traces. Designed to run as a Kubernetes CronJob
# every 15 minutes.
set -euo pipefail

AGENT_URL="${AGENT_URL:-http://cluster-health.agents.svc.cluster.local/v1/messages}"

# Rotate the input each invocation so traces show varied prompts.
NAMESPACES=(agents kagent agentregistry langfuse argo-cd default monitoring vault)
NS="${NAMESPACES[$(( RANDOM % ${#NAMESPACES[@]} ))]}"

PAYLOAD=$(cat <<EOF
{"messages":[{"role":"user","content":"Status of namespace ${NS}?"}]}
EOF
)

curl -fsS -X POST "$AGENT_URL" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  -o /dev/null -w "%{http_code} ${NS} %{time_total}s\n"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/heartbeat-cluster-health.sh
```

- [ ] **Step 3: Wrap as a CronJob**

`base-apps/cronjobs/cluster-health-heartbeat.yaml`:
```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: cluster-health-heartbeat
  namespace: agents
  labels:
    app.kubernetes.io/name: cluster-health-heartbeat
    app.kubernetes.io/component: demo-prebake
spec:
  schedule: "*/15 * * * *"
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 60
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: heartbeat
              image: curlimages/curl:8.10.1
              env:
                - name: AGENT_URL
                  value: "http://cluster-health.agents.svc.cluster.local/v1/messages"
              command: ["/bin/sh", "-c"]
              args:
                - |
                  NAMESPACES="agents kagent agentregistry langfuse argo-cd default monitoring vault"
                  arr=($NAMESPACES)
                  NS=${arr[$(($RANDOM % ${#arr[@]}))]}
                  curl -fsS -X POST "$AGENT_URL" \
                    -H "Content-Type: application/json" \
                    -d "{\"messages\":[{\"role\":\"user\",\"content\":\"Status of namespace ${NS}?\"}]}" \
                    -o /dev/null -w "%{http_code} ${NS} %{time_total}s\n"
              resources:
                requests: {cpu: 10m, memory: 16Mi}
                limits:   {cpu: 100m, memory: 64Mi}
```

- [ ] **Step 4: Add to the agents directory and commit**

```bash
mkdir -p base-apps/cronjobs
mv base-apps/cronjobs/cluster-health-heartbeat.yaml base-apps/agents/cluster-health/heartbeat-cronjob.yaml
git add base-apps/agents/cluster-health/heartbeat-cronjob.yaml scripts/heartbeat-cluster-health.sh
git commit -m "feat(demo-prebake): hourly heartbeat against cluster-health for Langfuse history (Phase 4)"
```

**Important:** start this CronJob ASAP — at least 7 days before demo day. ~96 invocations/day; should yield ~700 historical traces in a week, which looks credible during the demo.

- [ ] **Step 5: Verify**

After ~30 minutes:
```bash
kubectl get jobs -n agents -l app.kubernetes.io/name=cluster-health-heartbeat
# Expected: 2 completed jobs
```

Open Langfuse → project `golden-poc` → Traces. Trace count should be growing.

---

## Task 4.2: Pre-stage the deliberately-risky PR for the live demo

**Files:**
- Create: `scripts/demo/queued-pr-changes.patch`
- Create: `scripts/demo/open-demo-pr.sh`

The demo's "watch the PR Review Agent fire" beat needs a real PR. Don't make it on stage — pre-stage the changes so a single command opens the PR with the risky content.

- [ ] **Step 1: Capture the demo's risky changes as a diff**

The risky changes (per design Section 7):
- A Deployment with `replicas: 1` (down from 2)
- Removed `livenessProbe` block

Pick a real Deployment in the repo to modify. Suggestion: a non-critical sample app like `weather-kitchen-backend.yaml`.

`scripts/demo/queued-pr-changes.patch` — capture by running locally:
```bash
# In the worktree, manually edit the chosen file to add the risks.
# Then capture:
git diff > scripts/demo/queued-pr-changes.patch
git checkout -- .  # discard the local edits; the patch is the source of truth
```

The patch contents will look something like:
```diff
--- a/base-apps/weather-kitchen-backend/deployments.yaml
+++ b/base-apps/weather-kitchen-backend/deployments.yaml
@@ -10,7 +10,7 @@ spec:
-  replicas: 2
+  replicas: 1
@@ -28,11 +28,6 @@ spec:
-          livenessProbe:
-            httpGet:
-              path: /healthz
-              port: 8080
-            initialDelaySeconds: 30
-            periodSeconds: 30
```

Commit the patch file (NOT the live edits):
```bash
git add scripts/demo/queued-pr-changes.patch
git commit -m "feat(demo): pre-staged PR diff with intentional risks for PR Review Agent demo (Phase 4)"
```

- [ ] **Step 2: Write the script that opens the demo PR on demand**

`scripts/demo/open-demo-pr.sh`:
```bash
#!/usr/bin/env bash
# Opens the demo PR. Run this during the live demo to fire the PR Review Agent.
set -euo pipefail

cd "$(dirname "$0")/../.."
BRANCH="demo/pr-review-$(date +%Y%m%d-%H%M)"

git checkout main
git pull origin main
git checkout -b "$BRANCH"
git apply scripts/demo/queued-pr-changes.patch
git add -u
git commit -m "demo: scale weather-kitchen-backend down + remove livenessProbe (intentional)"
git push -u origin "$BRANCH"

gh pr create \
  --base main \
  --head "$BRANCH" \
  --title "[DEMO] scale down + remove probe" \
  --body "Demo PR. The PR Review Agent should comment with severity-tagged inline review."
```

```bash
chmod +x scripts/demo/open-demo-pr.sh
git add scripts/demo/open-demo-pr.sh
git commit -m "feat(demo): one-shot script to open the demo PR (Phase 4)"
```

---

## Task 4.3: Rehearsal script — the 30-minute demo narrative

**Files:**
- Create: `docs/golden-demo-rehearsal.md`

Write the demo as an annotated walk-through with timing. Rehearse twice: once solo, once with someone else watching.

- [ ] **Step 1: Write the rehearsal doc**

`docs/golden-demo-rehearsal.md`:
````markdown
# Golden POC Demo Rehearsal — 30 minutes

**Audience:** Mixed exec + engineering leadership at Golden.
**Goal:** Land the "engineer journey" — Backstage form to running agent in minutes.
**Tone:** Confident, technically grounded. Treat the audience as smart adults.

## Pre-flight (T-1 hour)

- [ ] Cluster reachable from demo laptop. VPN connected and tested.
- [ ] All ArgoCD apps healthy (`argocd app list`). Anything red — fix or excuse from demo.
- [ ] `gh` CLI authenticated. `kubectl` context correct.
- [ ] Browser tabs prepared (in this order):
  1. Backstage catalog filtered to `Type: agent`
  2. Backstage `cluster-health-agent` page (Try-it card visible)
  3. Langfuse project `golden-poc` Traces tab
  4. Backstage `/skills` page
  5. GitHub repo `arigsela/kubernetes` (PRs tab)
- [ ] Slack workspace open in a separate window with a #demo channel; bot invited.
- [ ] Recording started in the background (Task 4.4 — backup).
- [ ] Demo PR script in a terminal (not opened — only run if live build succeeds).

## The walkthrough

### Beat 1 — "Here's the platform" (T+0:00, 3 min)

Open Backstage catalog (Tab 1). Two agents visible: cluster-health and pr-review.

Talk track:
> "This is our internal AI platform. Two agents are live today. Both follow the same shape: they're declared as YAML, shipped through ArgoCD, run on kagent. Engineers don't write Kubernetes — they fill out a form."

### Beat 2 — "Let's use one" (T+3:00, 4 min)

Switch to Tab 2 (cluster-health page). Point out:
- Status (running)
- Recent traces (the heartbeat-driven history)
- Try-it card

Click the Recent Traces section, click into a trace. Switches to Langfuse (Tab 3). Show one trace with full LLM call detail.

Back to Backstage. Type into the Try-it box: `What's the status of the agentregistry namespace?`. Click Send. Response appears (~10s).

Talk track:
> "Same agent reachable in Slack, in Backstage, and via curl. We built this *once* and made it usable everywhere people already work."

### Beat 3 — "Let's build a new one" (T+7:00, 8 min)

Click "Create" → "Create New Agent".

Fill the form for a real-feeling new agent. Suggested:
- Name: `release-notes`
- Description: "Drafts release notes from merged PRs in the last week"
- System prompt: (have this pre-typed in your notes; paste it)
- Skills: github-mcp (paste OCI ref from Tab 4)
- Surface: HTTP only
- (Skip the slackCommand / githubRepo fields for HTTP surface)

Click Create. Backstage opens the PR.

> "That's the entire engineer experience for a new agent. Twenty lines of YAML — none of it Kubernetes."

Switch to GitHub (Tab 5). Walk through the PR's two files. Highlight that there's no Deployment, no Service, no Ingress, no Secret — Crossplane handles all of that.

Merge the PR.

Switch to a terminal, watch ArgoCD reconcile and Crossplane render:
```bash
watch -n 2 'kubectl get xagent release-notes -n agents -o jsonpath="{.status.conditions[*].type}={.status.conditions[*].status}"'
```

After ~90 seconds, the agent is Synced=True, Ready=True. Back to Backstage Catalog — refresh — the new agent is listed.

Click into the agent. Try-it card already works. Type "Draft notes for the last week of merged PRs." Send. Response appears.

> "Three minutes ago this didn't exist. Now it's a first-class agent — observable, reusable, owned by a team."

### Beat 4 — "Live, on a real PR" (T+15:00, 8 min)

Switch to Tab 5 (GitHub PRs). Open a terminal. Run:
```bash
./scripts/demo/open-demo-pr.sh
```

The PR opens. Refresh the GitHub tab; PR is visible. Wait ~15s.

Inline review comments appear on the affected lines, severity-tagged.

> "The PR Review Agent uses the same platform. New surface — GitHub webhooks instead of Slack — but same XAgent CR shape, same Composition, same observability."

Click into one of the comments. Switch to Langfuse (Tab 3). Find the trace for this PR review (filter by `pr-review` tag). Show the full agent reasoning.

### Beat 5 — "Where the next agent's skills come from" (T+23:00, 4 min)

Switch to Tab 4 (`/skills` page). Show the four skills.

Talk track:
> "agentregistry — the OCI-backed registry that ships skills as artifacts. Three of these came from upstream open source. One we built ourselves. Engineers browse this, copy a ref, paste it into the form. Same supply chain as containers."

Click into k8s-yaml-lint. Show the version, the OCI ref, the description.

### Beat 6 — Close (T+27:00, 3 min)

> "Every piece of what we just saw runs on commodity infra: ArgoCD, Crossplane, Vault, kagent, agentgateway, agentregistry, Langfuse. Nothing bespoke at the layer that matters. The differentiator is the *opinionated assembly* — the XAgent abstraction, the Backstage template, the per-agent observability defaults."
>
> "This is what 'AI platform' means in practice. Not chatbots — infrastructure."

Pause for questions.

## Fallbacks (use if something breaks)

| What broke | Fallback |
|---|---|
| Slack adapter down during Beat 2 | Use the Try-it card instead of Slack |
| ArgoCD sync slow during Beat 3 | Show a previously-created agent; pre-record this beat as backup |
| Webhook to homelab failing during Beat 4 | Skip the live build; show recorded video of a prior PR Review Agent firing |
| Langfuse UI down | `kubectl logs` showing the trace export, plus a screenshot of a prior trace |
| Anthropic API down | Skip Beat 4; extend Beat 5 with deeper agentregistry walkthrough |
| Cluster down entirely | Play the full backup video from Task 4.4 |

## Rehearsal log

- Rehearsal 1 (date): completed in __ min. Issues: __. Fixes: __.
- Rehearsal 2 (date): completed in __ min. Issues: __. Fixes: __.
````

- [ ] **Step 2: Commit**

```bash
git add docs/golden-demo-rehearsal.md
git commit -m "docs(demo): 30-minute rehearsal script + fallbacks (Phase 4)"
```

- [ ] **Step 3: Run the rehearsal twice**

The first rehearsal uncovers timing and prop issues. The second confirms the fixes. Update the doc's "Rehearsal log" section after each run.

---

## Task 4.4: Record backup video

**Files:**
- Create: `docs/golden-demo-recording-checklist.md`

The backup video runs ~30 minutes; if the live demo fails irrecoverably, switch to it.

- [ ] **Step 1: Pick a screen recorder**

macOS: QuickTime (built-in), or OBS Studio for higher quality.
Linux: SimpleScreenRecorder, OBS.

Settings: 1920x1080 minimum, 30fps, system audio + microphone.

- [ ] **Step 2: Record the rehearsed walkthrough**

Run through the rehearsal script once, recording start to finish. Don't pause; if you mess up a beat, keep going — it's the backup, not the polished cut.

- [ ] **Step 3: Trim and export**

Trim the start (pre-recording fumbling) and end. Export as MP4 H.264, no compression artifacts.

Filename: `golden-poc-demo-backup-YYYY-MM-DD.mp4`. Keep on the demo laptop AND a USB stick AND uploaded somewhere private (e.g., a private Drive folder).

- [ ] **Step 4: Document the backup playback path**

`docs/golden-demo-recording-checklist.md`:
````markdown
# Demo Recording — Locations + Playback

- **Primary:** demo laptop, `~/Movies/golden-poc-demo-backup-YYYY-MM-DD.mp4`
- **Backup 1:** USB stick (label: "GOLDEN POC DEMO")
- **Backup 2:** [private link]

## Playback fallback path

If the live demo fails irrecoverably:
1. Pause, acknowledge the issue ("homelab connectivity is being uncooperative — let me show you the recording instead").
2. Open the backup video. Play full screen.
3. Pause at natural breakpoints to handle questions.

## Don't

- Don't pretend the recording is live.
- Don't apologize repeatedly. One acknowledgment, then continue confidently.
- Don't rely on the recording so early in the demo that the audience never sees the live system. If you can land Beat 1 + Beat 2 live, the credibility is established — the recording covers Beats 3-6 if needed.
````

```bash
git add docs/golden-demo-recording-checklist.md
git commit -m "docs(demo): backup video recording + playback checklist (Phase 4)"
```

(The video file itself is NOT committed — too large. Stays on the laptop / USB / private storage.)

---

## Task 4.5: Demo-day operational checklist

**Files:**
- Create: `docs/golden-demo-day-checklist.md`

- [ ] **Step 1: Write the checklist**

`docs/golden-demo-day-checklist.md`:
````markdown
# Demo Day Checklist

## T-2 hours

- [ ] Eat lunch / coffee. Don't demo hungry or jittery.
- [ ] Review rehearsal doc one more time.

## T-1 hour

- [ ] VPN connected to homelab. Test:
  ```bash
  kubectl get nodes
  argocd app list | head
  ```
- [ ] All ArgoCD apps Synced + Healthy.
- [ ] Backup video on demo laptop, opened in a media player ready to fullscreen.
- [ ] Slack open, bot invited to #demo channel.
- [ ] Browser tabs in correct order (per rehearsal doc Pre-flight).
- [ ] Terminal windows ready:
  - One for `watch` commands during Beat 3
  - One for `./scripts/demo/open-demo-pr.sh` during Beat 4

## T-15 min

- [ ] Smoke test: invoke cluster-health from Slack. Confirm response.
- [ ] Smoke test: open the Try-it card in Backstage. Send a test message. Confirm response.
- [ ] Smoke test: open Langfuse, confirm recent traces visible.
- [ ] Notifications off (Slack, Mail, system).
- [ ] Charger plugged in.
- [ ] Phone on silent.

## T-0

- [ ] Start screen recording (in case audience asks for the recording later).
- [ ] Begin Beat 1.

## Post-demo

- [ ] Stop recording. Save with date.
- [ ] Note questions asked + your answers.
- [ ] Note anything that broke + the fallback used.
- [ ] Send a thank-you note within 24 hours.

## DO NOT

- Apologize for the platform's youth or feature gaps.
- Promise specific features without checking with the team first.
- Compare unfavorably to anything Golden currently uses.
- Show the system prompt of an agent in detail unless asked.

## DO

- Pause after each beat. Take a breath. Look up.
- Invite questions during natural breakpoints.
- If a question is hard, "let me come back to that" is fine.
````

```bash
git add docs/golden-demo-day-checklist.md
git commit -m "docs(demo): demo-day operational checklist (Phase 4)"
```

---

## Task 4.6: Cleanup items deferred from earlier phases

**Files:**
- Modify: `base-apps/crossplane-compositions/composition-agent.yaml` (Composition cleanup)

Phase 2 and Phase 3 left a few items marked for cleanup. Address them now.

- [ ] **Step 1: Drop unused per-agent slack-token ExternalSecret**

Phase 2 Task 2.3 noted: the per-agent `slack_bot_token` Secret rendered for `surface: slack` is unused (the slack-adapter holds the token). Either remove it or repurpose.

For Phase 4, **remove it** — clean Composition output is worth the small refactor.

In `base-apps/crossplane-compositions/composition-agent.yaml`, modify `make_external_secret_for_surface()`:

```python
          def make_external_secret_for_surface(agent_name, namespace, surface, annotations):
              # Slack: the slack-adapter holds the bot token; agents don't need
              # per-agent secrets for slack surface.
              if surface == "github-webhook":
                  # ... (unchanged)
                  return { ... }
              return None
```

After commit + ArgoCD sync, the existing `cluster-health-slack` ExternalSecret is pruned by Crossplane.

- [ ] **Step 2: Move PR Review's GitHub creds out of per-agent Vault path**

Phase 2 Task 2.7 noted: per-agent ExternalSecret reads from `k8s-secrets/agents/<name>` for `github-webhook` surface, but creds are duplicated from the adapter's path. Cleaner: have the Composition reference the shared `k8s-secrets/github-webhook-adapter` path instead, since GitHub App credentials are platform-shared, not per-agent.

In `make_external_secret_for_surface()`, change `github-webhook` branch's `remoteRef.key` from `f"agents/{agent_name}"` to `"github-webhook-adapter"`.

Then delete the duplicate Vault entry:
```bash
vault kv delete k8s-secrets/agents/pr-review
```

- [ ] **Step 3: Test the Composition still works**

```bash
crossplane render \
  /tmp/xagent-render/xr.yaml \
  base-apps/crossplane-compositions/composition-agent.yaml \
  /tmp/xagent-render/functions.yaml | head -100
```

Verify rendered output excludes the slack ExternalSecret and includes the github-webhook ExternalSecret pointing at the shared Vault path.

- [ ] **Step 4: Commit**

```bash
git add base-apps/crossplane-compositions/composition-agent.yaml
git commit -m "refactor(composition-agent): drop unused per-agent slack secret; share GH App creds (Phase 4 cleanup)"
```

---

## Task 4.7: Final acceptance + handoff to demo day

**Files:**
- Modify: `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md`

- [ ] **Step 1: Final pre-demo verification**

Run through the full demo rehearsal one more time. All beats land cleanly.

- [ ] **Step 2: Append final status**

```markdown
## Phase 4 — Status

- [x] Cluster Health heartbeat CronJob running every 15min — Langfuse has [N] historical traces.
- [x] Demo PR script tested.
- [x] Rehearsal completed twice. Time: __ min. Notes: __.
- [x] Backup video recorded and stored on demo laptop + USB + private storage.
- [x] Demo-day checklist documented.
- [x] Composition cleanup applied (slack secret dropped; GH creds shared path).

**POC complete. Ready for Golden demo on [date].**
```

- [ ] **Step 3: Final commit**

```bash
git add docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md
git commit -m "docs(golden-poc): Phase 4 complete — POC ready for Golden demo"
```

POC complete. Demo it.
