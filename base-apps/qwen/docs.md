---
type: "Kubernetes App Guide"
title: "Qwen3.5-0.8B"
description: "Self-hosted multimodal (text+vision) LLM server via llama.cpp, CPU-only, weights from a pinned OCI image volume"
app: qwen
catalog_entity: qwen
kind: docs
namespace: qwen
last_reviewed: 2026-09-27
status: current
tags: [llm, vision, multimodal, gpu-optional]
sources:
  - base-apps/qwen/deployments.yaml
  - base-apps/qwen/services.yaml
  - models/qwen/Dockerfile
  - scripts/build-model-image.sh
---

# qwen

## What it is
Self-hosted [Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B) multimodal model (text + vision) served by [llama.cpp](https://github.com/ggml-org/llama.cpp)'s OpenAI-compatible `llama-server`. It is a base provider — other apps call it over HTTP; it does not depend on any other catalogued app. Runs independently of the `ollama` app so it never contends with the latency-critical embedding service used by kagent.

## Why llama.cpp (not vLLM)
This cluster has **no GPU**. vLLM's value is GPU paged-attention batching and its CPU backend does not (as of this writing) support the new Qwen3.5 architecture; llama.cpp is the engine Unsloth recommends for this model's vision, has first-class CPU support, and serves an OpenAI-compatible API. Vision works via a separate multimodal projector (`mmproj`) file.

## Architecture & data flow
Single-replica `Deployment` (`deployments.yaml`, `RollingUpdate` with `maxSurge: 1`, `maxUnavailable: 0`) scheduled onto either worker (`nodeSelector` `node.kubernetes.io/workload: application`, a pool, not a pin). There is no GPU request anywhere in the spec, so this runs **CPU-only inference** — expect slower token throughput than a GPU-backed deployment, and noticeably slower first-token latency on image (vision) requests.

The weights are an **image volume** (Kubernetes 1.36): a data-only OCI image in ECR, `models/qwen3.5-0.8b`, pinned **by digest** in `deployments.yaml` and mounted read-only at `/models`. It holds two files:

- `Qwen3.5-0.8B-UD-Q4_K_XL.gguf` — the ~0.6GB 4-bit quantized text weights.
- `mmproj-F16.gguf` — the vision projector that turns image tokens into embeddings.

`scripts/build-model-image.sh qwen` builds it (`models/qwen/Dockerfile`) from a **pinned Hugging Face revision** of `unsloth/Qwen3.5-0.8B-GGUF`, verifying each file's sha256, reproducibly (same inputs, same digest). The kubelet pulls it once per node (with the `ecr-registry` secret the `inject-ecr-pull-secret` admission policy adds) and caches it; nothing is downloaded at pod start and no node-local volume pins the pod. Until Phase 4 the files were fetched from Hugging Face's moving `main` by an init container onto a node-pinned PVC.

The main container (`ghcr.io/ggml-org/llama.cpp:server`) runs `llama-server` with `--model` + `--mmproj --no-mmproj-offload` (CPU) and exposes port `8080` (`services.yaml`, Service `qwen`, `ClusterIP`) with HTTP `readinessProbe`/`livenessProbe` on `/health`. In-cluster consumers reach it at `http://qwen.qwen.svc.cluster.local:8080` — OpenAI-compatible at `/v1/chat/completions` (supply images as `image_url` content parts).

## Where config lives
- Workload, image, model/mmproj filenames, server flags, resource requests/limits: `deployments.yaml`.
- Model weights: the image volume `models` in `deployments.yaml` (ECR digest), built by `scripts/build-model-image.sh qwen`; the pinned revision and file hashes live in that script. There is no PVC: the node-pinned `qwen-models` volume was retired in plan Task 4.4.
- Network exposure: `services.yaml` — ClusterIP Service `qwen` on port `8080`.

## Resources
Main container: requests `cpu: 1` / `memory: 2Gi`, limits `cpu: 6` / `memory: 8Gi`. No GPU is requested. The weights (~0.76GB) live in the node's image store, not in the pod's memory or ephemeral storage; `llama-server` memory-maps them.

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
