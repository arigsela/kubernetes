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
A standalone [OpenClaw](https://docs.openclaw.ai) agent running as a plain `Deployment`, driven by the in-cluster **Qwen3.5-0.8B** model (`base-apps/qwen`). Follows OpenClaw's [official Kubernetes pattern](https://docs.openclaw.ai/install/kubernetes): the `ghcr.io/openclaw/openclaw:slim` image running `gateway run`, hardened securityContext, and **token auth**.

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

## Caveats (the honest part)
- **It is slow.** OpenClaw's agent system prompt is ~20K tokens; qwen runs it on **CPU** at ~50 tok/s prompt-eval, so the *first* turn takes minutes. This is the 0.8B/CPU reality, not a bug.
- Requires **qwen `--ctx-size` ≥ ~24K** (bumped to 32768) so the agent prompt fits — an 8K window overflows.
- A 0.8B is weak at multi-step tool-calling. Great for learning OpenClaw's mechanics; for real agent work, point the config at a bigger model (ideally on a GPU).
- If a turn errors mid-way (e.g. a timeout), the session can get a dangling message → `Cannot continue from message role: assistant`. Start fresh with `/reset` (or `/new`) in the TUI.
