# base-apps Index

One row per Argo CD app directory. Doc columns are relative to `base-apps/<app>/`.
Pilot apps carry the full agent-docs contract; others are stubs pending backfill.

| app | purpose | namespace | docs | runbook | catalog |
|---|---|---|---|---|---|
| chores-tracker-backend | FastAPI chores backend (PostgreSQL, Vault, JWT) | chores-tracker | docs.md | runbook.md | catalog-info.yaml |
| vault | In-cluster secret backend (KV v2) | vault | docs.md | runbook.md | catalog-info.yaml |
| argo-cd | GitOps control plane | argo-cd | docs.md | runbook.md | catalog-info.yaml |
| cert-manager | TLS via Let's Encrypt (HTTP-01 via nginx; Route 53 DNS-01 issuer) | cert-manager | docs.md | runbook.md | catalog-info.yaml |
| agent-sandbox-crds | | | | | |
| argo-rollouts | | | | | |
| argo-workflow-tasks | | | | | |
| argo-workflows | | | | | |
| argo-workflows-aws-infrastructure | | | | | |
| atlantis | Terraform/OpenTofu PR automation (Atlantis, GitHub + AWS auth via Vault, Infracost) | atlantis | docs.md | runbook.md | catalog-info.yaml |
| backstage | Internal developer portal / software catalog (Backstage, shared PostgreSQL, Vault, kubernetes-ingestor) | backstage | docs.md | runbook.md | catalog-info.yaml |
| chores-tracker | | | | | |
| chores-tracker-frontend | HTMX/nginx web frontend for Chores Tracker | chores-tracker-frontend | docs.md | runbook.md | catalog-info.yaml |
| cluster-scanner | | | | | |
| coroot | eBPF-based observability/APM (Coroot operator + instance, node/cluster agents, ClickHouse) | coroot | docs.md | runbook.md | catalog-info.yaml |
| crossplane-aws-provider | | | | | |
| crossplane-compositions | | | | | |
| crossplane-functions | | | | | |
| crossplane-system | | | | | |
| ecr-auth | | | | | |
| istio-ambient-config | | | | | |
| k8s-monitor | | | | | |
| kagent | Kubernetes-native AI agent platform (kagent Helm controller, declarative agents, MCP tool servers) | kagent | docs.md | runbook.md | catalog-info.yaml |
| kube-system | | | | | |
| kyverno-policies | | | | | |
| logging | Observability stack (Alloy collector, Loki logs on S3, Prometheus metrics, Grafana) | logging | docs.md | runbook.md | catalog-info.yaml |
| loki-aws-infrastructure | | | | | |
| n8n | Workflow automation platform (shared PostgreSQL, Vault, admin UI + public webhooks) | n8n | docs.md | runbook.md | catalog-info.yaml |
| nginx-ingress | Shared `nginx` IngressClass controller (Rancher HelmChart, DaemonSet, Cloudflare-aware) | ingress-nginx | docs.md | runbook.md | catalog-info.yaml |
| ollama | Local LLM/embedding model server (Ollama, CPU-only, PVC-backed) | ollama | docs.md | runbook.md | catalog-info.yaml |
| oncall-agent | AI on-call/incident-response agent (Anthropic Claude, Slack, GitOps PRs) | oncall-agent | docs.md | runbook.md | catalog-info.yaml |
| oncall-crewai | | | | | |
| openshell | | | | | |
| postgresql | Shared PostgreSQL + pgvector instance (root DB, kagent DB) | postgresql | docs.md | runbook.md | catalog-info.yaml |
| vcluster-sandbox-1 | | | | | |
| weather-kitchen-backend | Backend API for Weather Kitchen (likely FastAPI, JWT, Vault-backed DB) | weather-kitchen | docs.md | runbook.md | catalog-info.yaml |
| weather-kitchen-frontend | Web frontend for Weather Kitchen (nginx-fronted Node build) | weather-kitchen-frontend | docs.md | runbook.md | catalog-info.yaml |
| whoami-test | | | | | |
