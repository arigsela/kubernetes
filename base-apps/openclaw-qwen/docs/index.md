---
type: "Kubernetes App Guide"
title: "OpenClaw + qwen (standalone)"
description: "Standalone OpenClaw coding agent driven by the local Qwen3.5-0.8B model, no openshell sandbox"
app: openclaw-qwen
catalog_entity: openclaw-qwen
kind: docs
namespace: openclaw-qwen
last_reviewed: 2026-07-22
status: current
tags: [llm, agent, coding-agent, local, gpu-optional]
sources:
  - base-apps/openclaw-qwen/configmap.yaml
  - base-apps/openclaw-qwen/deployment.yaml
---

# openclaw-qwen

## What it is
A **standalone** [OpenClaw](https://github.com/kagent-dev) coding/ops agent (a Claude-Code-style TUI agent) running as a plain `Deployment`, driven by the in-cluster **Qwen3.5-0.8B** model (`base-apps/qwen`, llama.cpp, CPU). Lives in its own `openclaw-qwen` namespace — deliberately NOT the pre-existing, unrelated `openclaw` namespace (which owns `svc/ingress openclaw.arigsela.com`).

## Why standalone (not a kagent AgentHarness)
kagent's `AgentHarness` runs OpenClaw inside an **openshell sandbox**, which forces outbound traffic through an egress proxy. That proxy drops OpenClaw's calls to the *internal* `qwen.qwen.svc.cluster.local` service, so every agent turn failed with `[assistant turn failed before producing content]` — the request never reached qwen. Running OpenClaw as a plain Deployment removes the proxy: OpenClaw talks straight to qwen. Verified: a one-shot turn returns a real answer and the request lands in qwen's logs.

## Architecture & data flow
Single-replica `Deployment` (`strategy: Recreate`) on nodes labeled `node.kubernetes.io/workload: application`. An `initContainer` copies `openclaw.json` from the ConfigMap into the pod's writable `HOME` (`/state/.openclaw/`, an `emptyDir`). The main container runs `openclaw gateway run --auth none --bind loopback --port 18800`.

The config points OpenClaw's `openai` provider at `http://qwen.qwen.svc.cluster.local:8080/v1` (model `Qwen3.5-0.8B`, placeholder api key — llama.cpp ignores it) and sets it as the default agent model. Inference happens in the qwen pod, so this container stays light.

## How to interact
The gateway binds **loopback only** — it is not exposed on the cluster network (no Service). You reach it from inside the pod via `kubectl exec`:

```bash
# interactive agent TUI (connects to the loopback gateway)
kubectl -n openclaw-qwen exec -it deploy/openclaw-qwen -- openclaw tui

# or the local-embedded chat TUI (no gateway needed)
kubectl -n openclaw-qwen exec -it deploy/openclaw-qwen -- openclaw chat

# non-interactive one-shot (good for a smoke test)
kubectl -n openclaw-qwen exec deploy/openclaw-qwen -- \
  openclaw infer model run --model openai/Qwen3.5-0.8B --prompt "Say hi in one word" --local
```
Confirm you're on qwen: the TUI status bar should show `openai/Qwen3.5-0.8B` (not `gpt-5.5`).

## Where config lives
- Model provider, gateway mode, default model: `configmap.yaml` (`openclaw.json`).
- Workload, image (pinned by digest), init seed, resources: `deployment.yaml`.
- No Service by design (loopback-only). No secrets (qwen needs no auth).

## Caveats
- **0.8B on CPU**: agent turns are slow and the model is weak at multi-step tool-calling — expect sluggish, rough behavior. The *plumbing* is correct; the model is the limit. For real work, point the config at a bigger model (and ideally a GPU).
- State (`/state`) is an `emptyDir` — sessions/workspace do not survive a pod restart. Add a PVC if you need persistence.
