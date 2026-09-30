---
type: "Kubernetes App Runbook"
title: "Weather Kitchen Frontend — Runbook"
description: "Operational runbook for Weather Kitchen Frontend: failure modes, checks, and fixes."
app: weather-kitchen-frontend
catalog_entity: weather-kitchen-frontend
kind: runbook
namespace: weather-kitchen-frontend
last_reviewed: 2026-09-30
status: current
tags: [nginx, node, frontend]
sources:
  - base-apps/weather-kitchen-frontend/deployments.yaml
  - base-apps/weather-kitchen-frontend/httproute.yaml
  - base-apps/weather-kitchen-frontend/services.yaml
  - base-apps/weather-kitchen-backend/httproute.yaml
  - base-apps/weather-kitchen-backend/certificate.yaml
  - base-apps/istio-ingress/authorizationpolicy.yaml
  - base-apps/admission-policies/inject-ecr-pull-secret.yaml
  - base-apps/ecr-auth/cronjobs.yaml
---

# weather-kitchen-frontend runbook

## Failure modes

### Symptom: `weather-kitchen.arigsela.com/api/*` calls return the frontend UI (or 404) instead of backend responses, or `/api/docs` stops showing the docs page
Routing is split between two HTTPRoutes on listener `https-weather-kitchen`: this app's `weather-kitchen-frontend` (`Exact /api/docs`, `Exact /api/openapi.json`, `PathPrefix /`) and the backend's `weather-kitchen-backend` (`PathPrefix /api`, namespace `weather-kitchen`). If the backend route is missing or not accepted, this app's `/` prefix catches every `/api` request. If the two `Exact` rules here are removed or widened, `/api/docs` and `/api/openapi.json` fall through to the backend.
- **Check:** `kubectl -n weather-kitchen-frontend get httproute weather-kitchen-frontend -o yaml` and `kubectl -n weather-kitchen get httproute weather-kitchen-backend -o yaml` — confirm both are `Accepted` on `https-weather-kitchen` (`status.parents[].conditions`) and the paths match the files.
- **Fix:** open a PR restoring the route in `base-apps/weather-kitchen-frontend/httproute.yaml` or `base-apps/weather-kitchen-backend/httproute.yaml` rather than editing live objects (Argo CD `selfHeal` reverts direct edits).

### Symptom: clients get `403 Forbidden` (`RBAC: access denied`) from `https://weather-kitchen.arigsela.com`
The Gateway's `AuthorizationPolicy` (`base-apps/istio-ingress/authorizationpolicy.yaml`) has one `weather-kitchen.arigsela.com` rule, covering both this app and the backend, that allows only the allow-listed `/32`s. A client outside it is refused at the Gateway before reaching any pod.
- **Check:** compare the client's public IP (`curl -s ifconfig.me`) with that rule. If every allow-listed host broke at once, the home WAN address probably rotated.
- **Fix:** open a PR adding the address to that rule (one change covers both halves). For a WAN rotation, see `base-apps/wan-ip-monitor/runbook.md`.

### Symptom: TLS errors on `weather-kitchen.arigsela.com`
This app has no certificate: the listener serves `weather-kitchen-tls` from the backend's namespace.
- **Check / Fix:** see the TLS symptom in `base-apps/weather-kitchen-backend/runbook.md` (`certificate.yaml` and `reference-grant.yaml` there).

### Symptom: Pods stuck in `ImagePullBackOff`/`ErrImagePull`
The image (`852893458518.dkr.ecr.us-east-2.amazonaws.com/weather-kitchen-frontend:latest`, `deployments.yaml`) is pulled from a private ECR repo. The Deployment declares no `imagePullSecrets` on purpose: the MutatingAdmissionPolicy `inject-ecr-pull-secret` (`base-apps/admission-policies/inject-ecr-pull-secret.yaml`) appends `ecr-registry` to every pod that pulls from ECR, and `ecr-credentials-sync` (`base-apps/ecr-auth/cronjobs.yaml`, namespace `kube-system`, every 15 minutes) keeps that Secret fresh in every namespace. The policy is `failurePolicy: Ignore`, so if it fails the pod is still admitted, just without the secret.
- **Check:** `kubectl -n weather-kitchen-frontend get pod -l app=weather-kitchen-frontend -o jsonpath='{.items[*].spec.imagePullSecrets}'` — it must list `ecr-registry`. Then `kubectl -n weather-kitchen-frontend get secret ecr-registry` (exists and recent?) and `kubectl -n kube-system get cronjob ecr-credentials-sync` plus its latest job's logs.
- **Fix:** if the pod lacks `ecr-registry`, the admission policy didn't apply; see `base-apps/admission-policies/runbook.md`, then delete the pod so it is re-created with the secret. If the Secret is missing or stale, fix the `ecr-credentials-sync` CronJob. Don't add `imagePullSecrets` to the Deployment to work around either.
