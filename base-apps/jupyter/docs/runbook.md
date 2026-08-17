---
type: "Kubernetes App Runbook"
title: "JupyterLab Workspace — Runbook"
description: "Operational runbook for jupyter: failure modes, checks, and fixes."
app: jupyter
catalog_entity: jupyter
kind: runbook
namespace: jupyter
last_reviewed: 2026-08-17
status: current
tags: [python, notebooks, jupyter]
sources:
  - base-apps/jupyter/deployments.yaml
  - base-apps/jupyter/network-policy.yaml
---

# JupyterLab Workspace — Runbook

## Failure modes

### Symptom: pod CrashLoopBackOff, logs show a permission error on /home/jovyan
- **Check:** `kubectl -n jupyter get deploy jupyter -o jsonpath='{.spec.template.spec.securityContext}'`
- **Fix:** `fsGroup` must be `100` and `runAsUser` `1000`. Any other value leaves the mounted home unwritable.

### Symptom: browser loads JupyterLab but notebooks will not start a kernel; console shows a 403 on the websocket
- **Check:** `kubectl -n jupyter get deploy jupyter -o yaml | grep allow_origin`
- **Fix:** `--ServerApp.allow_origin` must be exactly `https://jupyter.arigsela.com`. Plain HTTP endpoints work without it, which makes this look like an auth problem rather than an origin-check problem.

### Symptom: 403 from every request, including with a valid token
- **Check:** `grep -A12 'jupyter.arigsela.com' base-apps/istio-ingress/authorizationpolicy.yaml`
- **Fix:** the gateway allow-list denies by default. If the WAN address rotated, the Route 53 record and this file must move together — see `base-apps/wan-ip-monitor/runbook.md`.

### Symptom: ExternalSecret shows SecretSyncedError
- **Check:** `kubectl -n jupyter describe externalsecret jupyter-secrets`
- **Fix:** confirm the Vault role exists and is bound to this namespace: `vault read auth/kubernetes/role/jupyter`. The role name must equal the namespace.

### Symptom: boto3 calls fail with AccessDenied
- **Check:** `kubectl -n jupyter get secret jupyter-s3-creds -o jsonpath='{.data}' | jq keys`
- **Fix:** keys are `username` and `attribute.secret` — there is no `attribute.id`. The IAM policy grants only `asela-jupyter-scratch`; any other bucket is denied by design, not by mistake.

### Symptom: pod Pending after a node reboot
- **Check:** `kubectl -n jupyter describe pvc jupyter-pvc`
- **Fix:** `local-path` pins the volume to one node. If that node is gone the PVC cannot bind. Nothing irreplaceable is on it: delete the PVC, let it rebind, then re-clone the notebooks repo and re-run `pip install --user -r requirements.txt`.

## How-to

### Deploy / update
Commit to `main`; Argo CD syncs. Never `kubectl apply`.

### Rotate the Jupyter token
`vault kv patch k8s-secrets/jupyter token=<new>`, then `kubectl -n jupyter rollout restart deploy/jupyter`. ESO refreshes hourly, but the pod reads the token only at startup. This logs out the browser and Claude Code together.

### Install a package permanently
Add it to `requirements.txt` in `arigsela/notebooks`, then from a JupyterLab terminal: `pip install --user -r ~/work/notebooks/requirements.txt`. It persists because `~/.local` is on the PVC.
