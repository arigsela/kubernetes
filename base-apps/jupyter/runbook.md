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

### Symptom: pod CrashLoopBackOff, logs end with `ValueError: invalid literal for int() with base 10: 'tcp://10.43.x.x:8888'`
- **Check:** `kubectl -n jupyter get deploy jupyter -o jsonpath='{.spec.template.spec.enableServiceLinks}'`
- **Fix:** it must be `false`. Kubernetes injects Docker-link-style env vars for every Service in the namespace, so `Service/jupyter` sets `JUPYTER_PORT=tcp://<clusterIP>:8888`, which `jupyter_server` tries to parse as a port number. This is a name collision between the Service and the app's own env var, not a Jupyter misconfiguration — do not chase it in the `args:` block. Renaming the Service would also fix it, but the HTTPRoute and NetworkPolicy both target `jupyter` by name.

### Symptom: pod CrashLoopBackOff, logs show a permission error on /home/jovyan
- **Check:** `kubectl -n jupyter get deploy jupyter -o jsonpath='{.spec.template.spec.securityContext}'`
- **Fix:** `fsGroup` must be `100` and `runAsUser` `1000`. Any other value leaves the mounted home unwritable.

### Symptom: browser loads JupyterLab but notebooks will not start a kernel; console shows a 403 on the websocket
- **Check:** first rule out the Gateway allow-list — `grep -A12 'jupyter.arigsela.com' base-apps/istio-ingress/authorizationpolicy.yaml`. A 403 from Istio's `AuthorizationPolicy` looks identical to a 403 from Jupyter itself in the browser console. Next, check for a failed websocket upgrade at the Gateway rather than an application-level rejection. Only then check `kubectl -n jupyter get deploy jupyter -o yaml | grep allow_origin`.
- **Fix:** if the allow-list is the cause, see "403 from every request" below. If the Gateway is failing to upgrade the websocket, that's a Gateway/HTTPRoute problem, not a Jupyter one. `--ServerApp.allow_origin` is belt-and-braces, not the load-bearing check: `jupyter_server`'s origin check already passes because `Origin` matches `Host` behind this Gateway, so a wrong value here is an unlikely last resort — check it last, not first.

### Symptom: 403 from every request, including with a valid token
- **Check:** `grep -A12 'jupyter.arigsela.com' base-apps/istio-ingress/authorizationpolicy.yaml`
- **Fix:** the gateway allow-list denies by default. If the WAN address rotated, the Route 53 record and this file must move together — see `base-apps/wan-ip-monitor/runbook.md`.

### Symptom: ExternalSecret shows SecretSyncedError
- **Check:** `kubectl -n jupyter describe externalsecret jupyter-secrets`
- **Fix:** confirm the Vault role exists and is bound to this namespace: `vault read auth/kubernetes/role/jupyter`. The role name must equal the namespace.

### Symptom: boto3 calls fail with AccessDenied
- **Check:** `kubectl -n jupyter get secret jupyter-s3-creds -o jsonpath='{.data}' | jq keys`
- **Fix:** keys are `username` and `attribute.secret` — there is no `attribute.id`. The IAM policy grants only `asela-jupyter-scratch`; any other bucket is denied by design, not by mistake.

### Symptom: pod never becomes Ready after someone edits the probes; `Service/jupyter` has no endpoints
- **Check:** `kubectl -n jupyter get deploy jupyter -o jsonpath='{.spec.template.spec.containers[0].livenessProbe}'`
- **Fix:** it must stay `tcpSocket` on port `8888`, same for the readiness probe. Revert to `tcpSocket`. If you genuinely need an HTTP probe, `/api` is the **only** endpoint that answers unauthenticated (200); `/api/status` — the intuitive choice — returns 403 to a probe with no token and will hold the pod out of `Ready` forever. Measured surface is in `docs.md`.

### Symptom: pod Pending after a node reboot
- **Check:** `kubectl -n jupyter describe pvc jupyter-pvc`
- **Fix:** `local-path` pins the volume to one node. If that node is gone the PVC cannot bind. Nothing irreplaceable is on it: delete the PVC, let it rebind, then re-clone the notebooks repo and re-run `pip install --user -r requirements.txt`.

## How-to

### Deploy / update
Commit to `main`; Argo CD syncs. Never `kubectl apply`.

### Rotate the Jupyter token
`vault kv patch k8s-secrets/jupyter token=<new>`, then `kubectl -n jupyter rollout restart deploy/jupyter`. ESO refreshes hourly, but the pod reads the token only at startup. This logs out the browser and Claude Code together.

### Log in without leaking the token
Browse to `https://jupyter.arigsela.com/login` and paste the token into the form. That submits it as a POST body, which the gateway access log does not capture.

**Never** browse to `https://jupyter.arigsela.com/?token=<token>`. `base-apps/istio-ingress/telemetry.yaml` enables Envoy access logging on the `main` Gateway, the default format logs the request path including the query string, and `base-apps/logging/alloy-config.yaml` ships every pod's logs to Loki, which persists to S3 — so a token pasted into the URL is written to durable, plaintext storage. Treat any token used that way as compromised and rotate it immediately (above).

Programmatic clients (Claude Code) send `Authorization: token <…>` as a header, which is never captured by the access log either.

### Install a package permanently
Add it to `requirements.txt` in `arigsela/notebooks`, then from a JupyterLab terminal: `pip install --user -r ~/work/notebooks/requirements.txt`. It persists because `~/.local` is on the PVC.
