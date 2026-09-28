# ADP Hardening — Resources & Observability Reference

A review index for the Agentic Development Platform hardening work: the AWS
resources it created, the dashboards to observe it in, and the tools it uses.
Pulled live from the cluster + Crossplane on 2026-07-15.

- **AWS account:** `852893458518`
- **Region:** `us-east-1`
- **Managed by:** Argo CD + Crossplane from `base-apps/` (delete the console
  resource and Argo recreates it — to remove, delete the manifests).

---

## 1. AWS resources created for this work

The **O4 durable-export** increment and the **agent-audit-web** UI touched AWS.
Everything else — Identity, Security/Capability, Observability O1–O3, Evaluation —
lives entirely in the cluster with no AWS footprint.

| Resource | Name / ARN | Console |
|---|---|---|
| S3 bucket | `asela-agent-audit-record` — versioned, public-access blocked, STANDARD_IA @90d, expire @730d | https://us-east-1.console.aws.amazon.com/s3/buckets/asela-agent-audit-record |
| IAM user | `agent-audit-s3-user` (path `/serviceaccounts/`) | https://us-east-1.console.aws.amazon.com/iam/home#/users/details/agent-audit-s3-user |
| IAM policy | `agent-audit-s3-write` — **`s3:PutObject` only** (write-only, append-only) | https://us-east-1.console.aws.amazon.com/iam/home#/policies/arn:aws:iam::852893458518:policy/agent-audit-s3-write |
| Access key | `AKIA4NFDJMBLDJBXV5EP` (the write-only exporter key ID) | on the IAM user page |
| IAM user | `agent-audit-web-s3-read` (path `/serviceaccounts/`) — agent-audit-web's reader | https://us-east-1.console.aws.amazon.com/iam/home#/users/details/agent-audit-web-s3-read |
| IAM policy | `agent-audit-web-s3-read` — **`s3:ListBucket` + `s3:GetObject` only** on this bucket | https://us-east-1.console.aws.amazon.com/iam/home#/policies/arn:aws:iam::852893458518:policy/agent-audit-web-s3-read |
| IAM role | `github-actions-agent-audit-web-ecr` — GitHub OIDC, `v*` tags of `arigsela/agent-audit-web` only; policy `agent-audit-web-ecr-push` (push to one repo) | https://us-east-1.console.aws.amazon.com/iam/home#/roles/details/github-actions-agent-audit-web-ecr |
| ECR repository | `agent-audit-web` (us-east-2, immutable tags, scan on push) — **created by hand**, not in git | https://us-east-2.console.aws.amazon.com/ecr/repositories/private/852893458518/agent-audit-web |
| Route 53 record | `agent-audit.arigsela.com` A → the WAN IP — **created by hand**; kept current by `wan-ip-monitor` | https://us-east-1.console.aws.amazon.com/route53/v2/hostedzones#ListRecordSets/Z0524483LR4JCFNLS7N0 |

Source of truth: `base-apps/agent-audit-aws-infrastructure/` (except the two hand-made rows).

**In the bucket:** redacted JSONL records — safe to open.
- `dt=YYYY-MM-DD/HHMMSS.jsonl` — the daily export
- `dt=backfill/2026-07-15-full-history.jsonl` — the 950-record backfill

> ⚠️ The exporter key is a **write-only** identity: it can `PutObject` and nothing
> else — it cannot list, read, or delete. To review the bucket contents, use your
> own console session, not that key.

---

## 2. Dashboards — what to look at for this work

| Service | URL | Relevance |
|---|---|---|
| **Grafana** | https://grafana.arigsela.com | Agent alert rules, Falco detections, all agent logs (via Loki). Most relevant. |
| **Coroot** | https://coroot.arigsela.com | Agent traces (kagent OTel spans), service maps. |
| **kagent UI** | https://kagent.arigsela.com | The agents themselves — chat, sessions. |
| **n8n** | https://n8n.arigsela.com | The alert-delivery workflow (`grafana-alerts` webhook). |
| **Agent audit** | https://agent-audit.arigsela.com | The agent action record: findings, calls, session timelines, token trends (read-only, redacted, SSO). |
| **Argo CD** | https://argocd.arigsela.com | Every app we created: `kagent`, `admission-policies`, `agent-audit-aws-infrastructure`, … |

### Grafana → Explore → Loki, three queries to try

```logql
# Falco runtime detections (previously silent; un-muted in O3)
{namespace="falco"} | json | rule=~".+"

# Every agent tool call
{namespace="kagent"} |= "function_call"

# The scheduled agent-audit finding
{namespace="postgresql", app="agent-audit"} | json
```

---

## 3. The agent action record (no UI yet — observe via CLI)

```bash
# the two scheduled jobs
kubectl get cronjob -n postgresql | grep agent-audit
#   agent-audit-export    30 1 * * *   (daily → S3)
#   agent-audit-ungated   0 7 * * *    (daily → alert on ungated tool use)

# last run's findings
kubectl logs -n postgresql -l app=agent-audit --tail=20

# the admission contracts are live at Enforce
kubectl get cpol agent-identity agent-capability
#   agent-identity     Enforce
#   agent-capability   Enforce
```

For browsing, use https://agent-audit.arigsela.com; the CLI below is still the way to script it.

Run the audit tool by hand (read-only `kagent_audit_ro` role):

```bash
kubectl port-forward -n postgresql svc/postgresql 5432:5432 &
U=$(kubectl get secret kagent-audit-credentials -n postgresql -o jsonpath='{.data.audit-user}' | base64 -d)
P=$(kubectl get secret kagent-audit-credentials -n postgresql -o jsonpath='{.data.audit-password}' | base64 -d)
export AGENT_AUDIT_DSN="postgresql://$U:$P@127.0.0.1:5432/kagent"

python scripts/agent-audit.py --ungated   # gated tools invoked with no approval
python scripts/agent-audit.py --cost       # per-agent token spend
```

---

## 4. Tools used — project & docs links

| Tool | Role here | Link |
|---|---|---|
| kagent | Runs the AI agents on Kubernetes | https://kagent.dev |
| Kyverno | Enforces the identity & capability contracts at admission | https://kyverno.io |
| Falco | Runtime threat detection (un-muted in O3) | https://falco.org |
| Coroot | eBPF traces/metrics/logs; agent telemetry sink | https://coroot.com |
| External Secrets Operator | Materializes the scoped Vault credentials | https://external-secrets.io |
| Crossplane | Provisions the AWS S3/IAM from Git | https://crossplane.io |
| Vault | Holds every scoped secret | https://developer.hashicorp.com/vault |
| Grafana / Loki | Dashboards + log store the alerts query | https://grafana.com/oss/loki |
| n8n | Alert delivery / fan-out | https://n8n.io |

---

## Where each pillar's code lives (for the reviewer)

| Pillar | Path |
|---|---|
| Identity | `base-apps/kagent/*secret-store*.yaml`, `templates/agent-identity/`, `scripts/validate-agent-identity.py` |
| Security / Capability | `base-apps/admission-policies/agent-capability*.yaml`, `scripts/gen-agent-capability-policy.py`, `scripts/validate-agent-capability.py` |
| Observability | `base-apps/postgresql/agent-audit-cronjob.yaml`, `base-apps/agent-audit-aws-infrastructure/`, `base-apps/logging/grafana-alerting.yaml`, `scripts/agent-audit.py` |
| Evaluation | `tests/eval-corpus/`, `scripts/mine-eval-corpus.py`, `scripts/validate-eval-corpus.py`, `scripts/score-eval.py` |
| Roadmap / specs | `docs/superpowers/specs/2026-07-14-adp-remaining-pillars-roadmap.md` |
