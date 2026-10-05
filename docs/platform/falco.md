# Falco

**Status:** living. Reviewed 2026-09-30 against `base-apps/falco.yaml`, `base-apps/logging/grafana-alerting.yaml`, `base-apps/logging/alloy-config.yaml` and `base-apps/n8n/workflows-configmap.yaml`.

## What it is
Falco is the runtime threat-detection DaemonSet. It watches syscalls on every node and emits a JSON line per rule match. It is a **Helm-only** Argo CD Application, `base-apps/falco.yaml`, with no `base-apps/falco/` directory:
- chart `falco` **9.2.0** from `https://falcosecurity.github.io/charts`, which is Falco **0.45.0** with container plugin **0.7.4**;
- namespace `falco`, `prune` and `selfHeal` on.

It has no sidecar or sink of its own. Detections travel Falco stdout → Alloy → Loki → a Grafana alert rule → the n8n webhook → Slack.

## Architecture & data flow
- **Coverage:** every node. There is no `nodeSelector`, on purpose, and control-plane/master tolerations cover `k3s-control-01`. `driver.kind: auto`. Resources: 100m / 512Mi requested, 1 CPU / 1Gi limit. `falcosidekick` and the Kubernetes metadata collector are disabled.
- **Rules:** the chart's default (stable) ruleset, plus three rules that ship disabled and are switched on in `customRules` → `override-smoke-test.yaml`:

  | Rule | Priority | Alerts? |
  |---|---|---|
  | `Terminal shell in container` | NOTICE | No. Loki only (fires on every `kubectl exec -it`). |
  | `Contact K8S API Server From Container` | NOTICE | Yes, except from namespaces `argo-cd`, `backstage` and `kagent`, and from three known Argo callers in `argo-workflows` (see below). |
  | `Read sensitive file untrusted` | WARNING | Yes, from any namespace. |

- **Output threshold `priority: notice`.** Falco's `priority` is an output filter: anything below it is dropped before stdout. It was `warning` until 2026-07-14, which silently discarded both NOTICE rules. Falco ran for months with zero detections while reporting healthy.
- **Output format:** `json_output: true` with `json_include_output_property`, logging to stderr. The Alloy DaemonSet (`base-apps/logging/alloy-config.yaml`) ships every pod's logs to Loki with a `namespace` label, and Loki keeps 30 days (`retention_period: 720h`).
- **Alerting:** Grafana rule `falco-sensitive-detection`, in group `agent-guardrails` (folder *Agent Guardrails*, evaluated every 5 min). It is an instant Loki query over the last hour:
  ```
  sum(count_over_time({namespace="falco"} |= "priority" | json
    | (rule="Read sensitive file untrusted"
       or rule="Contact K8S API Server From Container"
       and output_fields_k8s_ns_name!~"argo-cd|backstage|kagent"
       and <three argo-workflows exclusions>) [1h]))  > 0
  ```
  LogQL binds `and` tighter than `or`, so every exclusion applies only to the API-contact rule. `argo-workflows` is not excluded as a namespace: the Argo server has no login, so an unexpected API call there is worth an alert. Only these three callers are dropped, each pinned to its binary:
  - `/usr/bin/argoexec` in Argo's `wait`/`init` containers, which every step pod uses to report its result;
  - `/usr/bin/workflow-controller`;
  - `python3` in image-scan's `discover-images` pod, which lists pods by design.

  Before 2026-10-05, every weekly image scan fired this alert (104 events on 2026-10-04, all from these callers). The full expression and its rationale are in `grafana-alerting.yaml`. The rule's settings are `severity: warning`, `for: 0m`, `noDataState: OK`.
- **Delivery:** contact point `n8n` POSTs to `http://n8n.n8n.svc.cluster.local:5678/webhook/grafana-alerts`. The GitOps-managed n8n workflow `grafana-alerts-slack` (`base-apps/n8n/workflows-configmap.yaml`) formats the message and posts it to Slack `#oncall-alerts`. The notification policy is `group_by: [alertname]`, `group_wait: 30s`, `group_interval: 5m`, `repeat_interval: 24h`.

## Where config lives
- **Chart version, values and custom rules:** `base-apps/falco.yaml`.
- **Alert rule, contact point and policy:** `base-apps/logging/grafana-alerting.yaml` (ConfigMap `grafana-alerting`).
- **Log shipping:** `base-apps/logging/alloy-config.yaml`. **Retention:** `base-apps/logging/loki-config.yaml`.
- **Slack formatting:** `base-apps/n8n/workflows-configmap.yaml` (`grafana-alerts.json`).

## How to see detections
In Grafana Explore (Loki datasource), `https://grafana.arigsela.com`:
```
{namespace="falco"} | json | rule=~".+"                                   # every detection
{namespace="falco"} |= "priority" | json | rule="Terminal shell in container"
{namespace="falco"} |= "priority" | json | output_fields_k8s_ns_name="vault"   # one namespace
```
Or read the pods directly: `kubectl logs -n falco -l app.kubernetes.io/name=falco --all-containers | grep '"priority"'`. Firing state and history are under Alerting → Alert rules → *Agent Guardrails*.

## Gotchas & tribal knowledge
- **A healthy-looking Falco can be doing nothing.** The threshold trap is one way (above). A crash loop is another (below). The alert uses `noDataState: OK`, so silence from a dead or muted Falco looks exactly like a calm cluster. The alert's own summary says it: "If this alert never fires again, suspect the threshold before assuming the cluster is calm."
- **`notice` is deliberate, not maximal.** `informational`/`debug` would bury the signal. That's why the alert filters on specific rule names instead of any Falco line.
- **Rule names are case-sensitive**, and it's `K8S`. **A missing rule stops Falco loading the whole override file**: "Rule has 'enabled' key but no rule by that name already exists". Two rules were dropped for that reason when the stable pack removed them: `Write below etc` and `Launch Package Management Process in Container`.
- **Overrides need the explicit schema** (`override: {enabled: replace}` + `enabled: true`). Falco 0.40+ doesn't load the bare `enabled: true` form.
- **The alert depends on Falco's JSON field names** (`rule`, `output_fields.k8s.ns.name` → `output_fields_k8s_ns_name` after `| json`). An upgrade that renames or stops populating them breaks the alert silently.

## Runbook
### Symptom: Falco pods crash-looping (no detection on that node, and no alert)
- **How you'd notice:** nothing pages you. Falco's own health isn't covered by the Falco rule (`noDataState: OK`) or by the cluster-health rules (CPU starvation and memory-limit only). The signals are:
  - the Argo `falco` app going unhealthy (DaemonSet not fully available);
  - `scripts/hop-verify.sh gate` failing, since it requires every app Synced+Healthy;
  - a periodic look at `kubectl -n falco get pods -o wide` (one pod per node, all Running, restarts not climbing).
- **Check:** `kubectl -n falco logs <pod> --previous`, then `kubectl -n falco describe pod <pod>`.
- **Fix, by cause:**
  - **The known case** (2026-09-29): container plugin 0.6.3 (Falco 0.43.1, chart 8.0.2) nil-pointer panicked while listing containers on a node with stale NotReady sandboxes left from a reboot. That was k3s-worker-02, after the first Ansible patch run. Chart 9.2.0 (plugin 0.7.x) guards those fields, so a recurrence after a reboot on 9.2.0+ is a new bug. Read the panic, then check upstream.
  - **A rules-load error** (the log names the rule): fix the override in `base-apps/falco.yaml`.
  - **OOMKilled:** the limit is 1Gi. The generic `container-near-memory-limit` alert should have warned first.

### Symptom: Falco Running, but no detections at all
- **Check:** run a canary. `kubectl exec -it <any pod with a shell> -- sh`, then `exit`. Within a minute, `{namespace="falco"} | json | rule="Terminal shell in container"` should show it. The rule needs a TTY, so use `-it`. To prove the whole alert path, read `/etc/shadow` from inside a container (`cat /etc/shadow`). That should trigger `Read sensitive file untrusted` and **will** send the real Slack alert.
- **Fix:**
  - **Nothing in Loki but lines in `kubectl logs`:** Alloy isn't shipping (`base-apps/logging/runbook.md`).
  - **Nothing in the logs either:** check `falco.priority` in `base-apps/falco.yaml` and that the three overrides loaded (startup log).

### Symptom: the alert fires in Grafana but nothing reaches Slack
- **Check:** `curl` the webhook from inside the cluster, or look in n8n for workflow *Grafana Alerts to Slack*. If it's missing or inactive, Grafana's POSTs 404.
- **Fix:** the n8n Deployment's `import-workflows` initContainer imports the workflow from `base-apps/n8n/workflows-configmap.yaml` at pod start. Editing it only takes effect when the pod rolls, so bump `checksum/workflows` in `base-apps/n8n/deployments.yaml` or `kubectl rollout restart deploy/n8n -n n8n`. `SLACK_BOT_TOKEN` must be present in n8n's env.

### Symptom: the alert is permanently firing
- **Check:** group the last hour by rule and namespace: `sum by (rule, output_fields_k8s_ns_name) (count_over_time({namespace="falco"} |= "priority" | json [1h]))`.
- **Fix:** if a controller that talks to the API by design has appeared, add its namespace to the `!~` list **on the API-contact clause only**. Never exclude a namespace from `Read sensitive file untrusted`. A single event keeps the rule firing for up to an hour (1 h window), and repeats are throttled to 24 h.

### Upgrading Falco
1. Read the Falco and chart release notes for every version crossed. Check that every values key the Application sets still exists in the new chart's `values.yaml`. That check was done for 9.2.0, which also started mounting the container-engine socket **directory**, so Falco survives a runtime restart.
2. Check that the three override rule names still exist in the new stable rules pack, with exact case: `kubectl -n falco exec ds/falco -c falco -- grep '^- rule:' /etc/falco/falco_rules.yaml` on the new version, or the rules file in the release. One missing name and Falco won't load the override file.
3. Bump `targetRevision`, one change per PR. After the sync, confirm one Running pod per node, then run the canary above and confirm the event still carries `rule` and `output_fields.k8s.ns.name`.
