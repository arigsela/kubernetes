---
type: "Kubernetes App Runbook"
title: "hk demo — Runbook"
description: "Operational runbook for hk-demo: login, agent calls, missing evidence, releases and access."
app: hk-demo
catalog_entity: hk-demo
kind: runbook
namespace: hk-demo
last_reviewed: 2026-10-22
status: stable
tags: [agents, observability, fastapi, oauth2-proxy]
sources:
  - base-apps/hk-demo/deployment.yaml
  - base-apps/hk-demo/external-secrets.yaml
  - base-apps/hk-demo/namespace.yaml
  - base-apps/dex/configmap.yaml
  - base-apps/dex/deployment.yaml
  - base-apps/kagent/network-policy-controller.yaml
  - scripts/provision-hk-demo-vault.sh
---

# hk demo — Runbook

Logs: `kubectl -n hk-demo logs deploy/hk-demo -c app` (one JSON line per run phase, event
`hk-demo-run`) and `-c oauth2-proxy`.

## Dex says "invalid client" or "Unregistered redirect_uri"

- Check: `base-apps/dex/deployment.yaml` `checksum/config` matches the config
  (the command is in its comment); `kubectl -n dex logs deploy/dex | grep -i hk-demo`.
- Fix: the client is missing from `base-apps/dex/configmap.yaml`, or the checksum was not bumped
  (Dex keeps the old config, #837). Dex also fails if `HK_DEMO_CLIENT_SECRET` is empty: run
  `scripts/provision-hk-demo-vault.sh` and force the `dex-secrets` ExternalSecret to refresh.

## Login works but the sidecar answers 403

- Check: `kubectl -n hk-demo logs deploy/hk-demo -c oauth2-proxy | grep -i -E "permission|unauthorized|email"`.
- Fix: the email Dex got from GitHub (primary, verified) is not `allowed-emails` in Vault
  `k8s-secrets/hk-demo`. Correct it, then
  `kubectl -n hk-demo annotate externalsecret hk-demo-oauth2 force-sync=$(date +%s) --overwrite`;
  the kubelet refreshes the secret volume in about a minute.

## The page says "HTTP 401" or "HTTP 403 from kagent"

- Check: `kubectl get ns hk-demo --show-labels` has `istio.io/dataplane-mode=ambient`; the
  namespace is in `base-apps/kagent/network-policy-controller.yaml`; the pod mounts
  `/var/run/kagent/token` (audience `kagent`).
- Fix: without ambient the call bypasses the waypoint and has no verified identity; without the
  network policy entry the controller refuses the connection. Both are in Git: sync, then delete
  the pod.

## Boxes stay "waiting", or "trace not found after 2 min"

- Check: the app log's `end` line has a `task_id`; in Grafana Explore, Tempo search
  `{span.a2a.task.id="<task_id>"}`. A trace normally appears 15-30 s after the answer.
- Fix: no trace at all → the collector (`tracing/otel-collector`) or Tempo is down (`tracing`
  namespace); a trace without `a2a.request` → it arrives last, the page waits 30 s for it. Logs
  missing → Loki ingestion lag; open the run again from Recent runs a minute later.

## "A question is already running" and nothing moves

- Check: the app log's last `start` line has no matching `end` line.
- Fix: a run is stuck (kagent unreachable mid-stream; the stream budget is 900 s).
  `kubectl -n hk-demo delete pod -l app=hk-demo` clears it; Recent runs still lists finished runs
  from Loki.

## `ImagePullBackOff` on a first deploy

`ecr-auth` copies `ecr-registry` into a new namespace every 15 minutes. Wait.

## Releasing a new version

In `arigsela/hk-demo`: bump `version` in `pyproject.toml`, tag `vX.Y.Z`, push the tag. The release
workflow prints `image: …:vX.Y.Z@sha256:…`; pin that in `base-apps/hk-demo/deployment.yaml` in a PR.
Renovate does not watch our own ECR images.

## Rotating the oauth2 client secret

`scripts/provision-hk-demo-vault.sh --rotate --email <email>` writes both copies. Then restart
Dex (a checksum bump PR, or `kubectl -n dex rollout restart deploy/dex`) and the hk-demo pod once
both ExternalSecrets have refreshed.

## Revoking access fast

Remove the email from Vault `k8s-secrets/hk-demo:allowed-emails` and force-sync as above. For a
full stop, remove the `hk-demo.arigsela.com` rule from `gateway-allow` in a PR.
