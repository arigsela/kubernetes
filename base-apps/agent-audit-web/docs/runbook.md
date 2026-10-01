---
type: "Kubernetes App Runbook"
title: "Agent Audit Web — Runbook"
description: "Operational runbook for agent-audit-web: failure modes, checks, and fixes."
app: agent-audit-web
catalog_entity: agent-audit-web
kind: runbook
namespace: agent-audit
last_reviewed: 2026-09-27
status: stable
tags: [audit, agents, fastapi, oauth2-proxy]
sources:
  - base-apps/agent-audit-web/deployment.yaml
  - base-apps/agent-audit-web/external-secrets.yaml
  - base-apps/agent-audit-aws-infrastructure/web-s3-read-key.yaml
  - base-apps/postgresql/init-agent-audit-web-role.yaml
  - base-apps/dex/configmap.yaml
---

# Agent Audit Web — Runbook

## Failure modes

### Symptom: Dex says `Unregistered redirect_uri` or `invalid client`
- **Check:** `kubectl -n dex get deploy dex -o jsonpath='{.spec.template.metadata.annotations.checksum/config}'` against the value the comment in `base-apps/dex/deployment.yaml` tells you to compute; `kubectl -n dex logs deploy/dex --since=10m`.
- **Fix:** the `agent-audit` client is missing from `base-apps/dex/configmap.yaml`, or the config changed without bumping `checksum/config` (Dex reads its config only at start). Fix it in a PR.

### Symptom: login works, then oauth2-proxy returns 403
- **Check:** `kubectl -n agent-audit logs deploy/agent-audit-web -c oauth2-proxy | grep -i -E "permission|unauthorized|email"`.
- **Fix:** the email Dex got from GitHub (the account's primary, verified email) is not the value in Vault `k8s-secrets/agent-audit-web` → `allowed-emails`. Correct it in Vault, then force a refresh: `kubectl -n agent-audit annotate externalsecret agent-audit-web-oauth2 force-sync=$(date +%s) --overwrite`. Kubelet refreshes the mounted file in about a minute.

### Symptom: banner "Live database unavailable — showing archive only"
- **Check:** `kubectl -n agent-audit logs deploy/agent-audit-web -c app | grep "database query failed"` (it logs only the error class); `kubectl -n agent-audit get externalsecret agent-audit-web-db`; in `postgresql`, the `init-agent-audit-web-role` hook result on the Argo app.
- **Fix:** a wrong Vault key or role (the ExternalSecret is not `SecretSynced`), or the role/password drifted - re-run the Sync hook by syncing the `postgresql` app. Tables kagent creates or recreates later stay readable: the init Job sets default privileges `FOR ROLE` the database owner (`kagent`), which is what creates them.

### Symptom: banner "Archive unavailable — showing live database only"
- **Check:** `kubectl -n agent-audit logs deploy/agent-audit-web -c app | grep "archive refresh"`; `kubectl get accesskey.iam.aws.upbound.io agent-audit-web-s3-read-key`; `kubectl -n agent-audit get secret agent-audit-web-s3-creds`.
- **Fix:** the Crossplane AccessKey or the S3 read policy (`base-apps/agent-audit-aws-infrastructure/web-s3-read*.yaml`). Since v0.1.1 a bucket that can be listed but not read also lands here (archive down; the log says `archive refresh: N files listed, none readable: <ErrorClass>`) instead of passing for a healthy, empty archive.

### Symptom: `ImagePullBackOff` on a first deploy or a new namespace
- **Check:** `kubectl -n agent-audit get secret ecr-registry`.
- **Fix:** none needed - the `ecr-auth` app's CronJob `kube-system/ecr-credentials-sync` copies `ecr-registry` into every namespace every 15 minutes. Wait.

## How-to

### Release a new version
Tag `vX.Y.Z` in `arigsela/agent-audit-web`; the `release` workflow prints `…/agent-audit-web:vX.Y.Z@sha256:…`. Put that exact reference into `deployment.yaml` in a PR.

### Rotate the oauth2 client secret
Set `k8s-secrets/dex` → `agent-audit-client-secret` and `k8s-secrets/agent-audit-web` → `oauth2-client-secret` to the same new value. Force both ExternalSecrets to refresh (`dex-secrets` in `dex`, `agent-audit-web-oauth2` here), then restart Dex via a PR that adds or bumps a **separate** pod-template annotation on `base-apps/dex/deployment.yaml` (for example `rotation/restarted-at: "<date>"`) - Dex reads env only at start. Do not hand-edit `checksum/config`: it must equal the hash of `config.yaml`, and the first failure mode above checks exactly that.

### Revoke access fast
Remove the email from Vault `allowed-emails` and force-sync as above. For a full stop, delete the `agent-audit.arigsela.com` rule from `base-apps/istio-ingress/authorizationpolicy.yaml` in a PR (the gateway then denies the host).

### Retire the app
Remove the `init-agent-audit-web-role` Job **before** deleting the Vault key `agent-audit-web-db`; otherwise every `postgresql` sync hangs on the hook waiting for a Secret ESO can no longer create.
