---
type: "Kubernetes App Runbook"
title: "On-Call Agent — Runbook"
description: "Operational runbook for On-Call Agent: failure modes, checks, and fixes."
app: oncall-agent
catalog_entity: oncall-agent
kind: runbook
namespace: oncall-agent
last_reviewed: 2026-07-10
status: current
tags: [ai-agent, anthropic, incident-response, slack]
sources:
  - base-apps/oncall-agent/deployment.yaml
  - base-apps/oncall-agent/external-secret.yaml
  - base-apps/oncall-agent/secret-store.yaml
  - base-apps/oncall-agent/incident-memory-pvc.yaml
  - base-apps/oncall-agent/oncall-agent-external-ingress.yaml
---

# oncall-agent runbook

## Failure modes

### Symptom: `oncall-agent-api` pod CrashLoopBackOff / `CreateContainerConfigError`
`deployment.yaml` requires `oncall-agent-secrets` to exist (`ANTHROPIC_API_KEY`,
`GITHUB_TOKEN`, `API_KEYS`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` are all
non-optional `secretKeyRef`s). That Secret is populated by the `ExternalSecret`
`oncall-agent-secrets` (`external-secret.yaml`, `refreshInterval: 15s`) from Vault key
`oncall-agent` via the `vault-backend` `SecretStore` (`secret-store.yaml`, role
`oncall-agent`). If Vault is sealed/unreachable, or the `oncall-agent` Vault role/policy
doesn't grant access to that KV path, the Secret never populates (or goes stale) and the
pod fails to start.
- **Check:** `kubectl -n oncall-agent get externalsecret oncall-agent-secrets` (inspect
  `STATUS`/`READY`), `kubectl -n oncall-agent get secret oncall-agent-secrets`, and
  `kubectl -n oncall-agent describe pod -l app=oncall-agent-api` for the exact error.
- **Fix:** if Vault itself is sealed/down, that's the `vault` app's runbook, not this one.
  If Vault is healthy but this `ExternalSecret` still fails, the `SecretStore`'s
  `auth.kubernetes.role: oncall-agent` (`secret-store.yaml`) likely doesn't match the
  Vault-side role/policy — open a PR correcting the role name, or fix the Vault policy
  binding out-of-band.

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
- **Fix:** if the original node is gone, the local-path volume's incident history is gone
  with it — there is no failover copy. Open a PR to add a periodic backup/export of
  `/app/data/incidents` (e.g. a CronJob) if incident-history durability matters, and never
  scale `replicas` above `1` while storage stays local-path/LanceDB-backed.
