---
type: "Kubernetes App Runbook"
title: "Weather Kitchen Frontend — Runbook"
description: "Operational runbook for Weather Kitchen Frontend: failure modes, checks, and fixes."
app: weather-kitchen-frontend
catalog_entity: weather-kitchen-frontend
kind: runbook
namespace: weather-kitchen-frontend
last_reviewed: 2026-07-10
status: current
tags: [nginx, node, frontend]
sources:
  - base-apps/weather-kitchen-frontend/deployments.yaml
  - base-apps/weather-kitchen-frontend/nginx-ingress.yaml
  - base-apps/weather-kitchen-frontend/services.yaml
---

# weather-kitchen-frontend runbook

## Failure modes

### Symptom: `weather-kitchen.arigsela.com/api/*` calls return the frontend UI (or 404) instead of backend responses
Routing between the two apps is split purely by `nginx.ingress.kubernetes.io/priority` annotations on two separate `Ingress` objects sharing the same host: this app's `weather-kitchen-frontend-nginx` matches `/` at priority `"50"`, while `weather-kitchen-backend-nginx` (`base-apps/weather-kitchen-backend/nginx-ingress.yaml`, namespace `weather-kitchen`) matches `/api/(?!docs|openapi\.json)(.*)` at priority `"100"`. If either annotation is dropped or this app's path is broadened past `/`, the frontend's catch-all rule can shadow the backend's `/api` routes.
- **Check:** `kubectl -n weather-kitchen-frontend get ingress weather-kitchen-frontend-nginx -o yaml` and `kubectl -n weather-kitchen get ingress weather-kitchen-backend-nginx -o yaml` — confirm both `nginx.ingress.kubernetes.io/priority` annotations are present (`50` vs `100`) and this app's path is still `/` with `pathType: Prefix`.
- **Fix:** open a PR restoring the priority annotation / path scoping in `base-apps/weather-kitchen-frontend/nginx-ingress.yaml` rather than editing the live `Ingress` (Argo CD `selfHeal` will revert direct edits anyway).

### Symptom: clients get `403 Forbidden` from `https://weather-kitchen.arigsela.com`
The `Ingress` (`nginx-ingress.yaml`) hard-codes `nginx.ingress.kubernetes.io/whitelist-source-range` to a small allow-list of public IPs plus `10.0.0.0/8`. A legitimate client whose public IP isn't in that list is blocked at the ingress before it ever reaches a pod.
- **Check:** `kubectl -n weather-kitchen-frontend get ingress weather-kitchen-frontend-nginx -o yaml` and inspect the `whitelist-source-range` annotation.
- **Fix:** open a PR adding the new IP/CIDR to `whitelist-source-range` in `base-apps/weather-kitchen-frontend/nginx-ingress.yaml` (the backend's ingress carries the same list and typically needs the matching update).

### Symptom: Pods stuck in `ImagePullBackOff`/`ErrImagePull`
The image (`852893458518.dkr.ecr.us-east-2.amazonaws.com/weather-kitchen-frontend:latest`, `deployments.yaml`) is pulled from a private ECR repo, and this `Deployment` declares no `imagePullSecrets`. Cluster-wide ECR auth is refreshed into namespaces by `base-apps/ecr-auth/cronjobs.yaml` (an `ecr-registry` docker-registry `Secret`).
- **Check:** `kubectl -n weather-kitchen-frontend describe pod -l app=weather-kitchen-frontend` (look for `ErrImagePull`/`ImagePullBackOff` in Events) and `kubectl -n weather-kitchen-frontend get secret ecr-registry`.
- **Fix:** confirm the `ecr-credentials-sync` CronJob (`base-apps/ecr-auth/cronjobs.yaml`, namespace `kube-system`) is running successfully (`kubectl -n kube-system get cronjob ecr-credentials-sync` and check its latest job's logs); if the secret is missing here, open a PR adding an explicit `imagePullSecrets: [{name: ecr-registry}]` to `base-apps/weather-kitchen-frontend/deployments.yaml`.
