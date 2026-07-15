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

Only the **O4 durable-export** increment touched AWS. Everything else — Identity,
Security/Capability, Observability O1–O3, Evaluation — lives entirely in the
cluster with no AWS footprint.

| Resource | Name / ARN | Console |
|---|---|---|
| S3 bucket | `asela-agent-audit-record` — versioned, public-access blocked, STANDARD_IA @90d, expire @730d | https://us-east-1.console.aws.amazon.com/s3/buckets/asela-agent-audit-record |
| IAM user | `agent-audit-s3-user` (path `/serviceaccounts/`) | https://us-east-1.console.aws.amazon.com/iam/home#/users/details/agent-audit-s3-user |
| IAM policy | `agent-audit-s3-write` — **`s3:PutObject` only** (write-only, append-only) | https://us-east-1.console.aws.amazon.com/iam/home#/policies/arn:aws:iam::852893458518:policy/agent-audit-s3-write |
| Access key | `AKIA4NFDJMBLDJBXV5EP` (the write-only exporter key ID) | on the IAM user page |

Source of truth: `base-apps/agent-audit-aws-infrastructure/`.

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
| **Argo CD** | https://argocd.arigsela.com | Every app we created: `kagent`, `kyverno-policies`, `agent-audit-aws-infrastructure`, … |

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
| Security / Capability | `base-apps/kyverno-policies/agent-capability*.yaml`, `scripts/gen-agent-capability-policy.py`, `scripts/validate-agent-capability.py` |
| Observability | `base-apps/postgresql/agent-audit-cronjob.yaml`, `base-apps/agent-audit-aws-infrastructure/`, `base-apps/logging/grafana-alerting.yaml`, `scripts/agent-audit.py` |
| Evaluation | `tests/eval-corpus/`, `scripts/mine-eval-corpus.py`, `scripts/validate-eval-corpus.py`, `scripts/score-eval.py` |
| Roadmap / specs | `docs/superpowers/specs/2026-07-14-adp-remaining-pillars-roadmap.md` |
