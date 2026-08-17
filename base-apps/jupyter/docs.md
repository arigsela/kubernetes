---
type: "Kubernetes App Guide"
title: "JupyterLab Workspace"
description: "Single-workspace JupyterLab for interactive Python, served to a browser and to Claude Code via /api/kernels."
app: jupyter
catalog_entity: jupyter
kind: docs
namespace: jupyter
last_reviewed: 2026-08-17
status: current
tags: [python, notebooks, jupyter]
sources:
  - base-apps/jupyter/deployments.yaml
  - base-apps/jupyter/network-policy.yaml
  - base-apps/jupyter/external-secret.yaml
  - base-apps/jupyter-aws-infrastructure/iam-policy.yaml
---

# JupyterLab Workspace

## What it is
Interactive Python in the cluster at `jupyter.arigsela.com`. It serves two clients that are the same principal: a human in a browser, and Claude Code on the operator's laptop calling `/api/kernels`. Batch Python belongs in Argo Workflows; this is for exploration.

## Architecture & data flow
One `Deployment` of the upstream `quay.io/jupyter/scipy-notebook` image, digest-pinned, behind the `main` Istio Gateway. Jupyter Server serves the Lab UI and the kernel API from the same process on port 8888, and both clients present the same token — there is deliberately no second authentication path.

State is split three ways. Notebooks live in `arigsela/notebooks` on GitHub. The 20Gi `local-path` PVC (`pvc.yaml`) mounts the **whole home** at `/home/jovyan`, holding scratch data and `~/.local`. Bulk data lives in S3 `asela-jupyter-scratch`, provisioned by `base-apps/jupyter-aws-infrastructure/` with an IAM user scoped to that one bucket.

## Where config lives
- Workload and security posture: `deployments.yaml`
- Isolation: `network-policy.yaml`
- Secrets: `external-secret.yaml` / `secret-store.yaml`, from Vault `k8s-secrets/jupyter` (`token`, `github-token`), Vault role `jupyter`
- AWS: `base-apps/jupyter-aws-infrastructure/`, connection Secret `jupyter-s3-creds`
- Exposure: `httproute.yaml`, `certificate.yaml`, `reference-grant.yaml`, plus the `https-jupyter` listener in `base-apps/istio-ingress/gateway.yaml` and the restricted rule in `authorizationpolicy.yaml`

## Gotchas & tribal knowledge
- **The PVC mounts the whole home, not `work/`.** This is deliberate: it makes `~/.local` persistent so `pip install --user -r requirements.txt` survives restarts without a custom image. It also shadows whatever the image ships in `/home/jovyan`.
- **`fsGroup` must be `100`.** The image runs as `jovyan` (1000) in group `users` (100). A wrong `fsGroup` leaves the mounted home unwritable and the server exits at startup.
- **`--ServerApp.allow_origin` is belt-and-braces, not load-bearing.** `jupyter_server`'s origin check already passes here because the request's `Origin` and `Host` both resolve to `jupyter.arigsela.com` behind this Gateway; the flag is explicit insurance if that ever stops holding, not the fix for a websocket 403 — see runbook.md.
- **The pod has no ServiceAccount token, and reaches nothing in-cluster except two read-only endpoints.** Vault, PostgreSQL, Loki, Grafana, ClickHouse and the Kubernetes API are all refused; `pypi.org` and public IPs are reachable and DNS resolves — verified in-cluster 2026-08-17. Confirmed differentially against `n8n` (same cluster and mesh, no NetworkPolicy), which *can* reach PostgreSQL on the same IP and port, so the policy is what enforces this and deleting it would open the pod up. The two deliberate exceptions are **Prometheus `:9090`** and **Ollama `:11434`** (spec §3.3.2); both are read-only and hold no credentials. Adding a third is a design decision, not a config tweak — see `docs/superpowers/specs/2026-08-17-jupyter-notebooks-design.md` §3.3.
- **Prometheus is reachable, but per-PVC disk attribution is not available from any metric in this cluster.** `kubelet_volume_stats_used_bytes` and Coroot's `container_resources_disk_used_bytes` both report the *shared device*, so every `local-path` PVC on a node reports that node's usage — on 2026-08-17, eight different PVCs on `worker-01` all read ~117.8GB. That is the same root cause as the disk risk itself: no per-volume isolation means nothing per-volume to measure. Node-level trend and forecasting work fine; "which app is filling the disk" does not, and no query will fix it.
- **A denied connection surfaces as `ConnectionRefused`, not a timeout.** Under this CNI plus ztunnel the deny path sends an immediate RST. When debugging, do not read "connection refused" as "nothing is listening there" — from this pod, refused is what *blocked* looks like.
- **The 20Gi on the PVC is a request, not a quota — and the pod is pinned to `k3s-worker-02` because of it.** `local-path` bind-mounts a directory on the node, so `df` inside the pod reports the node's whole filesystem and a notebook can fill it. `k3s-worker-01` holds the local-path volumes for Vault, PostgreSQL, Prometheus and Coroot; a single large write there would starve all of them of disk. The NetworkPolicy stops this pod *talking* to Vault and PostgreSQL but does nothing about shared-disk exhaustion, so isolation is achieved by scheduling instead. `worker-02` hosts no stateful workload. Do not remove the `kubernetes.io/hostname` selector without putting a real quota in place.
- **Bulk data belongs in S3 (`asela-jupyter-scratch`), not the PVC.** Not a style preference — see above.
- **`enableServiceLinks: false` is load-bearing, not tidiness.** Kubernetes injects Docker-link-style env vars for every Service in the namespace, so `Service/jupyter` produces `JUPYTER_PORT=tcp://10.43.x.x:8888` — and `jupyter_server` reads `JUPYTER_PORT` expecting an integer. With service links on, the container dies at startup with `ValueError: invalid literal for int() with base 10: 'tcp://...:8888'`, which looks like a Jupyter config bug and is actually a name collision between the Service and the app's own env var. Hit on the first live deploy. Turning links off is also correct posture: the NetworkPolicy blocks all but two in-cluster endpoints, both reached by DNS name, so service-discovery env vars buy nothing.
- **`strategy: Recreate`.** `local-path` is ReadWriteOnce, so a rolling update deadlocks on the volume.
- **Liveness and readiness probes are `tcpSocket` on 8888, not `httpGet`.** This keeps the probe independent of Jupyter's auth semantics, which are easy to get wrong: `/api` is in fact the *only* unauthenticated endpoint (see the table below), so an `httpGet` probe against it would work — but one against `/api/status`, the obvious "health check" choice, returns 403 forever and would take the pod out of `Ready` permanently. If you do switch to HTTP, `/api` is the only safe target.
- **Measured unauthenticated surface (2026-08-17), for when you need to reason about the auth boundary rather than guess at it:**

  | Path | Unauthenticated |
  |---|---|
  | `/api` | 200 — version string only, no data |
  | `/api/status`, `/api/kernels`, `/api/contents`, `/api/sessions`, `/api/terminals` | 403 |
  | `POST /api/kernels` | 403 — no unauthenticated code execution |
  | `/lab` | 302 to login |
- **Rotating the token logs out both clients**, because both use it.
- **The GitHub PAT is mounted at `/etc/jupyter-secrets/github-token`, outside the home, and read by a git credential helper at invocation time.** It is deliberately never written to `~/.git-credentials`: the home is the PVC, so a copy there would survive rotation in Vault and outlive the secret it came from.
