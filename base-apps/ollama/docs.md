---
type: "Kubernetes App Guide"
title: "Ollama"
description: "Local embedding model server (Ollama, CPU-only, model from a pinned OCI image volume)"
app: ollama
catalog_entity: ollama
kind: docs
namespace: ollama
last_reviewed: 2026-09-27
status: current
tags: [llm, embeddings, gpu-optional]
sources:
  - base-apps/ollama/deployments.yaml
  - base-apps/ollama/services.yaml
  - models/nomic-embed-text/Dockerfile
  - scripts/build-model-image.sh
---

# ollama

## What it is
Self-hosted [Ollama](https://ollama.com) model server (`ollama/ollama:0.20.5`) providing local LLM/embedding inference in-cluster. It is a base provider — other apps call it over HTTP; it does not itself depend on any other catalogued app.

## Architecture & data flow
Single-replica `Deployment` (`deployments.yaml`, `RollingUpdate` with `maxSurge: 1`, `maxUnavailable: 0`) scheduled onto either worker (`nodeSelector` `node.kubernetes.io/workload: application`, a pool, not a pin). There is no GPU `nodeSelector`/resource request anywhere in the spec, so this runs **CPU-only inference** — expect slower token/embedding throughput than a GPU-backed deployment.

The model is an **image volume** (Kubernetes 1.36): a data-only OCI image in ECR, `models/nomic-embed-text`, pinned **by digest** in `deployments.yaml` and mounted read-only at `/models` (`OLLAMA_MODELS=/models`, `OLLAMA_NOPRUNE=1`). `scripts/build-model-image.sh nomic-embed-text` builds it (`models/nomic-embed-text/Dockerfile`) from Ollama tag `v1.5` and **asserts the model digest** (`sha256:970aa74c…`): kagent's stored memory vectors were made with this exact model, and a different one would silently make them unsearchable. The image's manifest is also named `latest`, which is what kagent asks for. `/root/.ollama` is an `emptyDir` for ollama's identity keypair only. Until Phase 4 an init container ran `ollama pull nomic-embed-text` (the registry's moving `latest`) onto a node-pinned PVC at every start.

The main container exposes port `11434` (`services.yaml`, Service `ollama`, `ClusterIP`) with HTTP `readinessProbe`/`livenessProbe` on `/`. Other in-cluster apps reach it at `http://ollama.ollama.svc.cluster.local:11434` — for example kagent's `embedding-model-config.yaml` configures `nomic-embed-text` against exactly that address.

## Where config lives
- Workload, image, model volume (ECR digest), resource requests/limits: `deployments.yaml`.
- The model's pinned tag and digest: `scripts/build-model-image.sh`. There is no PVC: the node-pinned `ollama-pvc` volume was retired in plan Task 4.4.
- Network exposure: `services.yaml` — ClusterIP Service `ollama` on port `11434`.

## Resources
Main container: requests `cpu: 250m` / `memory: 1Gi`, limits `cpu: 3` / `memory: 2Gi` (`OLLAMA_KEEP_ALIVE=-1` keeps the ~274MB model resident, so the cold reload never lands on a kagent call). No GPU is requested. The model file lives in the node's image store, not in the pod's memory or ephemeral storage.

## Who consumes it
kagent's embedding model config (`base-apps/kagent/embedding-model-config.yaml`) points at `ollama` for the `nomic-embed-text` embedding model via `http://ollama.ollama.svc.cluster.local:11434`.
