# Newsletter Digest → n8n Email Delivery — Implementation Plan

**Source PRD:** `docs/plans/newsletter-digest-n8n-prd.md`
**Owner:** Ari (DevOps Manager)
**Status:** Approved — ready for implementation
**Last updated:** 2026-04-22

## Overview

Replace the manual "send draft" step in the `newsletter-digest` skill with automated send via the self-hosted n8n webhook at `https://n8n.arigsela.com/webhook/newsletter-digest`. Preserve the Gmail draft path as a fallback so no digest is ever lost.

## Success Criteria

- [ ] Running the skill on a normal day sends an email to `arigsela@gmail.com` with no manual click.
- [ ] n8n down / 401 / timeout → skill falls back to Gmail draft + inline warning.
- [ ] `N8N_WEBHOOK_URL` unset → skill behaves exactly like today (draft-only).
- [ ] n8n rejects unauthenticated POSTs with 401.
- [ ] Workflow JSON is versioned in `n8n-workflows/newsletter-digest-send.json`.
- [ ] End-to-end latency < 10s on happy path.

## Research Findings

### Relevant Files (this repo)

- `docs/plans/newsletter-digest-n8n-prd.md` — source PRD.
- `base-apps/n8n/nginx-ingress-webhook.yaml` — confirms `/webhook` is publicly reachable over HTTPS (no IP allowlist, TLS, 10 MB body, 60 s timeouts, 50 rps).
- `base-apps/n8n/deployments.yaml` — confirms `WEBHOOK_URL=https://n8n.arigsela.com/` and Basic Auth is on for the admin UI (webhook ingress bypasses it).
- `n8n-workflows/{claude-digest-generator,devops-digest-generator,feed-data-ingestion}.json` — export format to match.

### Relevant Files (out of repo, skill lives here)

- `<cowork-skills>/newsletter-digest/SKILL.md` — Phase 6 lines 133–144 to replace; Phase 7 unchanged.

### Existing Patterns

- Workflow JSON exports are checked into `n8n-workflows/` — we'll follow suit.
- Ingress routing: anything under `/webhook/*` hits the n8n service — `/webhook/newsletter-digest` is the correct path.
- Per-app secret management in this repo uses Vault, but n8n credentials (bearer token, Gmail OAuth) live inside n8n's own encrypted store — no Vault integration needed.

### Dependencies

- Existing `SMTP account` credential in n8n (smtp.gmail.com:465, Gmail App Password) — connection already tested.
- n8n's Header Auth credential type for the bearer token.
- `curl` inside the Cowork sandbox where the skill runs.

## Architecture Decisions

### Decision 1: Webhook path

**Chosen:** `/webhook/newsletter-digest` — matches PRD, fits under the public `/webhook` prefix on the ingress. No ingress changes required.

### Decision 2: HTML conversion inside the skill

**Options:**
- A. Depend on `references/html_template.md` (per PRD §6.1 step 2).
- B. Inline a minimal wrapper (`<!doctype html><html><body>…</body></html>`) and convert Markdown inline with a small awk/python helper.

**Chosen:** **B**. The reference template isn't present in the synced skill directory today, so depending on it is brittle. A minimal wrapper keeps the skill self-contained and still produces valid, Gmail-friendly HTML. (If the user later authors `references/html_template.md`, the skill can swap in a richer template without changing the plan.)

### Decision 3: curl vs Python in the skill

**Chosen:** `curl`. One-shot POST with `--fail-with-body --max-time 30 -w "%{http_code}"` captures status + body cleanly. No extra dependencies.

### Decision 4: Send node and message_id extraction in n8n's "Respond to Webhook"

**Chosen:** Use n8n's **Send Email (SMTP)** node bound to the existing `SMTP account` credential (smtp.gmail.com:465, Gmail App Password). An SMTP credential was already configured and tested in n8n, so reusing it avoids a fresh Gmail OAuth consent flow and keeps things moving. The PRD (§7.2 step 3) explicitly allows either Gmail node or SMTP node.

SMTP's Send Email node returns nodemailer's output, where the message id is under `messageId` (RFC 5322 format, e.g. `<abc@smtp.gmail.com>`). The Respond node will reference `{{$('Send Email').item.json.messageId}}`. Task 1.4 verifies this field name on a live run.

### Decision 5: No retry, no archive, no bearer-token storage guidance

Per user direction: single POST, fall back to draft on any failure; no archive branch in v1; bearer-token storage on the skill side is Cowork's concern.

## Implementation

### Phase 1: Build and Export the n8n Workflow

#### Task 1.1: Generate bearer token and create Header Auth credential in n8n

**Files:** none (n8n UI operation)
**Steps:**
1. From an admin-allowlisted IP, log in to `https://n8n.arigsela.com`.
2. Generate token locally: `openssl rand -hex 32`.
3. In n8n UI: **Credentials → New → Header Auth**. Name: `Newsletter Digest Webhook Token`. Header name: `Authorization`. Header value: `Bearer <token>`.
4. Store the raw token in the user's password manager for later injection into Cowork.

**Testing:**
- [ ] Credential appears in the n8n credentials list.
- [ ] Token length is 64 hex chars.
- [ ] Token value is not logged anywhere (check n8n execution logs after creation).

#### Task 1.2: Verify SMTP credential in n8n

**Files:** none (n8n UI)
**Steps:**
1. In n8n UI: **Credentials**. Verify the existing `SMTP account` credential (smtp.gmail.com:465, user `arigsela@gmail.com`) shows "Connection tested successfully".
2. Note the credential name (`SMTP account`) for reuse in Task 1.3.

**Testing:**
- [x] Credential status shows "Connection tested successfully" (confirmed in existing credential screen).

#### Task 1.3: Build the "Newsletter Digest — Send" workflow

**Files:** none yet (built in n8n UI, exported in Task 1.5)
**Steps:**
1. **Webhook node**
   - Method: `POST`, Path: `newsletter-digest`, Authentication: Header Auth (credential from 1.1).
   - Response mode: "Using 'Respond to Webhook' node".
   - Options: body parsing = JSON, raw body off.
2. **IF — validate payload**
   - Conditions (AND): `{{$json.subject}}` not empty, `{{$json.html_body}}` not empty, `{{$json.to}}` not empty.
   - False branch → a **Respond to Webhook** node returning HTTP 400 with `{"error":"missing required fields"}`.
3. **Send Email (SMTP)** (true branch)
   - Credential: `SMTP account` (from Task 1.2).
   - From Email: `arigsela@gmail.com`. To Email: `={{$json.to}}`. Subject: `={{$json.subject}}`. HTML: `={{$json.html_body}}`. Leave Text empty (HTML-only is fine; most MUAs auto-derive).
4. **Respond to Webhook (success)**
   - Response code: 200. Body:
     ```json
     {
       "status": "sent",
       "message_id": "={{$('Send Email').item.json.messageId}}",
       "sent_at": "={{$now.toISO()}}"
     }
     ```
5. **Error Trigger workflow** (separate, one-time): if none exists in this n8n instance, create a minimal one that emails `arigsela@gmail.com` on failure and bind it to the send workflow under **Workflow Settings → Error Workflow**. If one already exists, reuse it.

**Testing:**
- [ ] Activate the workflow. Hit the webhook via curl from an allowlisted host with a valid bearer + sample JSON payload → receive 200 JSON with `message_id` populated and an email in inbox.
- [ ] Hit the webhook with missing `html_body` → receive 400 `{"error":"missing required fields"}`.
- [ ] Hit the webhook with no Authorization header → n8n returns 401.
- [ ] Hit the webhook with `Authorization: Bearer wrong` → n8n returns 401/403.
- [ ] Temporarily break the Gmail credential (rotate offline) → error workflow fires; webhook returns 500.

#### Task 1.4: Confirm the Send Email (SMTP) node output field for message_id

**Files:** none (n8n UI)
**Steps:**
1. In the n8n execution list, open a successful run.
2. Inspect the Send Email node output — confirm the message id is under `messageId`. If it's nested differently in this n8n version, update the Respond node expression accordingly and re-test.

**Testing:**
- [ ] Success response payload contains a non-null `message_id` (expected shape: `<something@smtp.gmail.com>`).

#### Task 1.5: Export and commit workflow JSON

**Files:** `n8n-workflows/newsletter-digest-send.json` (new)
**Steps:**
1. In n8n UI: **Workflow → Download** → save as `newsletter-digest-send.json`.
2. Move to `/Users/arisela/git/kubernetes/n8n-workflows/newsletter-digest-send.json`.
3. Verify no credential IDs or raw tokens are embedded (n8n exports reference credentials by ID only — confirm).

**Testing:**
- [ ] `grep -iE 'bearer|authorization|password' n8n-workflows/newsletter-digest-send.json` returns nothing suspicious.
- [ ] `jq . n8n-workflows/newsletter-digest-send.json` parses cleanly.
- [ ] Import the JSON into a scratch n8n instance (or re-import into the same instance under a different name) → workflow reconstructs without error.

### Phase 2: Skill Modification (applied in Cowork, documented here)

Scope note: this phase's edits land in the Cowork-synced skill directory, not in this git repo. The plan captures the exact diff so it's reproducible.

#### Task 2.1: Replace Phase 6 in `newsletter-digest/SKILL.md`

**Files:** `<cowork-skills>/newsletter-digest/SKILL.md` lines 133–144
**Steps:**
1. Replace the existing "Deliver both ways" block with:
   - **Step 1 — Inline render** (unchanged).
   - **Step 2 — Check env vars.** If `N8N_WEBHOOK_URL` or `N8N_WEBHOOK_TOKEN` is unset/empty → skip n8n, go straight to `Gmail:create_draft` (existing behavior) and log `"n8n not configured — saved as Gmail draft"`.
   - **Step 3 — Build HTML body.** Wrap the Markdown summary in a minimal HTML shell. Convert headings, bold, links, and paragraphs (Markdown→HTML) with an inline python one-liner or equivalent — no dependency on `references/html_template.md`.
   - **Step 4 — POST to n8n** via:
     ```bash
     curl -sS --fail-with-body --max-time 30 \
       -X POST "$N8N_WEBHOOK_URL" \
       -H "Authorization: Bearer $N8N_WEBHOOK_TOKEN" \
       -H "Content-Type: application/json" \
       -d @payload.json \
       -w '\n__HTTP_CODE__%{http_code}\n'
     ```
     Payload matches PRD §5.1 (subject, to, html_body, markdown_body, meta).
   - **Step 5 — Branch on HTTP code.**
     - `2xx` → inline confirmation: `"Digest emailed via n8n — message_id: <id>."`
     - Non-2xx or curl non-zero exit → fall through to `Gmail:create_draft` with same subject + html_body; inline warning: `"n8n delivery failed (<status>/<reason>). Saved as Gmail draft instead — review and send manually."`
2. Add a short config section near the top of SKILL.md listing `N8N_WEBHOOK_URL` and `N8N_WEBHOOK_TOKEN` (optional env vars) per PRD §6.2.
3. Do NOT change Phases 1–5 or Phase 7.

**Testing:** covered end-to-end in Phase 3.

### Phase 3: End-to-End Testing

#### Task 3.1: Happy path

**Steps:**
1. Set `N8N_WEBHOOK_URL` and `N8N_WEBHOOK_TOKEN` in the Cowork session env.
2. Run the skill against a real Gmail window that contains newsletters.
3. Watch for inline "Digest emailed via n8n" confirmation with a message_id.

**Testing:**
- [ ] Email arrives in `arigsela@gmail.com` inbox within 10s.
- [ ] Subject matches `Newsletter Digest — <Month DD, YYYY>`.
- [ ] HTML renders correctly in Gmail web + mobile.
- [ ] No Gmail draft is created.
- [ ] n8n execution log shows a single successful run.

#### Task 3.2: n8n unreachable

**Steps:**
1. Set `N8N_WEBHOOK_URL` to `https://n8n.arigsela.com/webhook/does-not-exist` (or a bogus host).
2. Run the skill.

**Testing:**
- [ ] Skill reports "n8n delivery failed (…). Saved as Gmail draft instead".
- [ ] A Gmail draft exists with the correct subject and HTML body.
- [ ] Inline summary is still rendered.

#### Task 3.3: Bad token

**Steps:**
1. Set `N8N_WEBHOOK_URL` correctly but `N8N_WEBHOOK_TOKEN` to `wrong`.
2. Run the skill.

**Testing:**
- [ ] curl receives HTTP 401/403 from n8n.
- [ ] Skill falls back to Gmail draft with warning.

#### Task 3.4: Unset env (backward compat)

**Steps:**
1. Unset both env vars in the Cowork session.
2. Run the skill.

**Testing:**
- [ ] No curl attempt made (check skill log).
- [ ] Gmail draft created (identical to pre-change behavior).
- [ ] Inline confirmation reads "Draft saved to Gmail — review and send when ready."

#### Task 3.5: Empty digest (pre-existing safeguard)

**Steps:**
1. Run the skill against a time window with no matching newsletters.

**Testing:**
- [ ] Skill aborts before Phase 6 per existing logic — no webhook call, no draft.

### Phase 4: Commit and Rollout

#### Task 4.1: Commit workflow JSON

**Files:** `n8n-workflows/newsletter-digest-send.json`
**Steps:**
1. `git add n8n-workflows/newsletter-digest-send.json`
2. Commit with a clear message referencing the PRD.
3. Push to `main` (this repo has no deploy impact from the workflow file — it's documentation/backup only; the live workflow lives in n8n).

**Testing:**
- [ ] `git log --oneline -1` shows the new commit.
- [ ] PR or direct push reviewed.

#### Task 4.2: Retire the manual "send draft" step

**Steps:**
1. Remove the daily-routine reminder/habit of opening Gmail drafts after a digest run.
2. Keep an eye on the next ~3 daily runs for any fallback triggers.

**Testing:**
- [ ] No manual send needed for 3 consecutive days.
- [ ] If a fallback fires, investigate the n8n execution log before disabling monitoring.

## End-to-End Testing

After all phases are complete, run one final end-to-end check on a normal morning: verify the digest email arrives, the inline skill output matches, and no draft was created. Spot-check the n8n execution log for a clean run.

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Send Email (SMTP) node output field shape differs between n8n versions | Low | Task 1.4 verifies on a live run before Phase 2. |
| Gmail App Password on SMTP credential is revoked silently | Low | Error workflow (Task 1.3 step 5) emails on failure. |
| Skill runs without `curl` available in Cowork sandbox | Very Low | PRD already prescribes curl; if absent, fall back to Python `urllib.request`. |
| Bearer token leaks via skill logs | Low | curl `-sS` avoids progress output; skill should log `$http_code`, never the token. Document this in Phase 2. |
| Let's Encrypt renewal gap breaks TLS | Very Low | cert-manager already manages renewal; if it happens, fallback path handles it cleanly. |
| Markdown→HTML conversion misrenders in Gmail | Medium | Keep conversion minimal; verify visually in Task 3.1. |
| Workflow JSON drifts from live n8n workflow | Medium | Any future edit to the workflow must re-export and commit. Call this out in the commit message. |

## Out of Scope (per user direction)

- Retry with backoff
- Archive branch (GDrive/S3 of markdown_body)
- Persistent multi-recipient support
- IP allowlist tightening on the webhook ingress
- Bearer-token storage guidance for the skill side (Cowork owns this)

## Progress Tracking

**Phases:** 1 / 4 complete
**Tasks:** 5 / 13 complete

### Phase Status
- ✅ Phase 1: Build and Export the n8n Workflow (5/5)
- ⬜ Phase 2: Skill Modification (0/1)
- ⬜ Phase 3: End-to-End Testing (0/5)
- ⬜ Phase 4: Commit and Rollout (0/2)
