---
type: "Kubernetes App Guide"
title: "On-Call Agent"
description: "AI on-call/incident-response agent (Anthropic Claude, Slack, GitOps PRs)"
app: oncall-agent
catalog_entity: oncall-agent
kind: docs
namespace: oncall-agent
last_reviewed: 2026-09-30
status: current
tags: [ai-agent, anthropic, incident-response, slack]
sources:
  - base-apps/oncall-agent/deployment.yaml
  - base-apps/oncall-agent/configmap.yaml
  - base-apps/oncall-agent/external-secret.yaml
  - base-apps/oncall-agent/secret-store.yaml
  - base-apps/oncall-agent/incident-memory-pvc.yaml
  - base-apps/oncall-agent/httproute.yaml
  - base-apps/oncall-agent/certificate.yaml
  - base-apps/oncall-agent/reference-grant.yaml
  - base-apps/oncall-agent/rbac.yaml
  - base-apps/oncall-agent/namespace.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/istio-waf/wasmplugin.yaml
---

# oncall-agent

## What it is
`oncall-agent-api` is a single-replica FastAPI-style service (`deployment.yaml`, image
`852893458518.dkr.ecr.us-east-2.amazonaws.com/oncall-agent:v2.0.2`) that acts as an AI
on-call/incident-response assistant for this k3s homelab. It uses Anthropic's Claude API
for LLM analysis (`ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` env vars sourced from
`oncall-agent-secrets`, defaulting to `claude-sonnet-4-5-20250929` in code when
`ANTHROPIC_MODEL` is unset), and can open GitOps pull requests back against
`arigsela/kubernetes` (`GITOPS_REPO`, `GITOPS_BASE_PATH: base-apps/`,
`GITOPS_BASE_BRANCH: main` in `configmap.yaml`) rather than mutating the cluster directly.

## How it's deployed
A single Deployment replica (`deployment.yaml`, `replicas: 1`, pinned to
`node.kubernetes.io/workload: application` nodes) runs the `api` container on port 8000,
fronted by a ClusterIP Service (`oncall-agent-api`, port 80 -> 8000). Non-secret runtime
config (log level, session/rate-limit tuning, GitHub org, AWS region, Slack channel/severity
threshold, Zeus integration disabled) comes from the `oncall-agent-config` ConfigMap
(`configmap.yaml`), loaded wholesale via `envFrom`. `/health` backs both the liveness and
readiness probes. The pod runs as ServiceAccount `oncall-agent` (see *Cluster RBAC*).

A release is a tag bump in **three** places in `deployment.yaml`: the `image:` tag and the
`version:` label on both the Deployment and its pod template (`imagePullPolicy: Always`).

## Secrets (Vault)
A namespace-local `SecretStore` (`secret-store.yaml`, name `vault-backend`) authenticates to
Vault at `http://vault.vault.svc.cluster.local:8200` (KV v2, path `k8s-secrets`) via the
Kubernetes auth method with Vault role `oncall-agent`, as the namespace's `default`
ServiceAccount (not the app's `oncall-agent` SA). The `ExternalSecret`
(`external-secret.yaml`, `refreshInterval: 15s`) syncs the Vault entry `oncall-agent` into a
K8s Secret `oncall-agent-secrets`, providing: `anthropic-api-key` (required),
`anthropic-model` (optional), `github-token`, `api-keys` (comma-separated API auth keys),
`slack-bot-token` / `slack-signing-secret`, and `tavily-api-key` (optional, used for Tavily
web search by the "desk assistant" feature). The deployment consumes all of these via
`secretKeyRef`.

## Incident memory (persistent state)
`incident-memory-pvc.yaml` provisions a 1Gi `local-path` PVC (`incident-memory-pvc`,
`ReadWriteOnce`) mounted at `/app/data/incidents`, used by the agent as a local LanceDB
vector store of past incidents for retrieval/context. Chat sessions are persisted on the
same volume (`SESSION_PERSIST_PATH` = `INCIDENT_MEMORY_PATH` = `/app/data/incidents` in
`configmap.yaml`). The PVC's own comments call out that LanceDB's local file storage
requires single-writer access — only one replica may ever write, matching the deployment's
`replicas: 1`. It carries `argocd.argoproj.io/sync-options: Prune=false`, because
`local-path` reclaims with Delete and an Argo prune would destroy the data.

## Exposure
`oncall.arigsela.com` is the Gateway API `HTTPRoute` `oncall-agent` (`httproute.yaml`,
listener `https-oncall` on the shared `main` Gateway in `base-apps/istio-ingress/`) →
`oncall-agent-api:80`. TLS is `certificate.yaml` (`oncall-agent-tls`, issuer
`letsencrypt-route53`, DNS-01), which the Gateway reads through `reference-grant.yaml`. The
nginx-era rate limits and 120s proxy timeouts did not carry over; the route sets no
timeouts, and the app enforces its own per-client limits (`RATE_LIMIT_*` in
`configmap.yaml`).

**The host has been IP-restricted since 2026-08-11, which pauses the Slack Events API
integration.** Its rule in `base-apps/istio-ingress/authorizationpolicy.yaml` now has a
`from` block limited to the allow-listed addresses; Slack posts callbacks from AWS addresses
it doesn't publish as a stable set, so no allow-list can keep Slack working. It was closed
because the public host drew only scanner traffic and no Slack POSTs. Nothing alerts on this:
Slack just retries and gives up. To bring Slack events back, delete that `from` block by PR;
nothing else needs to change. Outbound Slack alerts (bot token → Slack API) are unaffected.

The host also stays inside the Coraza WAF's scope (`base-apps/istio-waf/wasmplugin.yaml`,
rule 9000), with request-body inspection on for the whole host (rule 9011), so it is already
protected if the allow-list is lifted. Expect to check the WAF for false positives on Slack
payloads when that happens.

## Cluster RBAC
`rbac.yaml` creates a ServiceAccount `oncall-agent` bound (via `oncall-agent-reader-binding`)
to a read-only `ClusterRole` `oncall-agent-reader`: `get/list/watch` on `pods`, `pods/log`,
`pods/status`, `events`, `deployments`, `replicasets` and
`externalsecrets.external-secrets.io` (to verify Vault secret sync), and `get/list` only on
`namespaces` and `services`. There are no write verbs — the agent observes cluster state and
remediates by opening a GitOps PR, not by mutating live resources.

## Integrations
Slack alerting is enabled (`SLACK_ENABLED: "true"`, channel `#oncall-alerts`, minimum
severity `high` in `configmap.yaml`) using the `slack-bot-token`/`slack-signing-secret` from
Vault. Inbound Slack events are paused by the allow-list (see *Exposure*).
`ZEUS_INTEGRATION_ENABLED` is explicitly `"false"` for this homelab deployment.
