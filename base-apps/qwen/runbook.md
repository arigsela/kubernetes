---
type: "Kubernetes App Runbook"
title: "Qwen3.5 — Runbook"
description: "Operational runbook for the qwen multimodal server: failure modes, checks, and fixes."
app: qwen
catalog_entity: qwen
kind: runbook
namespace: qwen
last_reviewed: 2026-07-21
status: current
tags: [llm, vision, multimodal, gpu-optional]
sources:
  - base-apps/qwen/deployments.yaml
  - base-apps/qwen/pvc.yaml
  - base-apps/qwen/services.yaml
---

# qwen runbook

## Failure modes

### Symptom: pod stuck in `Init` / server never becomes ready
- **Check:** `kubectl -n qwen logs deploy/qwen -c download-model` — the init container downloads the GGUF + mmproj from Hugging Face; a slow or failed download blocks the main container. Confirm files landed with `kubectl -n qwen exec deploy/qwen -c llama-server -- ls -lh /models`.
- **Fix:** transient network/registry failures → `kubectl -n qwen delete pod -l app=qwen` to retry the init container. If Hugging Face URLs changed, PR the `BASE`/`MODEL`/`MMPROJ` values in `deployments.yaml`.

### Symptom: `llama-server` CrashLoopBackOff immediately on start
- **Likely cause:** image entrypoint or flag mismatch. The `ghcr.io/ggml-org/llama.cpp:server` entrypoint is `llama-server`; flag names (`--mmproj`, `--no-mmproj-offload`, `--ctx-size`) occasionally change between builds, and a brand-new model architecture may need a newer image than the one pulled.
- **Check:** `kubectl -n qwen logs deploy/qwen -c llama-server` — look for "unknown argument", "unknown model architecture", or a failed `mmproj` load.
- **Fix:** PR to pin a newer/known-good `image:` build digest in `deployments.yaml`, and/or adjust the flag names to match that build.

### Symptom: OOMKilled on the `llama-server` container
- **Check:** `kubectl -n qwen describe pod -l app=qwen` for `OOMKilled`; `kubectl -n qwen top pod -l app=qwen`. Vision requests load the F16 projector and per-image tensors, which spikes memory above the text-only baseline.
- **Fix:** PR to raise `resources.limits.memory` in `deployments.yaml` (nodes have ample RAM headroom), or reduce `--ctx-size`.

### Symptom: `PersistentVolumeClaim` full / download fails with no space left
- **Check:** `kubectl -n qwen exec deploy/qwen -c llama-server -- df -h /models`; `kubectl -n qwen get pvc qwen-models`.
- **Fix:** the PVC is 5Gi on `local-path`, which **cannot be expanded in place** (`allowVolumeExpansion=false`). To grow it: PR a larger `storage:` in `pvc.yaml`, then delete the PVC + pod so Argo CD recreates them (the model re-downloads on next start).

### Symptom: vision requests fail but text works
- **Check:** confirm the server was started with `--mmproj` and that `mmproj-F16.gguf` exists on the PVC. Some llama.cpp builds have had VLM/mmproj graph issues on certain architectures.
- **Fix:** verify with a text-only call first (`/v1/chat/completions` with a string `content`); if only vision breaks, try a newer image build or the `mmproj-BF16.gguf` variant.

## How-to

### Deploy / update
Edit manifests here and PR; Argo CD syncs on merge. `strategy: Recreate` means updates cause a brief full outage while the old pod terminates and the new one (including the `download-model` init container) starts.

### Smoke test
`kubectl -n qwen port-forward svc/qwen 8080:8080` then `curl localhost:8080/health` (expect `{"status":"ok"}`) and a `/v1/chat/completions` call.

### Note on performance
CPU-only inference — expect materially slower generation and image-processing latency than a GPU node pool. This is expected behavior, not a bug.
