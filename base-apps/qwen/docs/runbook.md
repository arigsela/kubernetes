---
type: "Kubernetes App Runbook"
title: "Qwen3.5 — Runbook"
description: "Operational runbook for the qwen multimodal server: failure modes, checks, and fixes."
app: qwen
catalog_entity: qwen
kind: runbook
namespace: qwen
last_reviewed: 2026-09-27
status: current
tags: [llm, vision, multimodal, gpu-optional]
sources:
  - base-apps/qwen/deployments.yaml
  - base-apps/qwen/services.yaml
  - scripts/build-model-image.sh
---

# qwen runbook

## Failure modes

### Symptom: new pod stuck in `ContainerCreating` / `ErrImagePull` on the `models` volume
- **Check:** `kubectl -n qwen describe pod -l app=qwen` events. The weights are an image volume pulled from ECR (`models/qwen3.5-0.8b@sha256:…`). `no basic auth credentials` / 401 → the pod lacks `imagePullSecrets: ecr-registry` or the namespace's secret is stale (see `admission-policies` runbook, ECR `ImagePullBackOff`). `not found` → the digest in `deployments.yaml` was never pushed.
- **Fix:** fix the secret or push the image (`scripts/build-model-image.sh qwen --push`). The old pod keeps serving meanwhile (`maxUnavailable: 0`). A first pull on a node takes ~45s (~0.76GB); later pods on that node reuse the cached image.

### Symptom: `llama-server` CrashLoopBackOff immediately on start
- **Likely cause:** image entrypoint or flag mismatch. The `ghcr.io/ggml-org/llama.cpp:server` entrypoint is `llama-server`; flag names (`--mmproj`, `--no-mmproj-offload`, `--ctx-size`) occasionally change between builds, and a brand-new model architecture may need a newer image than the one pulled.
- **Check:** `kubectl -n qwen logs deploy/qwen -c llama-server` — look for "unknown argument", "unknown model architecture", or a failed `mmproj` load.
- **Fix:** PR to pin a newer/known-good `image:` build digest in `deployments.yaml`, and/or adjust the flag names to match that build.

### Symptom: OOMKilled on the `llama-server` container
- **Check:** `kubectl -n qwen describe pod -l app=qwen` for `OOMKilled`; `kubectl -n qwen top pod -l app=qwen`. Vision requests load the F16 projector and per-image tensors, which spikes memory above the text-only baseline.
- **Fix:** PR to raise `resources.limits.memory` in `deployments.yaml` (nodes have ample RAM headroom), or reduce `--ctx-size`.

### Symptom: vision requests fail but text works
- **Check:** confirm the server was started with `--mmproj` and that `mmproj-F16.gguf` is in the model image (`kubectl -n qwen exec deploy/qwen -c llama-server -- ls -l /models`). Some llama.cpp builds have had VLM/mmproj graph issues on certain architectures.
- **Fix:** verify with a text-only call first (`/v1/chat/completions` with a string `content`); if only vision breaks, try a newer image build or the `mmproj-BF16.gguf` variant.

## How-to

### Deploy / update
Edit manifests here and PR; Argo CD syncs on merge. `RollingUpdate` with `maxSurge: 1` / `maxUnavailable: 0`: the new pod starts next to the old one, which keeps serving until the new one passes `/health`. No outage.

### Model update
The weights change only through a new image digest:
1. Change the pinned `HF_REV`, the files and their sha256 in `scripts/build-model-image.sh`, and **bump its `TAG`** (ECR tags are immutable; the script refuses to overwrite one).
2. `scripts/build-model-image.sh qwen` (local, reproducible), then `--push`. It prints `…/models/qwen3.5-0.8b@sha256:…`.
3. PR that digest into the `models` volume in `deployments.yaml`, together with any changed `--model`/`--mmproj` file names. Merge: a surge rollout.
4. Smoke test (below). **Rollback:** revert the PR; the old digest is still in ECR.

### Smoke test
`kubectl -n qwen port-forward svc/qwen 8080:8080` then `curl localhost:8080/health` (expect `{"status":"ok"}`) and a `/v1/chat/completions` call.

### Note on performance
CPU-only inference — expect materially slower generation and image-processing latency than a GPU node pool. This is expected behavior, not a bug.
