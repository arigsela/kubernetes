# PRD: Newsletter Digest → n8n Email Delivery

**Owner:** Ari (DevOps Manager)
**Status:** Draft — ready for implementation planning
**Last updated:** 2026-04-22

---

## 1. Background

We have an existing Claude skill, `newsletter-digest`, that runs on demand (and will soon run on a schedule) to:

1. Pull recent newsletter emails from Gmail (TLDR, Substack, etc.)
2. Rank and enrich stories through a DevOps / platform-engineering lens
3. Produce a synthesized Markdown summary
4. Save that summary as a **Gmail draft** addressed to `arigsela@gmail.com`

**Problem:** The Gmail MCP connector available to the skill exposes only `create_draft` — no `send_message` tool. That means every run leaves a draft sitting in Gmail that requires manual "Send." The goal of having a daily digest land in the inbox automatically is not met today.

We already run a self-hosted **n8n** instance, which is well suited to act as the send-side automation: it has mature Gmail / SMTP integrations and can expose an HTTP webhook that the skill can POST to.

## 2. Goals

- Deliver the daily digest to `arigsela@gmail.com` as a **sent email**, with no manual step.
- Keep the skill's existing inline chat output unchanged (still the primary deliverable).
- Offload the send responsibility to n8n so the skill itself never needs SMTP/OAuth credentials.
- Produce a minimal, auditable workflow in n8n (≤ 5 nodes) that is easy to re-deploy.

## 3. Non-Goals

- No persistent archive of digests (out of scope; can be added later by extending the n8n workflow to write to GDrive / S3).
- No support for multiple recipients in v1 — hard-coded to `arigsela@gmail.com`.
- No attachment support in v1 — body-only HTML email.
- Not replacing the Gmail draft path wholesale; draft-as-fallback is in scope (see §7).

## 4. Proposed Architecture

```
┌──────────────────────────┐        HTTPS POST          ┌───────────────────────────┐
│  newsletter-digest skill │ ─────────────────────────▶ │  n8n Webhook node         │
│  (Cowork sandbox)        │   JSON body +              │  /webhook/newsletter-     │
│                          │   Bearer auth header       │  digest                   │
└──────────────────────────┘                            └────────────┬──────────────┘
                                                                     │
                                                                     ▼
                                                        ┌───────────────────────────┐
                                                        │  Gmail node (send)        │
                                                        │  (or SMTP node)           │
                                                        └────────────┬──────────────┘
                                                                     │
                                                                     ▼
                                                        ┌───────────────────────────┐
                                                        │  Respond to Webhook node  │
                                                        │  200 OK + message_id      │
                                                        └───────────────────────────┘
```

Design decisions and rationale:

- **Webhook over file-drop.** A newsletter digest is 5–50 KB of HTML/Markdown — nowhere near n8n's default 16 MB webhook payload limit. A synchronous POST is simpler than a GDrive drop + polling workflow (3 nodes vs ~6) and gives the skill immediate success/failure feedback.
- **n8n owns credentials.** The skill never touches SMTP creds or Gmail OAuth. Auth to n8n is a single bearer token.
- **Gmail draft stays as fallback.** If the webhook POST fails (n8n down, network issue, auth rejected), the skill falls back to the existing `create_draft` behavior so the digest is never lost. Belt and suspenders.

## 5. API Contract

### 5.1 Request (skill → n8n)

**Endpoint:** `POST https://<n8n-host>/webhook/newsletter-digest`

**Headers:**

```
Content-Type: application/json
Authorization: Bearer <N8N_WEBHOOK_TOKEN>
```

**Body (JSON):**

```json
{
  "subject": "Newsletter Digest — April 22, 2026",
  "to": "arigsela@gmail.com",
  "html_body": "<!doctype html><html>...</html>",
  "markdown_body": "# Newsletter Digest — April 22, 2026\n\n...",
  "meta": {
    "run_id": "2026-04-22T13:04:11Z",
    "source_count": 5,
    "sources": ["tldr.tech", "newsletter.platformengineer.io", "..."],
    "top_pick_urls": ["https://...", "https://..."]
  }
}
```

Field notes:

- `html_body` is the primary render used by the Gmail/SMTP node.
- `markdown_body` is included for observability and future archival; n8n can ignore it.
- `meta` is informational — useful for n8n logging, alerting on anomalous runs (e.g., `source_count == 0`), and future archival workflows.

### 5.2 Response (n8n → skill)

**Success (200):**

```json
{
  "status": "sent",
  "message_id": "<gmail-message-id>",
  "sent_at": "2026-04-22T13:04:14Z"
}
```

**Failure (4xx/5xx):** Any non-2xx response triggers the skill's fallback path (create Gmail draft instead).

## 6. Skill Modification (`newsletter-digest`)

Target file: `/sessions/.../mnt/.claude/skills/newsletter-digest/SKILL.md`

### 6.1 Replace Phase 6 ("Deliver both ways")

Current Phase 6 behavior:

1. Render summary inline in chat ✅ keep
2. `Gmail:create_draft` ← replace with webhook POST + draft fallback

New Phase 6 behavior:

1. Render summary inline in chat (unchanged).
2. Convert Markdown → HTML using existing template at `references/html_template.md`.
3. `POST` to n8n webhook with the payload defined in §5.1. Use `bash` + `curl`:
   - Timeout: 30s.
   - Read `N8N_WEBHOOK_URL` and `N8N_WEBHOOK_TOKEN` from environment (set in skill config / session env).
4. On 2xx: confirm inline with "Digest emailed via n8n — message_id: `<id>`."
5. On non-2xx or timeout: fall back to `Gmail:create_draft` with the same subject and HTML body, and surface a warning inline: "n8n delivery failed (reason: `<http_status>` / `<error>`). Saved as Gmail draft instead — review and send manually."

### 6.2 Config surface

Document two required config values at the top of the skill:

| Env var | Description | Example |
|---|---|---|
| `N8N_WEBHOOK_URL` | Full webhook URL (including path) | `https://n8n.example.com/webhook/newsletter-digest` |
| `N8N_WEBHOOK_TOKEN` | Bearer token used in `Authorization` header | `<opaque random string, ≥32 chars>` |

If either is unset at run time, the skill should **skip the n8n POST entirely** and fall straight through to the existing draft behavior (logging a one-line warning). This keeps the skill backward-compatible for anyone running it without n8n configured.

### 6.3 No other phase changes

Phases 1–5 (time window, discovery, fetch, rank, build summary) and Phase 7 (state update) are unchanged.

## 7. n8n Workflow

### 7.1 Workflow name

`Newsletter Digest — Send`

### 7.2 Nodes

1. **Webhook**
   - Method: `POST`
   - Path: `newsletter-digest`
   - Authentication: Header Auth — expects `Authorization: Bearer <token>` where token is stored as an n8n credential.
   - Response mode: "Using 'Respond to Webhook' node"
   - Options: body parsing = JSON, raw body = off.

2. **IF — validate payload** (optional but recommended)
   - Condition: `subject` AND `html_body` AND `to` all non-empty.
   - True branch → Gmail node. False branch → Respond 400 with `{ "error": "missing required fields" }`.

3. **Gmail — Send** (or SMTP node if preferred)
   - Credential: existing Gmail OAuth credential in n8n.
   - Operation: `Send`
   - To: `{{ $json.to }}`
   - Subject: `{{ $json.subject }}`
   - Email Type: `HTML`
   - Message: `{{ $json.html_body }}`

4. **Respond to Webhook**
   - Response code: `200`
   - Response body:
     ```json
     {
       "status": "sent",
       "message_id": "={{ $node['Gmail'].json.id }}",
       "sent_at": "={{ $now.toISO() }}"
     }
     ```

5. **Error Trigger workflow** (separate, one-time setup)
   - On any error in the main workflow, send a notification (email / Slack) so failed deliveries don't go silently missed. Suggest reusing existing n8n error-handling conventions if present.

### 7.3 Security

- n8n webhook **must** require the bearer token. Reject unauthenticated requests with 401.
- Token generated once (≥32 bytes random, e.g., `openssl rand -hex 32`) and stored as an n8n credential + in the skill's env.
- Webhook URL should be served over HTTPS only.
- Consider IP allowlisting if the Cowork sandbox uses stable egress IPs (follow up; not a v1 blocker).

## 8. Observability & Error Handling

| Failure | Detection | User-visible behavior |
|---|---|---|
| n8n unreachable (network / DNS) | curl non-zero exit | Inline warning, Gmail draft fallback |
| n8n returns 401 | HTTP 401 | Inline warning ("auth misconfigured"), draft fallback |
| n8n returns 4xx (bad payload) | HTTP 4xx | Inline warning with response body, draft fallback |
| Gmail node fails inside n8n | n8n error workflow fires | Skill sees 5xx → draft fallback. Ari gets error notification from n8n. |
| Empty digest (zero newsletters found) | Already handled in Phase 2 | Skill aborts before Phase 6 — no webhook call. |

Every run logs to stdout (visible in Cowork transcript): `POST status`, `http_code`, `message_id` on success, or fallback reason on failure.

## 9. Success Criteria

- ✅ Running the skill on a normal day results in a sent email to `arigsela@gmail.com` with the digest as HTML body, **without any manual Send click.**
- ✅ Disabling / breaking n8n causes the skill to fall back to the existing draft flow with a clear inline warning. No run is lost.
- ✅ The n8n workflow rejects unauthenticated POSTs with 401.
- ✅ Skill changes are backward-compatible: running with `N8N_WEBHOOK_URL` unset behaves identically to today.
- ✅ End-to-end latency (skill POST → email delivered) < 10 seconds on a healthy path.

## 10. Open Questions / Decisions Needed

1. **n8n network reachability.** Is the n8n instance already internet-accessible over HTTPS, or behind VPN only? If internal-only, we need a public URL (Cloudflare Tunnel / existing reverse proxy) before the skill sandbox can reach it.
2. **Gmail node vs. SMTP node in n8n.** Gmail node is simpler but requires OAuth; SMTP is provider-agnostic. Preference?
3. **Where to store the bearer token on the skill side?** Options: session env var set at Cowork startup, a file in the skill's directory that the skill reads at run time, or passed as a skill argument. Recommendation: session env var — no secret on disk, and the skill already runs in the session context.
4. **Retry policy on transient failures.** v1 proposal: no retry in the skill itself (single POST; fall back to draft on any failure). Acceptable, or do you want one retry with backoff before falling back?
5. **Archive.** Out of scope for v1, but worth flagging: a follow-on n8n branch could append the `markdown_body` to a GDrive folder for a searchable archive. Cheap to add later.

## 11. Implementation Order

1. Create n8n workflow per §7 with a test payload and verify end-to-end.
2. Generate bearer token, store as n8n credential + in Cowork session env.
3. Modify skill per §6. Test with n8n up → expect sent email.
4. Test skill with n8n down / bad token → expect draft fallback + warning.
5. Test skill with `N8N_WEBHOOK_URL` unset → expect original draft behavior.
6. Roll out: retire the manual "send draft" step from daily routine.

---

**End of PRD.**
