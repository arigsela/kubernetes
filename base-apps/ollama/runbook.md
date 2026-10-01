---
type: "Kubernetes App Runbook"
title: "Ollama — Runbook"
description: "Operational runbook for Ollama (embedding-only): model-volume pulls, model not found, OOM, model updates."
app: ollama
catalog_entity: ollama
kind: runbook
namespace: ollama
last_reviewed: 2026-09-30
status: stable
tags: [llm, embeddings, gpu-optional]
sources:
  - base-apps/ollama/deployments.yaml
  - base-apps/ollama/services.yaml
  - models/nomic-embed-text/Dockerfile
  - scripts/build-model-image.sh
  - base-apps/kagent/embedding-model-config.yaml
---

# ollama runbook

## Failure modes

### Symptom: new pod stuck in `ContainerCreating` / `ErrImagePull` on the `models` volume
- **Check:** `kubectl -n ollama describe pod -l app=ollama` events. The model is an image volume pulled from ECR (`models/nomic-embed-text@sha256:…`). `no basic auth credentials` / 401 → the pod lacks `imagePullSecrets: ecr-registry` or the namespace secret is stale (`admission-policies` runbook, ECR `ImagePullBackOff`). `not found` → the digest in `deployments.yaml` was never pushed.
- **Fix:** fix the secret or push the image (`scripts/build-model-image.sh nomic-embed-text --push`). The old pod keeps serving meanwhile (`maxUnavailable: 0`).

### Symptom: embeddings fail with "model not found"
- **Check:** `kubectl -n ollama exec deploy/ollama -c ollama -- ollama list` must show `nomic-embed-text:latest` (and `:v1.5`). `OLLAMA_MODELS` must be `/models`.
- **Fix:** the image must carry a manifest named after what kagent asks for (`nomic-embed-text` = `:latest`); `models/nomic-embed-text/Dockerfile` writes it. Rebuild rather than `ollama pull`: the models directory is read-only, so `/api/pull` fails by design.

### Symptom: OOMKilled or CrashLoopBackOff on the `ollama` container
- **Check:** `kubectl -n ollama describe pod -l app=ollama` for `OOMKilled`/`Last State`, and `kubectl -n ollama top pod -l app=ollama`. The main container is limited to `memory: 2Gi` (`deployments.yaml`), sized for the resident `nomic-embed-text` plus concurrent requests.
- **Fix:** PR to raise `resources.limits.memory` (and `requests.memory`) for the `ollama` container in `deployments.yaml`.

## How-to

### Deploy / update
Edit manifests here and PR; Argo CD syncs on merge. `RollingUpdate` with `maxSurge: 1` / `maxUnavailable: 0`: the new pod starts next to the old one, which keeps serving embeddings until the new one is Ready. No outage.

### Model update
The model changes only through a new image digest, and **changing the embedding model invalidates every vector kagent has stored** (memories embedded with the old model are no longer comparable). Treat it as a migration, not a bump.
1. Change `MODEL_TAG`/`MODEL_DIGEST` (and `TAG`) in `scripts/build-model-image.sh`; ECR tags are immutable, so a new model needs a new tag.
2. `scripts/build-model-image.sh nomic-embed-text` (local, reproducible), then `--push`; it prints `…/models/nomic-embed-text@sha256:…`.
3. PR that digest into the `models` volume in `deployments.yaml` (and kagent's `embedding-model-config.yaml` if the model name changes). Plan how existing kagent memory is re-embedded or dropped.
4. **Rollback:** revert the PR; the old digest is still in ECR.

### Check current model inventory
`kubectl -n ollama exec deploy/ollama -c ollama -- ollama list`

### Note on performance
No GPU `nodeSelector` or GPU resource request is configured (`deployments.yaml`) — inference runs on CPU only. Expect materially slower embedding latency than a GPU-backed node pool; this is expected behavior, not a bug.

### "Can I run a chat/generation model here?"
Not as deployed. Only `nomic-embed-text` is in the image volume, and `/api/pull` fails because `/models` is read-only. Serving another model means building an image that contains it (extend `scripts/build-model-image.sh` / `models/`), adding it to the volume via PR, and resizing the container (the 2Gi limit is sized for the ~274MB embedding model kept resident by `OLLAMA_KEEP_ALIVE=-1`). Treat that as a design change, not a runbook step.
