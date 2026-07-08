# base-apps Index

One row per Argo CD app directory. Doc columns are relative to `base-apps/<app>/`.
Pilot apps carry the full agent-docs contract; others are stubs pending backfill.

| app | purpose | namespace | docs | runbook | catalog |
|---|---|---|---|---|---|
| chores-tracker-backend | FastAPI chores backend (MySQL, Vault, JWT) | chores-tracker | docs.md | runbook.md | catalog-info.yaml |
| vault | In-cluster secret backend (KV v2) | vault | docs.md | runbook.md | catalog-info.yaml |
| argo-cd | GitOps control plane | argo-cd | docs.md | runbook.md | catalog-info.yaml |
| cert-manager | TLS certs via Route 53 DNS-01 | cert-manager | docs.md | runbook.md | catalog-info.yaml |
| agent-sandbox-crds | | | | | |
| argo-rollouts | | | | | |
| argo-workflow-tasks | | | | | |
| argo-workflows | | | | | |
| argo-workflows-aws-infrastructure | | | | | |
| atlantis | | | | | |
| backstage | | | | | |
| chores-tracker | | | | | |
| chores-tracker-frontend | | | | | |
| cluster-scanner | | | | | |
| coroot | | | | | |
| crossplane-aws-provider | | | | | |
| crossplane-compositions | | | | | |
| crossplane-functions | | | | | |
| crossplane-system | | | | | |
| ecr-auth | | | | | |
| istio-ambient-config | | | | | |
| k8s-monitor | | | | | |
| kagent | | | | | |
| kube-system | | | | | |
| kyverno-policies | | | | | |
| logging | | | | | |
| loki-aws-infrastructure | | | | | |
| n8n | | | | | |
| nginx-ingress | | | | | |
| ollama | | | | | |
| oncall-agent | | | | | |
| oncall-crewai | | | | | |
| openshell | | | | | |
| postgresql | | | | | |
| vcluster-sandbox-1 | | | | | |
| weather-kitchen-backend | | | | | |
| weather-kitchen-frontend | | | | | |
| whoami-test | | | | | |
