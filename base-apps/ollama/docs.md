---
type: "Kubernetes App Guide"
title: "Ollama"
description: "Local LLM/embedding model server (Ollama, CPU-only, PVC-backed)"
app: ollama
catalog_entity: ollama
kind: docs
namespace: ollama
last_reviewed: 2026-07-10
status: current
tags: [llm, embeddings, gpu-optional]
sources:
  - base-apps/ollama/deployments.yaml
  - base-apps/ollama/pvc.yaml
  - base-apps/ollama/services.yaml
---

# ollama

## What it is
Self-hosted [Ollama](https://ollama.com) model server (`ollama/ollama:0.20.5`) providing local LLM/embedding inference in-cluster. It is a base provider — other apps call it over HTTP; it does not itself depend on any other catalogued app.

## Architecture & data flow
Single-replica `Deployment` (`deployments.yaml`, `strategy: Recreate`) scheduled onto nodes labeled `node.kubernetes.io/workload: application` via `nodeSelector`. There is no GPU `nodeSelector`/resource request anywhere in the spec, so this runs **CPU-only inference** — expect slower token/embedding throughput than a GPU-backed deployment.

An `initContainer` (`pull-model`, same `ollama/ollama:0.20.5` image) starts `ollama serve` in the background, runs `ollama pull nomic-embed-text`, then stops the server before the main container starts — so the `nomic-embed-text` embedding model is pre-pulled onto the PVC at deploy/restart time rather than lazily on first request.

The main container exposes port `11434` (`services.yaml`, Service `ollama`, `ClusterIP`) with HTTP `readinessProbe`/`livenessProbe` on `/`. Other in-cluster apps reach it at `http://ollama.ollama.svc.cluster.local:11434` — for example kagent's `embedding-model-config.yaml` configures `nomic-embed-text` against exactly that address.

## Where config lives
- Workload, image, model-pull init step, resource requests/limits: `deployments.yaml`.
- Model storage: `pvc.yaml` — a 2Gi `ReadWriteOnce` PVC (`ollama-pvc`) mounted at `/root/.ollama` in both the init container and the main container. 2Gi is sized for a small embedding model only; it is not enough headroom for large general-purpose LLMs.
- Network exposure: `services.yaml` — ClusterIP Service `ollama` on port `11434`.

## Resources
Main container: requests `cpu: 100m` / `memory: 512Mi`, limits `cpu: 1000m` / `memory: 1Gi`. Init container (model pull): requests `cpu: 200m` / `memory: 512Mi`, limits `cpu: 2000m` / `memory: 1Gi`. No GPU is requested.

## Who consumes it
kagent's embedding model config (`base-apps/kagent/embedding-model-config.yaml`) points at `ollama` for the `nomic-embed-text` embedding model via `http://ollama.ollama.svc.cluster.local:11434`.
