---
app: chores-tracker-frontend
catalog_entity: chores-tracker-frontend
kind: runbook
namespace: chores-tracker-frontend
last_reviewed: 2026-07-10
status: current
tags: [nginx, htmx, frontend]
sources:
  - base-apps/chores-tracker-frontend/deployments.yaml
  - base-apps/chores-tracker-frontend/nginx-ingress.yaml
  - base-apps/chores-tracker-frontend/services.yaml
---

# chores-tracker-frontend — Runbook

## Failure modes

### Symptom: `chores.arigsela.com/api/*` calls return the frontend UI (or 404) instead of backend responses
Routing between the two apps is split purely by `nginx.ingress.kubernetes.io/priority` annotations on two separate `Ingress` objects sharing the same host: this app's `chores-tracker-frontend-nginx` matches `/` at priority `"50"`, while `chores-tracker-backend-nginx` (`base-apps/chores-tracker-backend/nginx-ingress.yaml`) matches `/api/(?!docs|openapi\.json)(.*)` at priority `"100"`. If either annotation is dropped or the frontend's path is broadened, the frontend can shadow the backend's `/api` routes.
- **Check:** `kubectl -n chores-tracker-frontend get ingress chores-tracker-frontend-nginx -o yaml` and `kubectl -n chores-tracker get ingress chores-tracker-backend-nginx -o yaml` — confirm both `nginx.ingress.kubernetes.io/priority` annotations are present (`50` vs `100`) and the frontend's path is still `/` with `pathType: Prefix`, not something that overlaps `/api`.
- **Fix:** open a PR restoring the priority annotations / path scoping in `base-apps/chores-tracker-frontend/nginx-ingress.yaml` (and/or the backend's ingress) rather than editing the live `Ingress`.

### Symptom: Pods stuck in `ImagePullBackOff`/`ErrImagePull`
The image (`852893458518.dkr.ecr.us-east-2.amazonaws.com/chores-tracker-frontend:1.5.3`, `deployments.yaml`) is pulled from a private ECR repo. Cluster-wide ECR auth is refreshed hourly into every namespace by `base-apps/ecr-auth/cronjobs.yaml` (an `ecr-registry` docker-registry `Secret`), and this Deployment does not declare its own `imagePullSecrets`.
- **Check:** `kubectl -n chores-tracker-frontend describe pod -l app=chores-tracker-frontend` (look for `ErrImagePull`/`ImagePullBackOff` in Events) and `kubectl -n chores-tracker-frontend get secret ecr-registry`.
- **Fix:** confirm the `ecr-credentials-sync` CronJob (`base-apps/ecr-auth/cronjobs.yaml`) is running successfully cluster-wide (`kubectl -n kube-system get cronjob ecr-credentials-sync` and `kubectl -n kube-system get jobs --sort-by=.metadata.creationTimestamp | tail` / check its latest run's logs); if `chores-tracker-frontend` is missing the secret or the default `ServiceAccount` isn't wired to it, open a PR adding an explicit `imagePullSecrets: [{name: ecr-registry}]` to `deployments.yaml`.

### Symptom: Pods `CrashLoopBackOff` or failing readiness (`GET /health` on port 3000)
The container mounts an **empty** `emptyDir` volume over `/etc/nginx/conf.d` (`deployments.yaml`, `nginx-conf-d`) with no `ConfigMap` populating it — this overrides whatever server-block config is baked into the `chores-tracker-frontend` image at that path. If the image expects `conf.d` to contain its site config (rather than defining it elsewhere), this mount can leave nginx without a working server block.
- **Check:** `kubectl -n chores-tracker-frontend get pods` then `kubectl -n chores-tracker-frontend logs deploy/chores-tracker-frontend` and `kubectl -n chores-tracker-frontend exec deploy/chores-tracker-frontend -- ls -la /etc/nginx/conf.d` to see if the directory is unexpectedly empty.
- **Fix:** open a PR to back `nginx-conf-d` with a `ConfigMap` volume source containing the required site config (or remove the mount if the image no longer needs it) in `base-apps/chores-tracker-frontend/deployments.yaml`.
