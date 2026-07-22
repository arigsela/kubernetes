---
type: "Kubernetes App Runbook"
title: "OpenClaw + qwen — Runbook"
description: "Operational runbook for the standalone OpenClaw agent: failure modes, checks, and fixes."
app: openclaw-qwen
catalog_entity: openclaw-qwen
kind: runbook
namespace: openclaw-qwen
last_reviewed: 2026-07-22
status: current
tags: [llm, agent, coding-agent, local, gpu-optional]
sources:
  - base-apps/openclaw-qwen/deployment.yaml
  - base-apps/openclaw-qwen/configmap.yaml
  - base-apps/openclaw-qwen/secret.yaml
---

# openclaw-qwen runbook

## Failure modes

### Symptom: TUI connects but sending fails with `missing scope: operator.write`
- **Cause:** the gateway is not using token auth, or the client isn't presenting the token.
- **Check:** `kubectl -n openclaw-qwen exec deploy/openclaw-qwen -- sh -c 'echo $OPENCLAW_GATEWAY_TOKEN | head -c8'` (should be non-empty) and confirm `configmap.yaml` has `gateway.auth.mode: token`.
- **Fix:** ensure `secret.yaml` exists and the deployment mounts `OPENCLAW_GATEWAY_TOKEN`. Token (shared-secret) auth auto-approves the client as **operator**; `mode: none` leaves it read-only.

### Symptom: agent turns fail — `exceeds context size` / `[assistant turn failed before producing content]`
- **Cause:** OpenClaw's agent system prompt (~20K tokens) is larger than qwen's context window.
- **Check:** `kubectl -n qwen logs deploy/qwen -c llama-server --tail=60 | grep n_ctx_slot` (needs to be ≥ ~24K) and grep for `exceeds the available context size`.
- **Fix:** qwen must run `--ctx-size` ≥ ~24K (set to 32768 in `base-apps/qwen/deployments.yaml`). If it reverted to 8192, re-sync `base-apps/qwen`.

### Symptom: `Cannot continue from message role: assistant`
- **Cause:** a previous turn errored mid-way and left a dangling assistant message in the session.
- **Fix:** start a fresh session — `/reset` (or `/new`) in the TUI, or clear the session file: `kubectl -n openclaw-qwen exec deploy/openclaw-qwen -- rm -f /home/node/.openclaw/agents/default/sessions/*.jsonl`.

### Symptom: pod CrashLoop / not ready
- **Check:** `kubectl -n openclaw-qwen logs deploy/openclaw-qwen -c gateway` and `-c init-config`. Readiness hits `127.0.0.1:18789/readyz`.
- **Fix:** verify the pinned image still exposes `gateway run`; check the init container seeded `/home/node/.openclaw/openclaw.json` (the PVC must be writable, fsGroup 1000).

## How-to

### Deploy / update
Edit manifests here and PR; Argo CD syncs on merge. Config changes require a pod restart (`Recreate`) so the init container re-seeds `openclaw.json`.

### Smoke test (does qwen answer?)
```bash
kubectl -n openclaw-qwen exec deploy/openclaw-qwen -- \
  openclaw infer model run --model openai/Qwen3.5-0.8B --prompt "Reply with one word: grass color?" --local
```

### Point at a bigger / different model
Edit `configmap.yaml`: change `models.providers.openai.baseUrl` + `models[].id` and `agents.defaults.model.primary`, then PR. A larger model (ideally on a GPU) is the real fix for slow/weak agent behavior.

### Note on performance
The agent's ~20K-token prompt runs on the CPU-only qwen pod at ~50 tok/s prompt-eval, so the first turn takes minutes. Expected for a 0.8B on CPU, not a bug.
