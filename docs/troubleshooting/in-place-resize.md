# Resizing a running pod without restarting it

**Feature:** in-place pod resize (`InPlacePodVerticalScaling`), GA in Kubernetes 1.35; cluster on `v1.36.4+k3s1`
**Tool:** `scripts/resize-pod.sh`
**Plan:** `docs/plans/k8s-136-features-implementation-plan.md`, Phase 2

## When to use it

For a pod where a restart is an outage, change its CPU or memory while it runs. This matters
most for the single-instance stateful pods: Prometheus, Vault, Ollama and qwen. Typical reasons:

- **"Container near memory limit"** fired (Grafana → Cluster Health). The working set is above
  90% of the limit, and an OOM kill is next.
- **"Pod CPU starved"** fired and *Cluster Overview → Pod pressure → Pods most CPU-throttled*
  is also high for that pod. Its own CPU limit is the bottleneck.

If the pod can restart freely (stateless Deployments like Backstage), just change the manifest
and let it roll. Resizing in place buys nothing there.

## How

```bash
# 1. validate: local QoS check + server-side dry run, changes nothing
scripts/resize-pod.sh logging prometheus-0 prometheus cpu=250m/1500m

# 2. apply: resize, wait for the kubelet, confirm the container did NOT restart
scripts/resize-pod.sh logging prometheus-0 prometheus cpu=250m/1500m --apply

# 3. make git match (the script prints the exact values, and whether merging is safe)
```

Values are `REQUEST/LIMIT`. Use `cpu=250m` to set only the request, or `memory=/3Gi` to set only the limit.

**Step 3 is not optional.** A resize lives only in the running pod. Pods are never compared
with git (Argo CD diffs the StatefulSet, not the pod), so nothing reverts it. But the next time
the pod is recreated, it comes back with whatever the manifest says.

## Whether merging the new values restarts the pod

This depends on the owner. The script tells you.

| Owner | Merging new resources into git |
|---|---|
| StatefulSet with `updateStrategy: OnDelete` (**Vault, Prometheus**) | **No restart.** The template changes and the running pod already matches it. This is the clean path: resize, then merge. |
| StatefulSet with `RollingUpdate` | Restarts the pod. Switch it to OnDelete first; `updateStrategy` is not part of the pod template, so the switch itself doesn't restart anything. It needs `rollingUpdate: null` in the manifest, because a client-side apply otherwise keeps the defaulted `partition` and fails validation. |
| Deployment (**Ollama, qwen, pgvector Postgres**, …) | **Starts a rollout** (new ReplicaSet). Kubernetes 1.36 has no in-place rollout for workload controllers. Here the resize is a stop-gap: merge at a planned time. |
| CloudNativePG `Cluster` | **Refused by the script.** CNPG 1.30 treats any resource change as a rolling update, which with one instance is an outage. Revisit when cloudnative-pg#11116 (`resourcesUpdateStrategy: inPlace`) ships. |

## Rules the API enforces

- **The QoS class cannot change.** BestEffort cannot gain resources in place; Burstable cannot
  become Guaranteed. The script checks this before calling the API. Changing QoS takes one
  restart, e.g. vault-0 in Task 2.3.
- **A limit cannot be removed**, only changed.
- **Requests must fit the node.** Otherwise the resize is `PodResizePending: Infeasible`, and the
  script stops.
- **Lowering memory is best-effort.** If usage is above the new limit, the kubelet leaves the
  resize in progress rather than OOM-killing the container.
- **Prometheus sets `GOMEMLIMIT` once at startup** (0.9 × limit). An in-place memory raise removes
  the OOM risk, but Go keeps its old heap target until the next restart. Prometheus ≥ 3.15 can
  refresh it (`--auto-gomemlimit.refresh-interval`).

## Checking it worked

```bash
kubectl get pod -n <ns> <pod> -o jsonpath='{.status.containerStatuses[*].resources}'   # what the kubelet applied
kubectl get pod -n <ns> <pod> -o jsonpath='{.status.containerStatuses[*].restartCount}' # must not have moved
kubectl get pod -n <ns> <pod> -o jsonpath='{.status.conditions}' | grep -i resize       # no PodResize* conditions left
```

On the node, the container's cgroup shows the new CPU limit directly (`cpu.max`, microseconds
per 100ms period).

## Rolling back

Resize again with the old values. If git was already updated, revert the commit too. For an
OnDelete StatefulSet that revert doesn't restart the pod either.
