---
type: "Kubernetes App Runbook"
title: "On-Call Agent — Runbook"
description: "Operational runbook for On-Call Agent: failure modes, checks, and fixes."
app: oncall-agent
catalog_entity: oncall-agent
kind: runbook
namespace: oncall-agent
last_reviewed: 2026-09-30
status: stable
tags: [ai-agent, anthropic, incident-response, slack]
sources:
  - base-apps/oncall-agent/deployment.yaml
  - base-apps/oncall-agent/external-secret.yaml
  - base-apps/oncall-agent/secret-store.yaml
  - base-apps/oncall-agent/incident-memory-pvc.yaml
  - base-apps/oncall-agent/httproute.yaml
  - base-apps/oncall-agent/certificate.yaml
  - base-apps/oncall-agent/reference-grant.yaml
  - base-apps/oncall-agent/configmap.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/istio-waf/wasmplugin.yaml
---

# oncall-agent runbook

## Failure modes

### Symptom: `oncall-agent-api` pod CrashLoopBackOff / `CreateContainerConfigError`
`deployment.yaml` requires `oncall-agent-secrets` to exist (`ANTHROPIC_API_KEY`,
`GITHUB_TOKEN`, `API_KEYS`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` are all
non-optional `secretKeyRef`s). That Secret is populated by the `ExternalSecret`
`oncall-agent-secrets` (`external-secret.yaml`, `refreshInterval: 15s`) from Vault key
`oncall-agent` via the `vault-backend` `SecretStore` (`secret-store.yaml`, role
`oncall-agent`, authenticating as the namespace's `default` ServiceAccount). If Vault is
sealed/unreachable, or the `oncall-agent` Vault role/policy doesn't grant access to that KV
path, the Secret never populates (or goes stale) and the pod fails to start.
- **Check:** `kubectl -n oncall-agent get externalsecret oncall-agent-secrets` (inspect
  `STATUS`/`READY`), `kubectl -n oncall-agent get secret oncall-agent-secrets`, and
  `kubectl -n oncall-agent describe pod -l app=oncall-agent-api` for the exact error.
- **Fix:** if Vault itself is sealed/down, that's the `vault` app's runbook, not this one.
  If Vault is healthy but this `ExternalSecret` still fails, the `SecretStore`'s
  `auth.kubernetes.role: oncall-agent` (`secret-store.yaml`) likely doesn't match the
  Vault-side role/policy — open a PR correcting the role name, or fix the Vault policy
  binding out-of-band (the role must bind SA `default` in `oncall-agent`).

### Symptom: agent responds but every LLM-backed request fails (401/429 from Anthropic)
`ANTHROPIC_API_KEY` (required) and optional `ANTHROPIC_MODEL` come from
`oncall-agent-secrets` (Vault key `oncall-agent`, properties `anthropic-api-key` /
`anthropic-model`, `external-secret.yaml`). An expired/revoked key or an exhausted
Anthropic quota surfaces as auth or rate-limit errors in the app logs, not as a pod
crash — probes only hit local `/health`.
- **Check:** `kubectl -n oncall-agent logs deploy/oncall-agent-api --tail=200 | grep -i anthropic`
  for 401/429/model-not-found errors.
- **Fix:** rotate/replace the `anthropic-api-key` value at Vault key `oncall-agent`
  (property `anthropic-api-key`) — this is a Vault data change, not a manifest edit, so no
  PR is needed for the key itself; if the root cause is instead a stale/incorrect
  `ANTHROPIC_MODEL` override in Vault, open a PR to remove it so the app falls back to its
  in-code default.

### Symptom: incident-memory data missing/corrupt or pod stuck `Pending` after reschedule
`incident-memory-pvc.yaml` is a single `local-path` PVC (`1Gi`, `ReadWriteOnce`) mounted at
`/app/data/incidents`, backing a local LanceDB store. `local-path-provisioner` binds the
volume to whichever node first created it, and the PVC's own comments call out that
LanceDB requires single-writer access — this only works safely with `replicas: 1`
(`deployment.yaml`). There is no cross-node replication or backup of this PVC.
- **Check:** `kubectl -n oncall-agent get pod -l app=oncall-agent-api -o wide` (look for
  `Pending` and the assigned node) and `kubectl -n oncall-agent get pvc
  incident-memory-pvc -o wide` to confirm which node the volume lives on.
- **Fix:** if the original node is gone, the local-path volume's incident history (and the
  persisted chat sessions, which live on the same volume) is gone with it — there is no
  failover copy. Open a PR to add a periodic backup/export of `/app/data/incidents` (e.g. a
  CronJob) if durability matters, and never scale `replicas` above `1` while storage stays
  local-path/LanceDB-backed. The PVC is `Prune=false`, so renaming or removing it in Git
  leaves the old volume behind; delete it deliberately if that's the intent.

### Symptom: Slack events (mentions, slash commands, interactive messages) don't reach the agent
Expected at the moment: `oncall.arigsela.com` has been IP-restricted since 2026-08-11, and
Slack's callback addresses can't be allow-listed, so the Events API integration is paused.
Slack fails silently from its side (it retries, then gives up); nothing alerts. Outbound
alerts to `#oncall-alerts` still work.
- **Check:** the `oncall.arigsela.com` rule in `base-apps/istio-ingress/authorizationpolicy.yaml`
  — if it has a `from:` block, Slack is shut out by design. Denials show in the gateway log:
  `kubectl -n istio-ingress logs deploy/main-istio --tail=200 | grep rbac_access_denied`.
- **Fix:** to re-enable, open a PR deleting that `from:` block (nothing else changes). The
  host stays in the Coraza WAF's scope with body inspection on (rules 9000/9011 in
  `base-apps/istio-waf/wasmplugin.yaml`), so afterwards watch for WAF 403s (empty body, no
  `via_upstream` in the gateway log) on real Slack payloads and add scoped exclusions if needed.

### Symptom: `https://oncall.arigsela.com` fails the TLS handshake or serves the wrong certificate
The `https-oncall` listener on the `main` Gateway references Secret `oncall-agent-tls` in this
namespace. Without the `ReferenceGrant` (`reference-grant.yaml`) the listener comes up
**silently certless** rather than erroring; without a `Ready` Certificate the Secret doesn't
exist.
- **Check:** `kubectl -n oncall-agent get certificate oncall-agent-tls` (`READY=True`?),
  `kubectl -n oncall-agent get referencegrant gateway-to-oncall-agent-tls`, and the listener
  status: `kubectl -n istio-ingress get gateway main -o yaml` (look for `https-oncall`'s
  `ResolvedRefs` condition). `kubectl -n oncall-agent get httproute oncall-agent -o yaml`
  shows whether the route is `Accepted`.
- **Fix:** restore the missing object by PR. A stuck Certificate is a cert-manager DNS-01
  issue on `letsencrypt-route53` (see `base-apps/cert-manager/runbook.md`).

## How-to
### Release a new image
Bump the tag in all three places in `deployment.yaml` (the `image:` and the `version:` label
on the Deployment and on its pod template) and open a PR; Argo CD rolls the pod on merge.
