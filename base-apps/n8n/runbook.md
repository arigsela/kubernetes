---
type: "Kubernetes App Runbook"
title: "n8n — Runbook"
description: "Operational runbook for n8n: failure modes, checks, and fixes."
app: n8n
catalog_entity: n8n
kind: runbook
namespace: n8n
last_reviewed: 2026-09-30
status: stable
tags: [automation, postgresql, webhooks]
sources:
  - base-apps/n8n/deployments.yaml
  - base-apps/n8n/external-secrets.yaml
  - base-apps/n8n/httproute.yaml
  - base-apps/n8n/pvc.yaml
  - base-apps/n8n/workflows-configmap.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/istio-waf/wasmplugin.yaml
  - n8n-workflows/newsletter-digest-send.json
---

# n8n runbook

## Failure modes

### Symptom: admin UI (`https://n8n.arigsela.com/`) returns 403 `RBAC: access denied` (or times out) from a normal browser, but works from home/office
- **Check:** that 403 comes from the Gateway's `AuthorizationPolicy` (`base-apps/istio-ingress/authorizationpolicy.yaml`): only the webhook paths are public, and the rest of `n8n.arigsela.com` is limited to the allow-listed `/32`s. Compare the caller's public IP (`curl -s ifconfig.me`) with that rule. If *every* allow-listed host broke at once, the home WAN address probably rotated.
- **Fix:** this is the allow-list working as designed, not an outage. For a legitimate new address, PR it into the n8n admin rule. For a WAN rotation, see `base-apps/wan-ip-monitor/runbook.md`. Don't add a public rule for `/`: the editor can read the pod's secrets (see `docs.md`).

### Symptom: webhook calls to `https://n8n.arigsela.com/webhook/...` (or `/webhook-test/...`, `/mcp-server/...`) return 404
- **Check:** the route is one catch-all `HTTPRoute`, so a 404 almost always comes from n8n itself: `kubectl -n n8n get httproute n8n -o yaml` (accepted on listener `https-n8n`?) and `kubectl -n n8n logs deploy/n8n --tail=100`. Confirm in the UI that the target workflow is **Active** — n8n only registers a webhook path for an active workflow, and only at startup, and production (`/webhook/...`) vs. test (`/webhook-test/...`) URLs differ by design.
- **Fix:** for a UI-authored workflow, activate it in the editor. For a Git-managed one (`workflows-configmap.yaml`), the pod has to roll so the init container re-imports it: bump `checksum/workflows` in `deployments.yaml` (one-liner in its comment) or `kubectl -n n8n rollout restart deploy/n8n`, and check a new workflow has its `import:workflow`/`update:workflow --active=true` pair in the init container's `args`.

### Symptom: every request returns `503 Database is not ready!` while `/healthz` is 200
- **Check:** n8n lost its Postgres connection (the shared instance was evicted, restarted or failed over) and does not reconnect by itself. `kubectl -n postgresql get pods` for the DB, `kubectl -n n8n get pods` for restarts. On 2026-09-29 the postgresql pod was evicted during a node drain and n8n stayed broken for 16 hours until the pod was deleted by hand.
- **Fix:** the probes now use `/healthz/readiness`, so the liveness probe should restart n8n after 3 minutes of DB outage (18 × 10s, after the 240s initial delay). If it hasn't recovered once Postgres is healthy again, restart it: `kubectl -n n8n rollout restart deploy/n8n`. If Postgres itself is down, fix that first (`base-apps/postgresql/runbook.md`).

### Symptom: a webhook caller gets 403 with an empty body (or an automation silently stops, with no execution in n8n)
- **Check:** the Coraza WAF blocks before n8n sees the request, so there is no execution. Tell the two 403s apart in the gateway access log (`kubectl -n istio-ingress logs deploy/main-istio`): a WAF 403 has an empty body and no `via_upstream`; an n8n auth 403 has the body `Authorization data is wrong!` and `via_upstream`. Bodies on `/webhook*` and `/mcp-server*` are inspected (rule 9010 in `base-apps/istio-waf/wasmplugin.yaml`); a known, accepted false positive is a JSON body carrying filesystem paths (`../x`, `/var/log`, CRS 930110/930120). Small test payloads often pass where real ones don't, so reproduce with a realistic body.
- **Fix:** add a scoped exclusion after the CRS include (e.g. `SecRuleUpdateTargetById <rule> "!ARGS_POST:json.<field>"`), or, for a payload that can never converge, a per-path body exemption like rule 9013. Never raise the anomaly threshold. To take n8n out of enforcement temporarily, re-add its `DetectionOnly` line (rule 9003, commented in the file).

### Symptom: the interview janitor's GitHub run fails at the Slack step
The janitor posts to `https://n8n.arigsela.com/webhook/interview-janitor` and fails its run on any non-2xx.
- **500:** the workflow threw. Open the failed execution of **Interview Janitor to Slack** in n8n: `unauthorized` means the `X-Janitor-Token` header doesn't match `INTERVIEW_JANITOR_TOKEN` (Vault property missing or rotated without a pod restart; see the rotation how-to); `Slack did not accept the message` carries Slack's error code.
- **403 with an empty body:** the WAF. The body is exempt on this path (rule `9014`), so look for a header or URI match, or a change to that rule, in the gateway log as in the WAF symptom above.
- **404:** the workflow isn't active or registered; see the 404 symptom.

### Symptom: `#oncall-alerts` says "Interview sandbox: janitor silent for N h"
The workflow's watchdog has had no janitor check-in for 30 hours or more. Look at the janitor workflow's runs in the private interview-labs repo: late or missing scheduled runs, a failed run, or every post failing (a 500 or 403 above fails the run). A pod restart or re-import can reset the watchdog's clock (static data), which delays an alert but never fakes one.

### Symptom: the newsletter/feed digest arrives as a Gmail draft instead of an email
The digest skills (`newsletter-digest-n8n`, and `feed-digest`, which borrows its token) POST to `https://n8n.arigsela.com/webhook/newsletter-digest` and fall back to a Gmail draft on **any** non-2xx, so a draft is the only symptom.
- **Check:** in n8n, workflow **"Newsletter Digest — Send"** (id `qX9W779auOovEQe9`): Webhook (Header Auth) → validate `subject`/`to`/`html_body` (400 `{"error":"missing required fields"}` if any is empty) → Send Email via the `SMTP account` credential (Gmail SMTP, app password) → 200 `{"status":"sent","message_id":...}`. No execution for the run means the request never got past auth or the WAF:
  - **403 `Authorization data is wrong!`** (auth failures create no execution): token drift. The token lives in exactly two places, the n8n Header Auth credential **"Newsletter Digest Webhook Token"** (id `SaXQfVQf0KtjbuR6`, header `Authorization: Bearer <64-hex>`) and the skill's own copy. There is **no Vault copy**. Cheap check that sends no email: POST `{}` with the skill's token; `400 missing required fields` means auth passed.
  - **403 with an empty body:** the WAF. Rule 9013 exempts exactly `/webhook/newsletter-digest` from body inspection, because the HTML email body scores far over the threshold every day. If the path or rule changed, that's the cause. Verify with a realistic HTML body and an empty `subject`: n8n answers 400 and nothing is sent.
  - **5xx or an errored execution:** the SMTP send failed (e.g. a revoked Gmail app password on `SMTP account`).
- **Fix:** for drift, make the n8n credential match the skill's token (edit the credential in the UI from an allow-listed IP; on 2026-09-24 it was done with `n8n export:credentials --decrypted` → edit → `n8n import:credentials` in the pod, no restart needed). Compare values by sha256 prefix and never print them. Delete any decrypted export afterwards. The workflow's backup export is `n8n-workflows/newsletter-digest-send.json`; re-export it after editing the workflow.

### Symptom: pod CrashLoopBackOff, or n8n runs but reports it cannot decrypt existing credentials/workflows
- **Check:** `kubectl -n n8n get pods` and `kubectl -n n8n logs deploy/n8n --tail=200` (also `-c import-workflows` for the init container). First rule out normal slow startup — both probes use `initialDelaySeconds: 240`, so the pod is expected to take up to 4 minutes before probes even begin. If logs show Postgres connection errors, see the 503 symptom above. If logs show credential/decryption errors instead, check whether `N8N_ENCRYPTION_KEY` (`external-secrets.yaml`, Vault key `n8n`/`encryption-key`) was rotated in Vault — n8n cannot decrypt previously stored credentials with a different key than the one used to save them.
- **Fix:** for DB issues, resolve the shared `postgresql` app first — n8n has no local fallback DB since `DB_TYPE=postgresdb`. For an encryption-key mismatch, restore the original Vault value at `n8n`/`encryption-key` rather than rotating it in place; rotating `N8N_ENCRYPTION_KEY` requires n8n's own credential re-encryption process, not a plain secret swap.

### Symptom: pod healthy but new workflow executions fail to write data / pod evicted for disk pressure
- **Check:** `kubectl -n n8n exec deploy/n8n -- df -h /home/node/.n8n` against the `n8n-pvc` (`pvc.yaml`, `5Gi`, `local-path`). This volume holds n8n's local `.n8n` state directory, not workflow execution history (that's in Postgres per the `EXECUTIONS_DATA_SAVE_*` settings in `deployments.yaml`), but it can still fill from logs/binary temp files.
- **Fix:** PR a size increase to `spec.resources.requests.storage` in `base-apps/n8n/pvc.yaml` (note: `local-path` PVCs are not trivially resizable in-place — check the storage class's expansion support before relying on a live resize).

## How-to
### Deploy / update
Edit manifests here and PR; Argo CD auto-syncs on merge (`prune: true`, `selfHeal: true`). An n8n upgrade is a tag bump on **both** images in `deployments.yaml` (main container and `import-workflows`).

### Rotate the Slack bot token or another Vault-backed value
Update the property under Vault key `n8n` (e.g. `slack-bot-token`); ESO re-syncs `n8n-secrets` within the 1h `refreshInterval`, then restart the pod (`kubectl -n n8n rollout restart deploy/n8n`) since the values are env vars. Never rotate `encryption-key` this way (see above).

### Rotate the interview janitor token
The token lives in two places: Vault (`k8s-secrets/n8n`, property `interview-janitor-token`) and the `NOTIFY_WEBHOOK_TOKEN` Actions secret of the private interview-labs repo. Its `scripts/notify-setup.sh` writes both from one generated value without printing it. Then force the ESO re-sync (`kubectl -n n8n annotate externalsecret n8n-secrets force-sync=$(date +%s) --overwrite`) and restart the pod as above. Until the restart, janitor posts fail with a 500 (the running pod still holds the old token).
