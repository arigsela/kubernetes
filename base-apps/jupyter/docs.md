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
- **The pod has no ServiceAccount token; RFC1918 egress is *intended* to be blocked by NetworkPolicy, but that enforcement is unverified, not an established fact.** The token absence is a manifest fact (`automountServiceAccountToken: false`). The RFC1918 block depends on k3s enforcing NetworkPolicy egress rules alongside ztunnel, which is not yet confirmed in this cluster — see the plan's Task 6 Step 3 before relying on it. This is the design's central control, not an oversight. Anything needing in-cluster access does not belong in a notebook — see `docs/superpowers/specs/2026-08-17-jupyter-notebooks-design.md` §3.3.
- **`strategy: Recreate`.** `local-path` is ReadWriteOnce, so a rolling update deadlocks on the volume.
- **Liveness and readiness probes are `tcpSocket` on 8888, not `httpGet`.** Every endpoint under `/api` requires a token, so an unauthenticated HTTP probe can't tell "wedged" from "working" — it just fails every time, taking the pod out of `Ready` forever. Do not "fix" this to an HTTP health check.
- **Rotating the token logs out both clients**, because both use it.
- **The GitHub PAT is mounted at `/etc/jupyter-secrets/github-token`, outside the home, and read by a git credential helper at invocation time.** It is deliberately never written to `~/.git-credentials`: the home is the PVC, so a copy there would survive rotation in Vault and outlive the secret it came from.
