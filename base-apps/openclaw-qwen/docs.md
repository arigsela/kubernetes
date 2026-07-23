---
type: "Kubernetes App Guide"
title: "OpenClaw + qwen (standalone)"
description: "Standalone OpenClaw agent (official image, token auth) driven by the local Qwen3.5-0.8B model"
app: openclaw-qwen
catalog_entity: openclaw-qwen
kind: docs
namespace: openclaw-qwen
last_reviewed: 2026-07-22
status: current
tags: [llm, agent, coding-agent, local, gpu-optional]
sources:
  - base-apps/openclaw-qwen/deployment.yaml
  - base-apps/openclaw-qwen/configmap.yaml
  - base-apps/openclaw-qwen/secret.yaml
---

# openclaw-qwen

## What it is
A standalone [OpenClaw](https://docs.openclaw.ai) agent running as a plain `Deployment`, driven by **Qwen3.5-9B served on a GPU** (LM Studio on the gaming PC at `http://10.0.1.200:1234`). Follows OpenClaw's [official Kubernetes pattern](https://docs.openclaw.ai/install/kubernetes): the `ghcr.io/openclaw/openclaw:slim` image running `gateway run`, hardened securityContext, and **token auth**.

## Why this shape (two things that matter)
1. **Token auth, not `--auth none`.** OpenClaw gates `operator.write` (the ability to *send*) behind an operator identity. Shared-secret **token** auth auto-approves the connecting client as **operator**; `none` leaves it read-only. The gateway reads `OPENCLAW_GATEWAY_TOKEN` (from `secret.yaml`); a client that inherits that env authenticates as operator.
2. **Standalone, not a kagent AgentHarness.** A plain Deployment has no openshell egress proxy, so OpenClaw reaches `qwen.qwen.svc.cluster.local:8080` directly (the harness proxy silently dropped those calls).

## How to use it
The gateway binds **loopback** inside the pod; reach it via `kubectl exec` (the `OPENCLAW_GATEWAY_TOKEN` env in the pod authenticates you as operator automatically):

```bash
# interactive agent TUI
kubectl -n openclaw-qwen exec -it deploy/openclaw-qwen -- openclaw tui

# one-shot agent turn (non-interactive)
kubectl -n openclaw-qwen exec deploy/openclaw-qwen -- openclaw agent -m "your prompt" --agent default

# direct one-shot inference (fastest; skips the agent tool-prompt)
kubectl -n openclaw-qwen exec deploy/openclaw-qwen -- \
  openclaw infer model run --model openai/Qwen3.5-0.8B --prompt "hi" --local
```

## Architecture & config
- `deployment.yaml` — official image + `gateway run`, init container seeds config into the writable PVC home, hardened securityContext (non-root, read-only rootfs, dropped caps).
- `configmap.yaml` — `openclaw.json` (`gateway.auth.mode: token`, `openai` provider → qwen, default model `openai/Qwen3.5-0.8B`) + `AGENTS.md`.
- `secret.yaml` — `OPENCLAW_GATEWAY_TOKEN` (operator token).
- `pvc.yaml` — 5Gi writable home (sessions/workspace). `service.yaml` — ClusterIP 18789 (for port-forward).

## Notes
- **Model runs off-cluster on the gaming PC's GPU** (LM Studio, `10.0.1.200:1234`, same LAN subnet as the nodes). Verified reachable from a cluster pod; a full turn returns in **seconds** (vs. ~8 min on the old CPU 0.8B).
- **LM Studio must load the model with context length ≥ ~24K** — OpenClaw's agent prompt is ~20K tokens; the default 4K overflows.
- The model reasons (`reasoning_content`) before answering; OpenClaw handles this, and it's fast on the GPU.
- To switch models, edit the `openai` provider `baseUrl`/`models[].id` and `agents.defaults.model.primary` here.
- Earlier history: this app previously pointed at the in-cluster CPU `Qwen3.5-0.8B` (`base-apps/qwen`), which worked but was too slow (~8 min/turn) for interactive use.
