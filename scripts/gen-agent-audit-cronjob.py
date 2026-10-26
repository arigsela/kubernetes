#!/usr/bin/env python3
"""Generate the agent-audit CronJob manifest from the real source files.

    ./scripts/gen-agent-audit-cronjob.py            # regenerate
    ./scripts/gen-agent-audit-cronjob.py --check    # CI: fail if stale

WHY GENERATED — the CronJob has to run scripts/agent-audit.py inside the cluster.
Two ways to get it there: build an image, or ship it in a ConfigMap. An image build
means an ECR push on every edit to a 400-line script, and the repo's one example of
that (cluster-scanner) pins a tag that must be bumped by hand — a drift trap.

So it ships in a ConfigMap. But a hand-copied script in a ConfigMap is a WORSE drift
trap: the audited behaviour would silently diverge from the tested behaviour, and
the redaction logic is exactly the code you cannot afford to have two versions of.

Hence: generate it from the one true file, and let CI fail if the committed manifest
falls behind. Same pattern, and the same reasoning, as
gen-agent-capability-policy.py.

The taxonomy is embedded the same way, from the same file the admission policy is
generated from — so "which tools should have been gated" has one definition across
the admission gate, the CI validator, and this alert. So is the acknowledged file
(scripts/agent-audit-acknowledged.yaml): the triaged findings the alert leaves out.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

import yaml

SCRIPT = "scripts/agent-audit.py"
TAXONOMY = "base-apps/admission-policies/agent-capability-taxonomy.yaml"
ACKNOWLEDGED = "scripts/agent-audit-acknowledged.yaml"
RISK_SCRIPT = "scripts/agent-audit-risk.py"
KPIS_SCRIPT = "scripts/agent-kpis.py"
OUT = "base-apps/postgresql/agent-audit-cronjob.yaml"

HEADER = """\
---
# Agent action record — scheduled check (Observability O2).
#
# !!! GENERATED FILE — DO NOT EDIT BY HAND !!!
# Sources:    scripts/agent-audit.py
#             base-apps/admission-policies/agent-capability-taxonomy.yaml
#             scripts/agent-audit-acknowledged.yaml
#             scripts/agent-audit-risk.py
#             scripts/agent-kpis.py
# Regenerate: ./scripts/gen-agent-audit-cronjob.py
# CI fails if this file drifts from any source.
#
# WHAT IT WATCHES FOR
#
# A write/destructive tool invoked without an approval: on kagent 1.0 judged per
# call (the gate never fired for it, or it ran without an approved decision); on the
# 0.10 archive, no approval request in its session. That is
# not a hypothetical: k8s-agent executed arbitrary shell commands inside the VAULT
# POD, reading /vault/data (Vault's storage backend), 14 times on 2026-07-09, with
# no human approval — because Argo was silently stripping requireApproval on every
# sync. The gate was in the manifest and it was doing nothing. The evidence sat in
# kagent's database for months; nobody could see it because nothing looked.
#
# This is what looks.
#
# WHAT IT EMITS, AND WHAT IT DELIBERATELY DOES NOT
#
# It runs `agent-audit.py --summary`: an ARGUMENT-FREE JSON line — counts, agent
# names, tool names. Never arguments. On kagent 1.0 the line also counts what it
# scanned (events, tool calls, undecodable rows) and is a warning when it could not
# read the record, so a broken audit never reports "ok".
#
# That omission is the design, not an oversight. The full --ungated output carries
# tool ARGUMENTS, and those are only BEST-EFFORT redacted; a secret can hide in a
# `command` string in a shape no pattern matches. That risk is acceptable for a
# human running the tool by hand against the read-only database. It is NOT
# acceptable for a payload that lands in Loki (30d retention) and is read through
# Grafana, because that widens both who can see it and how long it lives.
#
# So the alert tells you WHAT, HOW MANY and WHO — and makes you go and look, behind
# the SELECT-only credential, to learn WITH WHAT.
#
# THE SINK, HONESTLY
#
# This cluster has NO Alertmanager and NO Pushgateway. Prometheus is running with
# `rule_files: []` and an alertmanagers stanza pointing at nothing. There is no
# alerting pipeline to plug into, so this does not pretend to page anyone:
#
#   1. the JSON line goes to stdout -> Grafana Alloy -> Loki (30d), queryable and
#      alertable from Grafana, which does have a Loki datasource;
#   2. the Job EXITS NON-ZERO on findings, so a failed Job in this CronJob's history
#      is itself the signal (`kubectl get jobs -n postgresql`);
#   3. `kubectl logs -n postgresql -l app=agent-audit` shows the finding directly.
#
# Wiring a real Alertmanager is a separate gap and is named as such. Falco has the
# same hole (falcosidekick disabled, detections go to stdout and nothing reads them).
# Fixing both together is the right move; it is not this increment.
#
# RISK TRIAGE (the third CronJob)
#
# agent-audit-risk runs after the export. It re-exports the same 25h window, scores
# every session (rules first, then Jev for sessions with a gated call, a redacted
# argument or a Secret read), uploads the `kind: risk` records next to the export,
# and prints an argument-free summary that the Grafana rule "Agent session scored
# high risk" reads. The redacted arguments of candidate sessions leave the cluster
# for TypeSafe: the operator's egress decision of 2026-10-06, recorded in the
# script header. Exits non-zero on a `high` session, like --ungated, and also on a Jev error
# (jev_errors > 0): a check that did not run must not look healthy.
#
# It reads the database as kagent_audit_ro — the SELECT-only role. It cannot mutate
# the evidence it audits. Proven: DELETE/UPDATE/INSERT/DDL are all denied by Postgres.
#
# AGENT KPIs (the fifth CronJob, §T.144)
#
# agent-kpis runs at :05 every hour and prints the clock hour that just ended as
# argument-free JSON lines (check=agent-kpi): how each agent task ended, how long each
# gated call waited for its decision, which pull requests merged and how long they
# took (GitHub's public API, unauthenticated, like the dispatcher's open-PR count),
# and an hourly roll-up. The Agent KPIs dashboard reads them from Loki
# ({namespace="postgresql", app="agent-kpis"}). Its own pod label keeps them out of
# the audit's Grafana rules. No retries: a retry after a partial print would count
# the hour twice. A pod that cannot start within 50 minutes is skipped
# (startingDeadlineSeconds), so it can never print the next run's hour. Each pod
# waits after printing: Alloy tails only Running pods, and a pod that exits at once
# can lose its last lines. Same SELECT-only role, same mounted taxonomy, never an
# argument.
#
# agent-kpis-backfill is the same script over the 168 hours before the earliest hour
# the hourly job printed, SUSPENDED; run it once after the first deploy so the
# dashboard opens on a week. It asks Loki what is already printed first, so it never
# prints an hour twice: it refuses after a previous backfill, when Loki does not
# answer, and between :00 and :10 (exit 2, nothing printed).
#
#   kubectl create job -n postgresql --from=cronjob/agent-kpis-backfill agent-kpis-backfill-1
#
# WHICH DATABASE
#
# The three scheduled jobs read kagent 1.0 (`--source v1`: database kagent_v1, key
# audit-database-v1, schema kagent). kagent 0.10's database (`kagent`, key
# audit-database) is a read-only archive since the cutover; nothing new lands in it,
# so it is not scanned on a schedule. agent-audit-archive below is the same --summary
# against it, SUSPENDED, with its own pod label so it never feeds the Grafana rules.
# Run it on demand:
#
#   kubectl create job -n postgresql --from=cronjob/agent-audit-archive agent-audit-archive-manual
#   kubectl logs -n postgresql -l app=agent-audit-archive --tail=1
#
# or from a laptop: `scripts/agent-audit.py --source v010 --ungated` with the DSN in
# that script's connect() help pointed at the archive database.
"""


def db_env(database_key: str) -> list[dict]:
    """The SELECT-only credential, pointed at one of the two databases."""
    return [
        {"name": "HOME", "value": "/scratch"},
        {"name": "PIP_CACHE_DIR", "value": "/scratch/.cache"},
        {"name": "AUDIT_USER", "valueFrom": {"secretKeyRef": {
            "name": "kagent-audit-credentials", "key": "audit-user"}}},
        {"name": "AUDIT_PASSWORD", "valueFrom": {"secretKeyRef": {
            "name": "kagent-audit-credentials", "key": "audit-password"}}},
        {"name": "AUDIT_DB", "valueFrom": {"secretKeyRef": {
            "name": "kagent-audit-credentials", "key": database_key}}},
    ]


V1_DB = "audit-database-v1"       # kagent 1.0: database kagent_v1
ARCHIVE_DB = "audit-database"     # kagent 0.10: database kagent, read-only archive

SECURITY_CONTEXT = {
    "allowPrivilegeEscalation": False,
    "runAsNonRoot": True,
    "runAsUser": 1000,
    "capabilities": {"drop": ["ALL"]},
}

VOLUME_MOUNTS = [
    {"name": "code", "mountPath": "/opt/audit", "readOnly": True},
    {"name": "scratch", "mountPath": "/scratch"},
]

VOLUMES = [
    {"name": "code", "configMap": {"name": "agent-audit-code"}},
    # writable scratch for pip + HOME; the root filesystem stays untouched. A plain
    # `pip install` dies on a non-writable HOME under runAsNonRoot.
    {"name": "scratch", "emptyDir": {"sizeLimit": "256Mi"}},
]


def _container(name, pip_pkgs, script, extra_env=(), database_key=V1_DB):
    return {
        "name": name,
        "image": "python:3.12-slim",
        "command": ["/bin/sh", "-c"],
        "args": [
            "set -eu\n"
            f"pip install --quiet --no-cache-dir --target=/scratch/pylib {pip_pkgs}\n"
            "export PYTHONPATH=/scratch/pylib\n"
            "export AGENT_AUDIT_DSN="
            "\"postgresql://${AUDIT_USER}:${AUDIT_PASSWORD}"
            "@postgresql.postgresql.svc.cluster.local:5432/${AUDIT_DB}\"\n"
            + script
        ],
        "env": db_env(database_key) + list(extra_env),
        "volumeMounts": VOLUME_MOUNTS,
        "resources": {
            "requests": {"cpu": "50m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        },
        "securityContext": SECURITY_CONTEXT,
    }


def _cronjob(name, schedule, container, *, backoff=0, failed_history=7, app="agent-audit",
             suspend=False, extra_spec=None):
    spec = {"suspend": True} if suspend else {}
    spec.update(extra_spec or {})
    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {"name": name, "namespace": "postgresql", "labels": {"app": app}},
        "spec": {
            **spec,
            "schedule": schedule,
            "concurrencyPolicy": "Forbid",
            "failedJobsHistoryLimit": failed_history,
            "successfulJobsHistoryLimit": 1,
            "jobTemplate": {"spec": {
                "backoffLimit": backoff,
                "template": {
                    "metadata": {"labels": {"app": app}},
                    "spec": {
                        "restartPolicy": "Never",
                        "nodeSelector": {"node.kubernetes.io/workload": "application"},
                        "containers": [container],
                        "volumes": VOLUMES,
                    },
                },
            }},
        },
    }


def build(script_src: str, taxonomy_src: str, acknowledged_src: str, risk_src: str,
          kpis_src: str = "") -> list[dict]:
    code_cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "agent-audit-code",
            "namespace": "postgresql",
            "labels": {"app": "agent-audit"},
        },
        "data": {
            "agent-audit.py": script_src,
            "agent-audit-risk.py": risk_src,
            "agent-kpis.py": kpis_src,
            "agent-capability-taxonomy.yaml": taxonomy_src,
            "agent-audit-acknowledged.yaml": acknowledged_src,
        },
    }

    # --- the alerting check (O2). Daily; the finding is a historical fact, so a
    # real-time cadence buys nothing. Exits non-zero on a finding, which IS the
    # signal in a cluster with no Alertmanager. Triaged findings listed in the
    # acknowledged file are counted but do not alert; anything else still does.
    ungated = _cronjob(
        "agent-audit-ungated", "0 7 * * *",
        _container(
            "agent-audit",
            "'psycopg[binary]==3.2.3' 'pyyaml==6.0.2'",
            "exec python /opt/audit/agent-audit.py --source v1 --summary "
            "--taxonomy /opt/audit/agent-capability-taxonomy.yaml "
            "--acknowledged /opt/audit/agent-audit-acknowledged.yaml\n",
        ),
    )

    # --- the durable export (O4). Writes the REDACTED record to S3, append-only,
    # date-partitioned. Uses a WRITE-ONLY IAM credential (s3:PutObject only) — it
    # cannot read the accumulated history back or delete it. Runs after midnight
    # and exports a 25h window (1h overlap absorbs clock skew; the store is
    # append-only and keyed by run date, so overlap is harmless).
    export = _cronjob(
        "agent-audit-export", "30 1 * * *",
        _container(
            "agent-audit-export",
            "'psycopg[binary]==3.2.3' 'pyyaml==6.0.2' 'boto3==1.35.71'",
            # write the redacted JSONL, then PutObject it under dt=YYYY-MM-DD/.
            # KEY is exported so the boto3 one-liner sees it via os.environ.
            "export KEY=\"dt=$(date -u +%Y-%m-%d)/$(date -u +%H%M%S).jsonl\"\n"
            "python /opt/audit/agent-audit.py --source v1 --export --since 25h "
            "> /scratch/record.jsonl\n"
            "echo \"exporting $(wc -l < /scratch/record.jsonl) records to ${KEY}\"\n"
            "python -c \"import boto3,os;"
            "boto3.client('s3',"
            "aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],"
            "aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],"
            "region_name='us-east-1')"
            ".upload_file('/scratch/record.jsonl',"
            "'asela-agent-audit-record', os.environ['KEY'])\"\n"
            "echo \"uploaded s3://asela-agent-audit-record/${KEY}\"\n",
            extra_env=[
                # The Upbound IAM AccessKey connection secret keys, confirmed
                # against the live argo-workflows-s3-creds secret: `username` holds
                # the ACCESS KEY ID (AKIA...), `attribute.secret` holds the secret.
                # There is no `attribute.id` key — do not reintroduce it.
                {"name": "AWS_ACCESS_KEY_ID", "valueFrom": {"secretKeyRef": {
                    "name": "agent-audit-s3-creds", "key": "username"}}},
                {"name": "AWS_SECRET_ACCESS_KEY", "valueFrom": {"secretKeyRef": {
                    "name": "agent-audit-s3-creds", "key": "attribute.secret"}}},
            ],
        ),
        backoff=2,          # upload is retryable, unlike a finding
        failed_history=3,
    )

    # --- risk triage. After the export so a failed Jev day never blocks the
    # durable copy. Re-reads the same 25h window with the SELECT-only role, scores
    # it, uploads `-risk.jsonl` beside the export, prints the summary line. backoff
    # 0: a `high` verdict exits 1 on purpose and must not be retried (each retry
    # would bill Jev again and emit a second summary line).
    risk = _cronjob(
        "agent-audit-risk", "30 2 * * *",
        _container(
            "agent-audit-risk",
            "'psycopg[binary]==3.2.3' 'pyyaml==6.0.2' 'boto3==1.35.71'",
            "export KEY=\"dt=$(date -u +%Y-%m-%d)/$(date -u +%H%M%S)-risk.jsonl\"\n"
            "python /opt/audit/agent-audit.py --source v1 --export --since 25h "
            "> /scratch/record.jsonl\n"
            "set +e\n"
            "python /opt/audit/agent-audit-risk.py --records /scratch/record.jsonl "
            "--out /scratch/risk.jsonl --taxonomy /opt/audit/agent-capability-taxonomy.yaml "
            "--max-sessions 50\n"
            "RC=$?\n"
            "set -e\n"
            # No risk.jsonl means a usage error: exit with the script's own code, not an upload traceback.
            "[ -s /scratch/risk.jsonl ] || exit $RC\n"
            "echo \"uploading $(wc -l < /scratch/risk.jsonl) risk records to ${KEY}\" >&2\n"
            "python -c \"import boto3,os;"
            "boto3.client('s3',"
            "aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],"
            "aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],"
            "region_name='us-east-1')"
            ".upload_file('/scratch/risk.jsonl',"
            "'asela-agent-audit-record', os.environ['KEY'])\"\n"
            "echo \"uploaded s3://asela-agent-audit-record/${KEY}\" >&2\n"
            "exit $RC\n",
            extra_env=[
                {"name": "TYPESAFE_API_KEY", "valueFrom": {"secretKeyRef": {
                    "name": "agent-audit-risk-credentials", "key": "typesafe-api-key"}}},
                {"name": "AWS_ACCESS_KEY_ID", "valueFrom": {"secretKeyRef": {
                    "name": "agent-audit-s3-creds", "key": "username"}}},
                {"name": "AWS_SECRET_ACCESS_KEY", "valueFrom": {"secretKeyRef": {
                    "name": "agent-audit-s3-creds", "key": "attribute.secret"}}},
            ],
        ),
        backoff=0,
        failed_history=7,
    )

    # --- the kagent 0.10 archive, on demand only (see the header). Same check as
    # agent-audit-ungated against the read-only 0.10 database. Suspended: nothing new
    # lands there. Its own pod label keeps its summary line out of the Grafana rules,
    # which select app=agent-audit and must only ever see kagent 1.0's result.
    archive = _cronjob(
        "agent-audit-archive", "0 7 * * *",
        _container(
            "agent-audit-archive",
            "'psycopg[binary]==3.2.3' 'pyyaml==6.0.2'",
            "exec python /opt/audit/agent-audit.py --source v010 --summary "
            "--taxonomy /opt/audit/agent-capability-taxonomy.yaml "
            "--acknowledged /opt/audit/agent-audit-acknowledged.yaml\n",
            database_key=ARCHIVE_DB,
        ),
        app="agent-audit-archive",
        suspend=True,
        failed_history=1,
    )

    # --- the agent KPIs (§T.144). Hourly at :05: the clock hour that just ended, so runs
    # neither overlap nor leave gaps. backoff 0: a retry after a partial print would count
    # the hour twice. Its own app label: the audit's Grafana rules read app=agent-audit.
    def kpi_cmd(extra, drain):
        # Not exec: the pod waits after printing so Alloy (which tails Running pods only)
        # reads every line before the pod completes, then exits with the script's code.
        return ("set +e\n"
                "python /opt/audit/agent-kpis.py --taxonomy /opt/audit/agent-capability-taxonomy.yaml"
                f"{extra}\n"
                "RC=$?\n"
                f"sleep {drain}\n"
                "exit $RC\n")
    kpi_pkgs = "'psycopg[binary]==3.2.3' 'pyyaml==6.0.2'"
    kpi_spec = {"timeZone": "Etc/UTC", "startingDeadlineSeconds": 3000}
    kpis = _cronjob("agent-kpis", "5 * * * *", _container("agent-kpis", kpi_pkgs, kpi_cmd("", 30)),
                    backoff=0, failed_history=3, app="agent-kpis", extra_spec=kpi_spec)
    # The 168 hours before the earliest hour the hourly job printed, on demand only (see the
    # header). Same app label: its lines are the dashboard's first week.
    kpis_backfill = _cronjob(
        "agent-kpis-backfill", "5 * * * *",
        _container("agent-kpis-backfill", kpi_pkgs, kpi_cmd(" --backfill-hours 168", 120)),
        backoff=0, failed_history=1, app="agent-kpis", suspend=True, extra_spec=kpi_spec)

    return [code_cm, ungated, export, risk, archive, kpis, kpis_backfill]


def render(repo: Path) -> str:
    script_src = (repo / SCRIPT).read_text()
    taxonomy_src = (repo / TAXONOMY).read_text()
    acknowledged_src = (repo / ACKNOWLEDGED).read_text()
    risk_src = (repo / RISK_SCRIPT).read_text()
    kpis_src = (repo / KPIS_SCRIPT).read_text()
    docs = build(script_src, taxonomy_src, acknowledged_src, risk_src, kpis_src)
    body = "\n---\n".join(
        yaml.safe_dump(d, sort_keys=False, width=10_000, default_flow_style=False)
        for d in docs
    )
    return HEADER + body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the committed manifest is stale")
    args = ap.parse_args(argv)

    want = render(args.repo_root)
    path = args.repo_root / OUT

    if not args.check:
        path.write_text(want)
        print(f"wrote {OUT}")
        return 0

    have = path.read_text() if path.exists() else ""
    if have == want:
        print("agent-audit CronJob is in sync with agent-audit.py, agent-audit-risk.py, "
              "agent-kpis.py, the taxonomy and the acknowledged findings")
        return 0

    print(f"{OUT} is STALE — regenerate with ./scripts/gen-agent-audit-cronjob.py",
          file=sys.stderr)
    sys.stderr.writelines(difflib.unified_diff(
        have.splitlines(True), want.splitlines(True),
        fromfile="committed", tofile="generated",
    ))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
