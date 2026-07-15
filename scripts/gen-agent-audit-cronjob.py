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

The taxonomy is embedded the same way, from the same file Kyverno's policy is
generated from — so "which tools should have been gated" has one definition across
the admission gate, the CI validator, and this alert.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

import yaml

SCRIPT = "scripts/agent-audit.py"
TAXONOMY = "base-apps/kyverno-policies/agent-capability-taxonomy.yaml"
OUT = "base-apps/postgresql/agent-audit-cronjob.yaml"

HEADER = """\
---
# Agent action record — scheduled check (Observability O2).
#
# !!! GENERATED FILE — DO NOT EDIT BY HAND !!!
# Sources:    scripts/agent-audit.py
#             base-apps/kyverno-policies/agent-capability-taxonomy.yaml
# Regenerate: ./scripts/gen-agent-audit-cronjob.py
# CI fails if this file drifts from either source.
#
# WHAT IT WATCHES FOR
#
# A write/destructive tool invoked with NO approval request in its session. That is
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
# names, tool names. Never arguments.
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
# It reads the database as kagent_audit_ro — the SELECT-only role. It cannot mutate
# the evidence it audits. Proven: DELETE/UPDATE/INSERT/DDL are all denied by Postgres.
"""


DB_ENV = [
    {"name": "HOME", "value": "/scratch"},
    {"name": "PIP_CACHE_DIR", "value": "/scratch/.cache"},
    {"name": "AUDIT_USER", "valueFrom": {"secretKeyRef": {
        "name": "kagent-audit-credentials", "key": "audit-user"}}},
    {"name": "AUDIT_PASSWORD", "valueFrom": {"secretKeyRef": {
        "name": "kagent-audit-credentials", "key": "audit-password"}}},
    {"name": "AUDIT_DB", "valueFrom": {"secretKeyRef": {
        "name": "kagent-audit-credentials", "key": "audit-database"}}},
]

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


def _container(name, pip_pkgs, script, extra_env=()):
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
        "env": DB_ENV + list(extra_env),
        "volumeMounts": VOLUME_MOUNTS,
        "resources": {
            "requests": {"cpu": "50m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        },
        "securityContext": SECURITY_CONTEXT,
    }


def _cronjob(name, schedule, container, *, backoff=0, failed_history=7):
    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {"name": name, "namespace": "postgresql", "labels": {"app": "agent-audit"}},
        "spec": {
            "schedule": schedule,
            "concurrencyPolicy": "Forbid",
            "failedJobsHistoryLimit": failed_history,
            "successfulJobsHistoryLimit": 1,
            "jobTemplate": {"spec": {
                "backoffLimit": backoff,
                "template": {
                    "metadata": {"labels": {"app": "agent-audit"}},
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


def build(script_src: str, taxonomy_src: str) -> list[dict]:
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
            "agent-capability-taxonomy.yaml": taxonomy_src,
        },
    }

    # --- the alerting check (O2). Daily; the finding is a historical fact, so a
    # real-time cadence buys nothing. Exits non-zero on a finding, which IS the
    # signal in a cluster with no Alertmanager.
    ungated = _cronjob(
        "agent-audit-ungated", "0 7 * * *",
        _container(
            "agent-audit",
            "'psycopg[binary]==3.2.3' 'pyyaml==6.0.2'",
            "exec python /opt/audit/agent-audit.py --summary "
            "--taxonomy /opt/audit/agent-capability-taxonomy.yaml\n",
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
            "python /opt/audit/agent-audit.py --export --since 25h "
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

    return [code_cm, ungated, export]


def render(repo: Path) -> str:
    script_src = (repo / SCRIPT).read_text()
    taxonomy_src = (repo / TAXONOMY).read_text()
    docs = build(script_src, taxonomy_src)
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
        print("agent-audit CronJob is in sync with agent-audit.py + the taxonomy")
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
