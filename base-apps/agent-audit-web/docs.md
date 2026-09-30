---
type: "Kubernetes App Guide"
title: "Agent Audit Web"
description: "Read-only web UI over the redacted agent action record (findings, calls, sessions, token trends)"
app: agent-audit-web
catalog_entity: agent-audit-web
kind: docs
namespace: agent-audit
last_reviewed: 2026-09-30
status: current
tags: [audit, agents, fastapi, oauth2-proxy]
sources:
  - base-apps/agent-audit-web.yaml
  - base-apps/agent-audit-web/deployment.yaml
  - base-apps/agent-audit-web/service.yaml
  - base-apps/agent-audit-web/httproute.yaml
  - base-apps/agent-audit-web/reference-grant.yaml
  - base-apps/agent-audit-web/certificate.yaml
  - base-apps/agent-audit-web/serviceaccounts.yaml
  - base-apps/agent-audit-web/secret-stores.yaml
  - base-apps/agent-audit-web/external-secrets.yaml
  - base-apps/agent-audit-aws-infrastructure/web-s3-read.yaml
  - base-apps/agent-audit-aws-infrastructure/web-s3-read-key.yaml
  - base-apps/agent-audit-aws-infrastructure/web-ecr-push.yaml
  - base-apps/postgresql/init-agent-audit-web-role.yaml
  - base-apps/postgresql/external-secrets-agent-audit-web-db.yaml
  - base-apps/dex/configmap.yaml
---

# agent-audit-web

## What it is
A private, read-only web UI over kagent's **agent action record**: which agent called
which tool, with what (redacted) arguments, and whether a human approval was
requested. It shows findings (write/destructive tools run in a session with no
approval request), every tool call, one session's timeline in order, a per-agent
summary and daily token trends. It reads the live kagent database and the daily S3
export, and it **never shows a tool response body** - only its size and a hash. It
exists because the record was previously visible only through
`scripts/agent-audit.py` over a port-forward, and the ungated-tool alert deliberately
carries no arguments. The app's code lives in
[`arigsela/agent-audit-web`](https://github.com/arigsela/agent-audit-web) (private).

## Architecture & data flow
```
browser ─TLS─▶ Istio gateway `main` (per-host IP allow-list: gateway-allow)
        ─▶ HTTPRoute agent-audit.arigsela.com ─▶ Service :4180
        ─▶ Pod (namespace agent-audit)
             ├─ oauth2-proxy :4180   OIDC via Dex (GitHub), PKCE, one-email allow-list
             │     └─▶ upstream http://127.0.0.1:8000
             └─ app (uvicorn/FastAPI) bound to 127.0.0.1:8000 ONLY
                   ├─▶ postgresql.postgresql.svc:5432  db kagent, role kagent_audit_web_ro
                   │     (SELECT-only; read-only transactions; short statement timeout)
                   └─▶ S3 s3://asela-agent-audit-record  IAM user agent-audit-web-s3-read
                         (s3:ListBucket + s3:GetObject on this bucket only)
```

- **Two layers of access control.** The gateway's IP allow-list (`base-apps/istio-ingress/authorizationpolicy.yaml`) and then oauth2-proxy. Dex admits any GitHub account, so oauth2-proxy's `authenticated-emails-file` - one email, from Vault, never in git - is the real allow-list.
- **The app has no authentication of its own.** Its image CMD binds `127.0.0.1:8000`, so the sidecar is the only way in; the Service exposes only `4180`. Do not add `command:`/`args:` to the app container: the CMD also carries `--no-access-log`.
- **Probes are `exec`**, because kubelet cannot reach a loopback port. `/readyz` is 200 while the database **or** the archive works, so one source going down degrades the pages (a banner) instead of taking the app out of the Service.
- **Sessions deleted from kagent** still appear from the S3 archive, labelled "archive only". The database wins per session.

## Where config lives
- **Image:** pinned `tag@digest` in `deployment.yaml`, released by the app repo's `release.yml` on a `v*` tag (GitHub OIDC → role `github-actions-agent-audit-web-ecr`, `base-apps/agent-audit-aws-infrastructure/web-ecr-push.yaml`). ECR repository `agent-audit-web` (us-east-2, immutable tags) was created by hand.
- **Vault** (`k8s-secrets`, roles/policies created by hand):
  - `agent-audit-web` - `oauth2-client-secret`, `oauth2-cookie-secret`, `allowed-emails`. Role `agent-audit-web` → ESO SA `eso-agent-audit-web` in `agent-audit`.
  - `agent-audit-web-db` - `db-user`, `db-password`, `db-name`. Role `agent-audit-web-db` → ESO SA `eso-agent-audit-web-db` in `agent-audit` **and** `postgresql` (the init Job).
  - `dex` / `agent-audit-client-secret` - the same client secret as `oauth2-client-secret`; rotate both together.
- **Database role** `kagent_audit_web_ro`: `base-apps/postgresql/init-agent-audit-web-role.yaml`, derived from `init-kagent-audit-role.yaml` (identical SQL).
- **S3 reader:** `web-s3-read.yaml` (user, policy, attachment) and `web-s3-read-key.yaml` (the AccessKey, written into this namespace as `agent-audit-web-s3-creds`).
- **Dex client** `agent-audit` (confidential + PKCE): `base-apps/dex/configmap.yaml`.
- **DNS:** A record `agent-audit.arigsela.com` (created by hand), kept current on WAN-IP rotation by `base-apps/wan-ip-monitor` (`MANAGED_HOSTNAMES`).

## Redaction
The app contains no redaction logic of its own: it vendors a byte-identical copy of
`scripts/agent-audit.py` (and the capability taxonomy) pinned to a commit of this
repo. **Changing `scripts/agent-audit.py` means re-vendoring in the app repo**
(`scripts/vendor_sync.py --latest`) and releasing. The app repo's daily
`upstream-drift` workflow opens an issue when this repo's copy moves ahead.
