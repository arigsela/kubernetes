---
app: oncall-agent
catalog_entity: oncall-agent
kind: docs
namespace: oncall-agent
last_reviewed: 2026-07-10
status: current
tags: [ai-agent, anthropic, incident-response, slack]
sources:
  - base-apps/oncall-agent/deployment.yaml
  - base-apps/oncall-agent/configmap.yaml
  - base-apps/oncall-agent/external-secret.yaml
  - base-apps/oncall-agent/secret-store.yaml
  - base-apps/oncall-agent/incident-memory-pvc.yaml
  - base-apps/oncall-agent/oncall-agent-external-ingress.yaml
  - base-apps/oncall-agent/rbac.yaml
  - base-apps/oncall-agent/namespace.yaml
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
readiness probes.

## Secrets (Vault)
A namespace-local `SecretStore` (`secret-store.yaml`, name `vault-backend`) authenticates to
Vault at `http://vault.vault.svc.cluster.local:8200` (KV v2, path `k8s-secrets`) via the
Kubernetes auth method with Vault role `oncall-agent`. The `ExternalSecret`
(`external-secret.yaml`, `refreshInterval: 15s`) syncs the Vault entry `oncall-agent` into a
K8s Secret `oncall-agent-secrets`, providing: `anthropic-api-key` (required),
`anthropic-model` (optional), `github-token`, `api-keys` (comma-separated API auth keys),
`slack-bot-token` / `slack-signing-secret`, and `tavily-api-key` (optional, used for Tavily
web search by the "desk assistant" feature). The deployment consumes all of these via
`secretKeyRef`.

## Incident memory (persistent state)
`incident-memory-pvc.yaml` provisions a 1Gi `local-path` PVC (`incident-memory-pvc`,
`ReadWriteOnce`) mounted at `/app/data/incidents`, used by the agent as a local LanceDB
vector store of past incidents for retrieval/context. The PVC's own comments call out that
LanceDB's local file storage requires single-writer access — only one replica may ever
write, matching the deployment's `replicas: 1`.

## Exposure
`oncall-agent-external-ingress.yaml` defines an nginx `Ingress` (`ingressClassName: nginx`)
for host `oncall.arigsela.com`, TLS via `cert-manager.io/cluster-issuer: letsencrypt-prod`
(secret `oncall-agent-tls`), with rate limiting (`limit-rps: 30`, `limit-connections: 10`)
and extended proxy timeouts (`proxy-read-timeout`/`proxy-send-timeout: 120`) to accommodate
longer-running LLM calls.

## Cluster RBAC
`rbac.yaml` creates a ServiceAccount `oncall-agent` bound (via `oncall-agent-reader-binding`)
to a read-only `ClusterRole` `oncall-agent-reader`: `get/list/watch` on `pods`, `pods/log`,
`pods/status`, `events`, `deployments`, `replicasets`, `namespaces` (get/list only),
`services`, and `externalsecrets.external-secrets.io` (to verify Vault secret sync). There
are no write verbs — the agent observes cluster state and remediates by opening a GitOps PR,
not by mutating live resources.

## Integrations
Slack alerting is enabled (`SLACK_ENABLED: "true"`, channel `#oncall-alerts`, minimum
severity `high` in `configmap.yaml`) using the `slack-bot-token`/`slack-signing-secret` from
Vault. `ZEUS_INTEGRATION_ENABLED` is explicitly `"false"` for this homelab deployment.
