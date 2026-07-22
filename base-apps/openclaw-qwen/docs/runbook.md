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
  - base-apps/openclaw-qwen/configmap.yaml
  - base-apps/openclaw-qwen/deployment.yaml
---

# openclaw-qwen runbook

## Failure modes

### Symptom: `[assistant turn failed before producing content]` / turns never reach qwen
- **Check:** from inside the pod, `kubectl -n openclaw-qwen exec deploy/openclaw-qwen -- curl -sS -m10 http://qwen.qwen.svc.cluster.local:8080/health` (expect `{"status":"ok"}`), and `kubectl -n qwen logs deploy/qwen -c llama-server --since=2m | grep task` to see if requests land.
- **Fix:** if the health check fails, qwen is down (see `base-apps/qwen`) or network policy is blocking. If health is OK but no requests land, confirm `HOME=/state` and that `/state/.openclaw/openclaw.json` exists and points at the qwen baseUrl (`exec ... -- cat /state/.openclaw/openclaw.json`). Unlike the openshell harness, this pod has no egress proxy, so proxy interception is not a factor here.

### Symptom: TUI shows model `gpt-5.5` instead of `openai/Qwen3.5-0.8B`
- **Check:** the config wasn't loaded — `exec ... -- sh -c 'HOME=/state openclaw config validate'` and inspect `/state/.openclaw/openclaw.json`.
- **Fix:** ensure `HOME=/state` is set (env in `deployment.yaml`) and the init container seeded the config. Delete the pod to re-seed. `gpt-5.5` is OpenClaw's built-in fallback when it can't resolve the configured provider.

### Symptom: pod CrashLoop / liveness failing
- **Check:** `kubectl -n openclaw-qwen logs deploy/openclaw-qwen` and `kubectl -n openclaw-qwen describe pod -l app=openclaw-qwen`. The liveness probe greps `/proc/net/tcp` for the gateway port (18800 = `:4970`); if the gateway never binds, the probe fails.
- **Fix:** check the gateway startup logs for config errors; verify the pinned image still ships `openclaw`. Raise `initialDelaySeconds` if the gateway is slow to bind on first start.

## How-to

### Deploy / update
Edit manifests here and PR; Argo CD syncs on merge. Config changes require a pod restart (`strategy: Recreate`) so the init container re-seeds `openclaw.json`.

### Smoke test (does qwen answer?)
```bash
kubectl -n openclaw-qwen exec deploy/openclaw-qwen -- \
  openclaw infer model run --model openai/Qwen3.5-0.8B --prompt "Reply with one word: what color is grass?" --local
```

### Point at a bigger / different model
Edit `configmap.yaml`: change `models.providers.openai.baseUrl` + `models[].id` and `agents.defaults.model.primary`, then PR. (A larger model is the real fix for weak agent behavior.)

### Note on performance
Inference runs on the CPU-only qwen pod (Qwen3.5-0.8B). Agent turns are slow and the model is weak at tool-calling — expected, not a bug.
