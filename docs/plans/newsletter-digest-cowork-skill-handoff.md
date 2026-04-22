# Handoff: Modify `newsletter-digest` Skill to Send via n8n Webhook

**Audience:** Claude Code running in Cowork sandbox, with access to the `newsletter-digest` skill.
**Prereqs on the n8n side:** complete and verified (workflow active, webhook authenticated, end-to-end email send tested from curl).
**Scope of this doc:** exactly what edits to make to the skill file, and how to test them.

---

## 1. Background

The `newsletter-digest` skill currently ends every run by creating a Gmail **draft** that the user must manually send. A self-hosted **n8n** instance now exposes a webhook that accepts a JSON payload and sends the digest as a real email, with no manual step.

Your job: change **Phase 6** of the skill so that, when the webhook env vars are present, the skill POSTs to n8n and only falls back to Gmail draft on failure. When the env vars are absent, the skill must behave exactly as it does today (draft-only, backward-compatible).

---

## 2. Target file

`<skill-root>/newsletter-digest/SKILL.md`

Edits are confined to:

- Phase 6 ("Deliver both ways") — rewrite.
- A new short config section near the top of the skill listing the optional env vars.

**Do NOT modify:** Phases 1–5 (time window, discovery, fetch, rank, build summary), Phase 7 (state update), the Error handling section, or the References list (except to note the template change, see §5 step 3).

---

## 3. Webhook contract (source of truth)

### Endpoint

```http
POST https://n8n.arigsela.com/webhook/newsletter-digest
```

### Required headers

```http
Authorization: Bearer <N8N_WEBHOOK_TOKEN>
Content-Type: application/json
```

### Request body (JSON)

```json
{
  "subject": "Newsletter Digest — April 22, 2026",
  "to": "arigsela@gmail.com",
  "html_body": "<!doctype html><html>...</html>",
  "markdown_body": "# Newsletter Digest — April 22, 2026\n\n...",
  "meta": {
    "run_id": "2026-04-22T13:04:11Z",
    "source_count": 5,
    "sources": ["tldr.tech", "newsletter.platformengineer.io"],
    "top_pick_urls": ["https://...", "https://..."]
  }
}
```

Field notes:

- `subject`, `to`, `html_body` are **required** — n8n returns 400 if any is empty.
- `to` should stay hard-coded to `"arigsela@gmail.com"`.
- `markdown_body` and `meta` are informational (useful for future archival and n8n-side alerting).

### Success response (200)

```json
{
  "status": "sent",
  "message_id": "<uuid@gmail.com>",
  "sent_at": "2026-04-22T13:04:14.986-04:00"
}
```

### Failure responses

- `400 {"error": "missing required fields"}` — payload was incomplete.
- `403 Authorization data is wrong!` — token missing or wrong (note: n8n returns a bare text body here, not JSON — handle gracefully).
- `5xx` — SMTP send failed or n8n itself crashed. An n8n-side error workflow will alert separately.

On any non-2xx (or curl transport error), fall back to `Gmail:create_draft` — never lose the digest.

---

## 4. Config surface to document in SKILL.md

Add near the top of the skill, right after the frontmatter / opening paragraph:

```markdown
## Configuration

This skill optionally delivers digests as sent emails via an n8n webhook. When the env vars below are present at run time, the skill POSTs the digest to n8n for automated send. When either is absent, the skill falls back to creating a Gmail draft (original behavior, backward-compatible).

| Env var              | Description                                     |
| -------------------- | ----------------------------------------------- |
| `N8N_WEBHOOK_URL`    | Full webhook URL, including path                |
| `N8N_WEBHOOK_TOKEN`  | Bearer token for the `Authorization` header    |

Both are expected to be set in the session env by the user — the skill never reads them from disk.
```

---

## 5. New Phase 6 behavior

Replace the existing Phase 6 block (currently the "Deliver both ways" section) with the following logic. Keep the section header style consistent with other phase headers in the file.

### Step 1 — Inline render (unchanged)

Render the complete Markdown summary inline in the chat response. This stays the primary deliverable.

### Step 2 — Check env vars

If `N8N_WEBHOOK_URL` **or** `N8N_WEBHOOK_TOKEN` is unset or empty, skip the webhook entirely and go to Step 6 (Gmail draft). Log a single line: `n8n not configured — saving Gmail draft instead`.

### Step 3 — Build HTML body

Wrap the Markdown summary in a minimal, Gmail-friendly HTML shell. **Do not depend on `references/html_template.md`** — that file is not guaranteed to exist in the sandbox. Inline the conversion:

- Outer wrapper: `<!doctype html><html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 680px; margin: 0 auto; padding: 16px; line-height: 1.5;"> ... </body></html>`
- Convert `#` / `##` / `###` headings, `**bold**`, `[text](url)` links, and paragraphs.
- Use a small Python one-liner, `pandoc` if available, or a similar tool — whichever works cleanly. Do not introduce a new dependency.
- The HTML content must match the inline Markdown summary semantically.

### Step 4 — POST to n8n

Compose the payload per §3 of this document and POST it with `curl`:

```bash
curl -sS --fail-with-body --max-time 30 \
  -X POST "$N8N_WEBHOOK_URL" \
  -H "Authorization: Bearer $N8N_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d @payload.json \
  -w '\n__HTTP_CODE__%{http_code}\n'
```

Capture both the response body and `%{http_code}` (the `__HTTP_CODE__<n>` sentinel at the end makes parsing trivial).

**Logging rule:** log `http_code` and `message_id` (on success) to stdout. **Never log the bearer token or the full `Authorization` header.**

### Step 5 — Branch on HTTP code

- **2xx:** parse the JSON response, extract `message_id`, and confirm inline with:
  `Digest emailed via n8n — message_id: <id>.`
  Then proceed to Phase 7 (state update).

- **Any non-2xx, curl non-zero exit, or timeout:** go to Step 6 (fallback draft).

### Step 6 — Fallback to Gmail draft

Create a Gmail draft with the same subject and the HTML body from Step 3, using the existing `Gmail:create_draft` MCP tool. Subject format stays `Newsletter Digest — <Month DD, YYYY>`. Recipient stays `arigsela@gmail.com`.

Surface a warning inline:

- If env vars were unset (Step 2): `Digest saved to Gmail draft — review and send when ready.`
- If n8n call failed: `n8n delivery failed (<http_status>/<short_reason>). Saved as Gmail draft instead — review and send manually.`

Then proceed to Phase 7.

### A note about the "never send directly" rule

The existing SKILL.md text says: *"Never send the email directly — drafting only. The safety rules require explicit per-send confirmation for email."* This constraint is **relaxed** under the n8n pattern — the user has authorized automated send via the n8n workflow because the send decision now lives in n8n (where it can be audited, disabled, or rate-limited independently of the skill). Remove that sentence from the new Phase 6.

---

## 6. Error handling section updates

Locate the existing `## Error handling` section. Add one new bullet; keep the rest.

```markdown
- **n8n webhook returns non-2xx or is unreachable**: fall back to `Gmail:create_draft` as Phase 6 Step 6 describes, and surface the failure reason inline.
```

---

## 7. Test plan — run these after making the edits

Run each scenario end-to-end. All four must behave as specified.

### 7.1 Happy path

**Given:** `N8N_WEBHOOK_URL` and `N8N_WEBHOOK_TOKEN` are set and valid.
**When:** you run the skill against a Gmail window containing newsletters.
**Then:**

- curl returns HTTP 200 with a non-empty `message_id`.
- Skill prints `Digest emailed via n8n — message_id: <id>.`
- An email arrives in `arigsela@gmail.com` within ~10 seconds.
- No Gmail draft is created.

### 7.2 n8n unreachable

**Given:** `N8N_WEBHOOK_URL="https://n8n.arigsela.com/webhook/does-not-exist"` (or any bogus host/path); `N8N_WEBHOOK_TOKEN` is anything.
**When:** you run the skill.
**Then:**

- curl returns non-2xx (404 for bad path; nonzero exit for DNS failure).
- Skill creates a Gmail draft with the correct subject and HTML body.
- Skill prints `n8n delivery failed (<status>/<reason>). Saved as Gmail draft instead — review and send manually.`

### 7.3 Bad token

**Given:** `N8N_WEBHOOK_URL` correct; `N8N_WEBHOOK_TOKEN="wrong"`.
**When:** you run the skill.
**Then:**

- curl returns HTTP 403 with body `Authorization data is wrong!`.
- Skill creates a Gmail draft.
- Skill prints `n8n delivery failed (403/...). Saved as Gmail draft instead — review and send manually.`

### 7.4 Unset env (backward compat)

**Given:** both env vars are unset or empty.
**When:** you run the skill.
**Then:**

- No curl attempt is made.
- Skill creates a Gmail draft (identical to pre-change behavior).
- Skill prints `Digest saved to Gmail draft — review and send when ready.`

### 7.5 Isolated webhook sanity check (optional but recommended before testing the skill)

Before running the full skill, prove the webhook works in isolation:

```bash
curl -sS -w '\n__HTTP__%{http_code}\n' \
  -X POST "$N8N_WEBHOOK_URL" \
  -H "Authorization: Bearer $N8N_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "Skill-side smoke test",
    "to": "arigsela@gmail.com",
    "html_body": "<p>If you see this, the skill-side curl path works.</p>"
  }'
```

Expect `HTTP 200` + an email. If that fails, the skill change won't work — stop and report before editing SKILL.md.

---

## 8. Constraints and nice-to-haves

- **Don't add retries.** Single POST; fall back on any failure. (This was an explicit product decision — we'd rather fail fast to draft than stack latency.)
- **Don't log the bearer token**, including in error messages. Log only `http_code` and, on success, `message_id`.
- **Don't introduce new Python/npm dependencies.** Use curl and whatever's already in the sandbox for Markdown→HTML conversion.
- **Don't broaden recipient support.** `to` stays `"arigsela@gmail.com"` in v1.
- **Don't archive the digest elsewhere.** `markdown_body` is in the payload so n8n can archive it later if we decide to; the skill doesn't write anywhere except state.

---

## 9. Success criteria (what "done" looks like)

- All four test scenarios in §7 pass.
- Phases 1–5, Phase 7, and the rest of SKILL.md are byte-identical to before.
- No bearer token appears in any log or error surface.
- A diff of SKILL.md shows: new Configuration section near the top, rewritten Phase 6, one new bullet in Error handling, and optionally a one-line note in References about the inlined HTML wrapper.

Report back with: the diff, the four test outputs, and a confirmation that the happy-path email arrived.
