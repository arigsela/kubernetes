---
type: "Kubernetes App Runbook"
title: "Ollama — Runbook"
description: "Operational runbook for Ollama: failure modes, checks, and fixes."
app: ollama
catalog_entity: ollama
kind: runbook
namespace: ollama
last_reviewed: 2026-07-10
status: current
tags: [llm, embeddings, gpu-optional]
sources:
  - base-apps/ollama/deployments.yaml
  - base-apps/ollama/pvc.yaml
  - base-apps/ollama/services.yaml
---

# ollama runbook

## Failure modes

### Symptom: pod stuck in `Init` / model requests fail with "model not found"
- **Check:** `kubectl -n ollama logs deploy/ollama -c pull-model` (the `pull-model` init container runs `ollama pull nomic-embed-text` before the main container starts — a slow/failed pull blocks readiness). Also check `kubectl -n ollama get pods` for `Init:` status and `kubectl -n ollama exec deploy/ollama -c ollama -- ollama list` once running, to confirm `nomic-embed-text` is present.
- **Fix:** if the pull fails due to network/registry issues, restart the pod (`kubectl -n ollama delete pod <pod>`) to retry the init container. If a different/larger model is needed, PR a change to the `pull-model` command in `deployments.yaml` and raise the PVC size in `pvc.yaml` accordingly (see PVC-full mode below).

### Symptom: OOMKilled or CrashLoopBackOff on the `ollama` container
- **Check:** `kubectl -n ollama describe pod -l app=ollama` for `OOMKilled`/`Last State`, and `kubectl -n ollama top pod -l app=ollama`. The main container is limited to `memory: 1Gi` (`deployments.yaml`), which can be tight if a larger model than `nomic-embed-text` is loaded or concurrent requests are high.
- **Fix:** PR to raise `resources.limits.memory` (and `requests.memory`) for the `ollama` container in `deployments.yaml`.

### Symptom: `PersistentVolumeClaim` full / model pull fails with no space left on device
- **Check:** `kubectl -n ollama exec deploy/ollama -c ollama -- df -h /root/.ollama` and `kubectl -n ollama get pvc ollama-pvc`. The PVC is only `2Gi` (`pvc.yaml`), sized for the small `nomic-embed-text` embedding model — pulling additional or larger models can exhaust it.
- **Fix:** PR to increase `spec.resources.requests.storage` in `pvc.yaml` (note: expanding a bound PVC requires the underlying StorageClass to support volume expansion).

## How-to

### Deploy / update
Edit manifests here and PR; Argo CD syncs on merge. The Deployment uses `strategy: Recreate` (not `RollingUpdate`), so updates cause a brief full outage while the old pod terminates and the new one (including the `pull-model` init container) starts.

### Check current model inventory
`kubectl -n ollama exec deploy/ollama -c ollama -- ollama list`

### Note on performance
No GPU `nodeSelector` or GPU resource request is configured (`deployments.yaml`) — inference runs on CPU only. Expect materially slower embedding/generation latency than a GPU-backed node pool; this is expected behavior, not a bug.
