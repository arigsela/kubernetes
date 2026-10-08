---
type: "Kubernetes App Runbook"
title: "JupyterLab Workspace — Runbook"
description: "Operational runbook for jupyter: failure modes, checks, and fixes."
app: jupyter
catalog_entity: jupyter
kind: runbook
namespace: jupyter
last_reviewed: 2026-10-08
status: stable
tags: [python, notebooks, jupyter]
sources:
  - base-apps/jupyter/deployments.yaml
  - base-apps/jupyter/pvc.yaml
  - base-apps/jupyter/network-policy.yaml
  - base-apps/jupyter/external-secret.yaml
  - base-apps/jupyter/secret-store.yaml
  - base-apps/jupyter/httproute.yaml
  - base-apps/jupyter/oauth2-proxy-config.yaml
  - base-apps/dex/configmap.yaml
  - base-apps/istio-ingress/telemetry.yaml
  - base-apps/istio-istiod.yaml
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
- **Fix:** if the allow-list is the cause, see "403 from every request" below. If the Gateway is failing to upgrade the websocket, that's a Gateway/HTTPRoute problem, not a Jupyter one. `--ServerApp.allow_origin` is belt-and-braces, not the load-bearing check: `jupyter_server`'s origin check already passes because `Origin` matches `Host` behind this Gateway, so a wrong value here is an unlikely last resort — check it last, not first. That equality holds only while oauth2-proxy forwards the original `Host`: `passHostHeader` must stay `true` in `oauth2-proxy-config.yaml`.

### Symptom: 403 from every request, before any GitHub login page appears
- **Check:** `grep -A12 'jupyter.arigsela.com' base-apps/istio-ingress/authorizationpolicy.yaml`
- **Fix:** the gateway allow-list denies by default. If the WAN address rotated, the Route 53 record and this file must move together — see `base-apps/wan-ip-monitor/runbook.md`.

### Symptom: Dex shows "Invalid client credentials" or "Unregistered redirect_uri" after the GitHub login
- **Check:** `kubectl -n dex get secret dex-secrets -o jsonpath='{.data}' | jq 'has("JUPYTER_CLIENT_SECRET")'`, and compare the pod's start time with the Secret's: `kubectl -n dex get pod -l app=dex -o jsonpath='{.items[0].status.startTime}'`.
- **Fix:** Dex reads its secrets only at startup. If `JUPYTER_CLIENT_SECRET` was missing or changed after the Dex pod started, Dex is running with an empty or stale secret for the `jupyter` client (it starts anyway; only this client is broken). Make `k8s-secrets/dex` `jupyter-client-secret` equal `k8s-secrets/jupyter` `oauth2-client-secret`, force-sync `dex-secrets`, then restart Dex (ask first: every Dex login blips). A redirect_uri error means the `jupyter` client in `base-apps/dex/configmap.yaml` is missing or the Dex pod predates it; see the Dex runbook's checksum note.

### Symptom: the GitHub login succeeds, then oauth2-proxy answers 403
- **Check:** `kubectl -n jupyter logs deploy/jupyter -c oauth2-proxy | grep -i -E "permission|unauthorized|email"`
- **Fix:** the email Dex got from GitHub (primary, verified) is not the value in `k8s-secrets/jupyter` `allowed-emails`. Correct it in Vault, then `kubectl -n jupyter annotate externalsecret jupyter-oauth2 force-sync=$(date +%s) --overwrite`. The proxy watches that file, so no restart is needed; kubelet refreshes secret volumes in about a minute.

### Symptom: logged in with GitHub, but JupyterLab shows its own token page, or Lab API calls return 403
- **Check:** whether the injected header still matches the server's token: compare `kubectl -n jupyter get secret jupyter-oauth2 -o jsonpath='{.data.upstream-authorization}' | base64 -d | cut -c7- | shasum` with `kubectl -n jupyter get secret jupyter-secrets -o jsonpath='{.data.token}' | base64 -d | shasum`.
- **Fix:** they differ after a token rotation that skipped a step. Force-sync both ExternalSecrets, then restart the pod (see "Rotate the Jupyter token"). If they match, the pod started before the Secrets updated: restart it. Both containers read the token only at startup.

### Symptom: ExternalSecret shows SecretSyncedError
- **Check:** `kubectl -n jupyter describe externalsecret jupyter-secrets jupyter-oauth2`
- **Fix:** confirm the Vault role exists and is bound to this namespace: `vault read auth/kubernetes/role/jupyter`. The role name must equal the namespace. For `jupyter-oauth2`, also confirm `k8s-secrets/jupyter` has `oauth2-client-secret`, `oauth2-cookie-secret` and `allowed-emails` (see "Provision the GitHub login secrets").

### Symptom: boto3 calls fail with AccessDenied
- **Check:** `kubectl -n jupyter get secret jupyter-s3-creds -o jsonpath='{.data}' | jq keys`
- **Fix:** keys are `username` and `attribute.secret` — there is no `attribute.id`. The IAM policy grants only `asela-jupyter-scratch`; any other bucket is denied by design, not by mistake.

### Symptom: pod never becomes Ready after someone edits the probes; `Service/jupyter` has no endpoints
- **Check:** `kubectl -n jupyter get deploy jupyter -o jsonpath='{.spec.template.spec.containers[0].livenessProbe}'`
- **Fix:** the jupyter container's probes must stay `exec` probes that open a socket to `127.0.0.1:8888`, with `timeoutSeconds: 5`. Jupyter listens on loopback, so a `tcpSocket` or `httpGet` probe (kubelet probes the pod IP) can never pass. If you need an HTTP check inside the exec, `/api` is the **only** endpoint that answers unauthenticated (200); `/api/status`, the intuitive choice, returns 403 without a token. Measured surface is in `docs.md`. The oauth2-proxy container's probes are `httpGet /ping` on 4180, which is fine.

### Symptom: pod Pending with `node(s) didn't match Pod's node affinity/selector`, or a volume node-affinity conflict
- **Check:** `kubectl -n jupyter get pvc jupyter-pvc -o jsonpath='{.spec.volumeName}'` then `kubectl get pv <name> -o jsonpath='{.spec.nodeAffinity}'`
- **Fix:** the pod is pinned to `k3s-worker-02` (see `docs.md` for why — it is a disk-isolation control). A `local-path` PV is bound to whichever node first provisioned it, so a PVC created while the pod ran on `k3s-worker-01` cannot follow it. Delete the PVC and let Argo CD recreate it — nothing irreplaceable is on it by design: `kubectl -n jupyter delete pvc jupyter-pvc`, wait for the pod to start, then re-clone the notebooks repo and `pip install --user -r ~/work/notebooks/requirements.txt`. Confirm the clone has nothing unpushed first.

### Symptom: node disk filling, or other apps on the node failing to write
- **Check:** `kubectl -n jupyter exec deploy/jupyter -- du -sh /home/jovyan` and `df -h /home/jovyan` — the latter reports the **node's** filesystem, not a 20Gi volume.
- **Fix:** `local-path` enforces no quota, so the PVC's 20Gi is advisory. Move large datasets to S3 (`asela-jupyter-scratch`) and delete them from the PVC. The pod is pinned away from the node holding Vault, Prometheus and Coroot's ClickHouse, but **one CNPG PostgreSQL instance also runs on `k3s-worker-02`** (two instances since 2026-09-30, one per worker) — so treat a filling disk as a database risk too: `kubectl -n postgresql get pods -o wide -l cnpg.io/cluster=postgresql-cluster -L cnpg.io/instanceRole` shows whether the worker-02 instance is currently the primary.

### Symptom: pod Pending after a node reboot
- **Check:** `kubectl -n jupyter describe pvc jupyter-pvc`
- **Fix:** `local-path` pins the volume to one node. If that node is gone the PVC cannot bind. Nothing irreplaceable is on it: delete the PVC, let it rebind, then re-clone the notebooks repo and re-run `pip install --user -r requirements.txt`.

## How-to

### Deploy / update
Commit to `main`; Argo CD syncs. Never `kubectl apply`.

### Rotate the Jupyter token
`vault kv patch -mount=k8s-secrets jupyter token=<new>`, then force-sync both ExternalSecrets (`kubectl -n jupyter annotate externalsecret jupyter-secrets jupyter-oauth2 force-sync=$(date +%s) --overwrite`), wait until both report `SecretSynced`, and `kubectl -n jupyter rollout restart deploy/jupyter`. Both containers read the token only at startup: Jupyter as `JUPYTER_TOKEN`, oauth2-proxy as the injected header. Claude Code needs the new token; the browser does not notice, because it never held the token.

### Log in (browser)
Browse to `https://jupyter.arigsela.com`. oauth2-proxy sends you to Dex, Dex to GitHub, and back. Only the email in `k8s-secrets/jupyter` `allowed-emails` is admitted. The session cookie lasts 12h; after that the next request goes through the login again (usually without a prompt, since GitHub remembers you).

**Never** put the token in a URL (`?token=<token>`), here or over the port-forward. The Gateway access log strips query strings (since f04063a, `%REQ_WITHOUT_QUERY%` in `base-apps/istio-istiod.yaml`, pinned by `tests/istio_logging/`), but the URL still lands in browser history and `Referer` headers. Treat any token used in a URL as compromised and rotate it (above).

### Connect Claude Code (or any API client)
The public hostname only accepts a GitHub browser login, so API clients come in through the Kubernetes API, which authenticates against Dex too:

```bash
kubectl --context homelab-oidc -n jupyter port-forward deploy/jupyter 8888:8888
```

Then call `http://127.0.0.1:8888/api/...` with `Authorization: token <token>` (from `k8s-secrets/jupyter` `token`). Send the token as a header, never in the URL. Kernel websockets work over the port-forward; Jupyter's origin check passes because the client's `Origin`, if any, matches `Host`.

**Break-glass when Dex is down:** the same port-forward with the x509 admin kubeconfig (`docs/troubleshooting/kubectl-oidc.md`), then open `http://127.0.0.1:8888/login` in a browser and paste the token.

### Provision the GitHub login secrets (one-time; done before the 2026-10-08 change merged)
From a laptop with Vault port-forwarded and a `vault login -method=oidc` session. `kv patch` adds fields and keeps the existing ones; `kv put` would wipe `token` and `github-token`:

```bash
CLIENT_SECRET=$(openssl rand -hex 32)
COOKIE_SECRET=$(openssl rand -base64 32 | tr -- '+/' '-_')   # 32 random bytes, as oauth2-proxy expects
vault kv patch -mount=k8s-secrets jupyter \
  oauth2-client-secret="$CLIENT_SECRET" \
  oauth2-cookie-secret="$COOKIE_SECRET" \
  allowed-emails="$(vault kv get -mount=k8s-secrets -field=allowed-emails agent-audit-web)"
vault kv patch -mount=k8s-secrets dex jupyter-client-secret="$CLIENT_SECRET"
unset CLIENT_SECRET COOKIE_SECRET
```

`allowed-emails` reuses agent-audit-web's value: the same operator, the same verified GitHub primary email.

### Install a package permanently
Add it to `requirements.txt` in `arigsela/notebooks`, then from a JupyterLab terminal: `pip install --user -r ~/work/notebooks/requirements.txt`. It persists because `~/.local` is on the PVC.
