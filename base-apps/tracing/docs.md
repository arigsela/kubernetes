---
type: "Kubernetes App Guide"
title: "Tracing"
description: "Distributed tracing: the OpenTelemetry Collector (scrubs, samples, fans out) and Tempo 3.x on S3, queried from Grafana; added for beacon's Phase 3"
app: tracing
catalog_entity: tracing
kind: docs
namespace: tracing
last_reviewed: 2026-10-21
status: draft
tags: [tempo, tracing, opentelemetry]
sources:
  - base-apps/tracing.yaml
  - base-apps/tracing/tempo.yaml
  - base-apps/tracing/collector.yaml
  - base-apps/opentelemetry-operator.yaml
  - base-apps/beacon/instrumentation.yaml
  - base-apps/tracing/smoke-job.yaml
  - appsets/managed-apps/tracing-aws-infrastructure.yaml
  - base-apps/tracing-aws-infrastructure/s3-bucket.yaml
  - base-apps/tracing-aws-infrastructure/s3-lifecycle.yaml
  - base-apps/tracing-aws-infrastructure/iam.yaml
  - base-apps/logging/grafana-deployment.yaml
  - tests/beacon/test_tracing.py
  - tests/beacon/test_collector.py
  - tests/agent-capability/test_agent_traces.py
  - base-apps/beacon/network-policies.yaml
---

# Tracing

## What it is
The cluster's trace store. Before it, Coroot was the only place traces went (kagent and
Substrate exported there directly, and Coroot refused kagent's gzip). This app was built for beacon's
Phase 3 (`arigsela/beacon` SPEC, beacon §T.18 to beacon §T.23): proving that a service with
no tracing code still shows up in Tempo because platform policy instruments it. Every
comment cites beacon's sections as `beacon §T.n` / `beacon §V.n`, never a bare `§T.n` (that
is this repo's own SPEC.md).

| Workload | Kind | What it does |
|---|---|---|
| `tempo` | Deployment, 1 replica | Tempo 3.1 single binary: receives OTLP gRPC on 4317, keeps recent traces in its live-store, writes blocks to S3, serves queries on 3200. No Kafka in this mode. |
| `otel-collector` | Deployment, 1 replica | OTLP in on 4317 (gRPC) and 4318 (HTTP). Scrubs URLs, tail-samples, sends to Tempo and to Coroot. Counts spans per service on 8889 for Prometheus. |
| `tracing-smoke` | PreSync Job | Runs every third-party image of beacon's Phase 3 on a node before any of it rolls out (beacon §V.18): Tempo, the collector, the OpenTelemetry Operator and its Python layer. |

## The collector (`collector.yaml`)
Every trace source sends to `otel-collector.tracing.svc.cluster.local`: 4318 for OTLP/HTTP
(beacon's services; the Python SDK ships only the HTTP exporter), 4317 for gRPC (Istio, from
beacon §T.20; kagent's controller and sessions and Substrate's atenet-router,
`base-apps/kagent.yaml` and `base-apps/substrate.yaml`). Nothing else exports OTLP to Coroot
(`tests/agent-capability/test_agent_traces.py`). In order:
1. `memory_limiter` first in every traces pipeline (beacon §V.41): refuse, don't OOM.
2. `filter/noise` drops kagent's `TaskStoreService/UpdateTask` spans: one per streamed status
   update, 524 of a 617-span agent run, and nothing a dashboard reads.
3. `transform/genai-cost` stamps `agent.cost.estimate_usd` on each model call
   (`generate_content` span) of a priced model, from its token counts at Anthropic's list
   prices (the table is in the config, dated). A model without a row gets none.
4. `transform/scrub` (beacon §V.42): strips the query string from `http.url`, `url.full` and
   `http.target`, drops `url.query`, and cuts the notifier's outgoing `url.full` to
   `scheme://host`. Envoy's `http.url` keeps the query (OAuth `code`/`state`, search text),
   and a Slack webhook path is itself the credential. It also deletes
   `gcp.vertex.agent.tool_response`: kagent's runtime copies each tool's whole output onto
   its `execute_tool` span even with content capture off. `tests/beacon/test_collector.py`
   runs this exact config and checks the secret parts never come out.
5. `tail_sampling` (beacon §V.43): keeps every trace with an error and every trace touching a
   `beacon-*` service, and 10% of the rest. It also keeps every kagent trace
   (`service.namespace=kagent`, the controller and each session): agent runs are rare and only
   useful whole. Sampling happens here, not at the Gateway,
   because apps behind the Gateway (kagent, Substrate) follow its sampling decision.
6. Exported to Tempo and to Coroot (Coroot keeps its own 7 days). The Coroot export is
   uncompressed: Coroot's receiver refuses gzip (beacon §B.13).
A second pipeline counts spans per `service.name` before sampling
(`tracing_spans_received_total`, one label, a running total via `deltatocumulative`, beacon
§B.14) for the beacon §V.9 alert. A third, `traces/agent-platform`, reads the agent
platform's spans (kagent, Substrate, the MCP adapters, the Gateway's spans into kagent), also
before sampling, into metrics for the Agents dashboards on the same `:8889`:
- `traces_service_graph_*{client, server}` (`service_graph`): who calls whom. A call whose
  server sends no span (the model API, kagent-tools, ollama) is named by its `peer.service`,
  set from the client span's `server.address` (short service name, never an IP).
- `agent_tokens_{input,output,cache_read,cache_write}_total`, `agent_cost_estimate_usd_total`,
  `agent_model_calls_total`, `agent_tool_calls_total`, `agent_runs_total`
  (`signal_to_metrics`, by agent and model or tool): each span counted once. The exporter
  keeps a series exposed for 48 hours after its last update, and `deltatocumulative/agent-platform`
  keeps its running total one hour less, so a series that comes back starts from zero. The
  dashboards subtract each series' value before a window instead of using `increase()`, which
  would lose every new series' first value.
- `agent_run_start_seconds{trace_id, gen_ai_conversation_id}`: one series per agent run,
  exposed 48 hours, so the Agent Platform dashboard opens on the newest run.
Before the counters, a `batch` step makes concurrent sessions reach them one batch at a time.
`transform/scrub` also deletes ADK's `llm_request` and `llm_response`, should kagent ever write
them; tool arguments stay.
`tests/agent-capability/test_agent_traces.py` runs the config on a synthetic run and checks
each of these. One replica on purpose: tail sampling needs a whole trace on one instance.

## The OpenTelemetry Operator (`base-apps/opentelemetry-operator.yaml`)
Helm chart 0.126.0 (operator 0.161.0), namespace `opentelemetry-operator-system`. One job:
Python auto-instrumentation. Its pod webhook only sees namespaces labelled
`platform.arigsela.com/otel: python` (today only `beacon`; the chart default would be every
pod in the cluster) and fails open: with the operator down, a pod starts untraced rather than
not at all. A pod is instrumented when it carries
`instrumentation.opentelemetry.io/inject-python: "true"`; the operator then adds an init
container that copies its Python layer into the pod and points `PYTHONPATH` at it. How it
instruments is the namespace's `Instrumentation` (`base-apps/beacon/instrumentation.yaml`):
OTLP/HTTP to the collector, explicit sampler, log correlation on, traces only. beacon's
smoke Job runs the notifier under that layer before every sync (the layer is built on Python
3.14, the notifier runs 3.13). The operator's images are smoke-run here first
(`smoke-job.yaml`).

## Storage
- Bucket `asela-tempo-traces-20261019` (us-east-2), its IAM user, policy and access key are
  the managed app `tracing-aws-infrastructure`. The `AccessKey` writes Secret
  `tempo-s3-creds` straight into this namespace (keys `username`, `attribute.secret`), so
  no key is ever made by hand.
- Retention: Tempo deletes blocks after 168h; the bucket expires anything left after 8 days.
- The live-store WAL is an `emptyDir`: a restart loses only traces not yet flushed (minutes).

## Grafana
`base-apps/logging/grafana-deployment.yaml` provisions the `Tempo` datasource (uid `tempo`).
Trace to logs: from a span, "Logs for this span" searches Loki in every namespace for lines
holding the trace id, five minutes either side of the span (an agent run logs from kagent's
controller and from Substrate, and an agent's service name is no pod's `app` label). Logs to
trace: Loki's derived field turns a 32-hex trace id into a link to Tempo in three shapes:
`trace_id=<id>` (beacon's services, beacon §T.43), `"trace_id":"<id>"` (kagent's controller)
and `trace.id=<id>` (agentgateway: the MCP adapters, Substrate's router and egress). The
service map reads the collector's `traces_service_graph_*` from Prometheus. The Agents
dashboards are in `base-apps/logging/` (`scripts/gen-agent-dashboards.py`).

## Known limits
- One replica, `Recreate` rollouts: Tempo is briefly down on every config change.
- Metrics-generator is off until its cost is measured. Tempo still answers TraceQL metrics
  from its blocks, up to 24 h per query, but Grafana 11.6 runs only instant ones (it cannot
  read Tempo 3.1's range responses: `unknown field "step"`) and never `sum_over_time` (it does
  not recognise it as a metrics query). Sums and time series come from the collector's
  counters instead.
