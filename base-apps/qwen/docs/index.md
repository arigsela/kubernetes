---
type: "Kubernetes App Guide"
title: "Qwen3.5-0.8B"
description: "Self-hosted multimodal (text+vision) LLM server via llama.cpp, CPU-only, PVC-backed"
app: qwen
catalog_entity: qwen
kind: docs
namespace: qwen
last_reviewed: 2026-07-21
status: current
tags: [llm, vision, multimodal, gpu-optional]
sources:
  - base-apps/qwen/deployments.yaml
  - base-apps/qwen/pvc.yaml
  - base-apps/qwen/services.yaml
---

# qwen

## What it is
Self-hosted [Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B) multimodal model (text + vision) served by [llama.cpp](https://github.com/ggml-org/llama.cpp)'s OpenAI-compatible `llama-server`. It is a base provider — other apps call it over HTTP; it does not depend on any other catalogued app. Runs independently of the `ollama` app so it never contends with the latency-critical embedding service used by kagent.

## Why llama.cpp (not vLLM)
This cluster has **no GPU**. vLLM's value is GPU paged-attention batching and its CPU backend does not (as of this writing) support the new Qwen3.5 architecture; llama.cpp is the engine Unsloth recommends for this model's vision, has first-class CPU support, and serves an OpenAI-compatible API. Vision works via a separate multimodal projector (`mmproj`) file.

## Architecture & data flow
Single-replica `Deployment` (`deployments.yaml`, `strategy: Recreate`) scheduled onto nodes labeled `node.kubernetes.io/workload: application` via `nodeSelector`. There is no GPU request anywhere in the spec, so this runs **CPU-only inference** — expect slower token throughput than a GPU-backed deployment, and noticeably slower first-token latency on image (vision) requests.

An `initContainer` (`download-model`, `curlimages/curl`) fetches two files from the Unsloth GGUF repo onto the PVC before the server starts:

- `Qwen3.5-0.8B-UD-Q4_K_XL.gguf` — the ~0.6GB 4-bit quantized text weights.
- `mmproj-F16.gguf` — the vision projector that turns image tokens into embeddings.

The download is idempotent (skips files already present), so restarts do not re-download.

The main container (`ghcr.io/ggml-org/llama.cpp:server`) runs `llama-server` with `--model` + `--mmproj --no-mmproj-offload` (CPU) and exposes port `8080` (`services.yaml`, Service `qwen`, `ClusterIP`) with HTTP `readinessProbe`/`livenessProbe` on `/health`. In-cluster consumers reach it at `http://qwen.qwen.svc.cluster.local:8080` — OpenAI-compatible at `/v1/chat/completions` (supply images as `image_url` content parts).

## Where config lives
- Workload, image, model/mmproj filenames, server flags, resource requests/limits: `deployments.yaml`.
- Model storage: `pvc.yaml` — a 5Gi `ReadWriteOnce` PVC (`qwen-models`) mounted at `/models`. The only StorageClass (`local-path`) has `allowVolumeExpansion=false`, so the PVC cannot be grown in place — it must be recreated to resize.
- Network exposure: `services.yaml` — ClusterIP Service `qwen` on port `8080`.

## Resources
Main container: requests `cpu: 1` / `memory: 2Gi`, limits `cpu: 6` / `memory: 8Gi`. Init container (download): requests `cpu: 200m` / `memory: 256Mi`, limits `cpu: 2` / `memory: 512Mi`. No GPU is requested.

## Consuming the API
```bash
kubectl -n qwen port-forward svc/qwen 8080:8080
# text
curl localhost:8080/v1/chat/completions -H 'content-type: application/json' -d '{
  "messages":[{"role":"user","content":"Say hi in one word."}]}'
# vision (image_url content part)
curl localhost:8080/v1/chat/completions -H 'content-type: application/json' -d '{
  "messages":[{"role":"user","content":[
    {"type":"text","text":"What is in this image?"},
    {"type":"image_url","image_url":{"url":"https://example.com/pic.jpg"}}]}]}'
```

## Caveats
- 0.8B is a small model — good for prototyping, classification, extraction, and vision experiments, **not** reliable multi-step reasoning or agentic tool-calling.
- The container image tag (`:server`) and llama-server flag names are the most likely things to need a one-time adjustment after watching the first pod start; see the runbook.
