# Model gateway: a daily budget per agent for model calls

**Date:** 2026-10-23. **Status:** design, for the owner's review. **Builds on:** the Agents
dashboards and the collector's cost estimate (§T.135, §V.105), agentgateway on the MCP adapters
(§T.133, §T.134), the eval gate (§V.93, §V.99) and the dispatcher (§V.86, §V.87).

## Why

The platform's Inference Endpoints capability is rated "partial". Its row reads, in part: "No
model gateway with per-agent budgets; spend capped in the provider console". What that costs was
shown on 2026-10-14 (§T.119): the Anthropic workspace hit its $10/month spend limit and every
agent went down at once. All agents share one key (`kagent-anthropic`), so the console cannot
tell them apart, and the per-agent cost on the Agents dashboards is our own estimate.

The owner chose two of the three options offered on 2026-10-23:
- (1) an alert on each agent's estimated daily cost;
- (3) a model gateway that enforces a budget per agent.

The third option, one Anthropic workspace per agent group, was not chosen.

## Goals

1. **A runaway agent stops alone.** Once an agent has spent its daily budget, the gateway refuses
   its model calls. Every other agent keeps working.
2. **The owner hears first.** Slack (`#oncall-alerts`) gets a message when an agent passes 80% of
   its daily budget, and another when the gateway blocks it.
3. **Budgets are daily** and reset at 00:00 UTC.
4. **Budgets live in one table in git.** Raising one is a pull request. There is no override
   switch.
5. **The real Anthropic key exists only in the gateway's namespace.** A sandbox never holds the
   real key or its own budget key. There is one stated exception: eval-gate's judge keeps its own
   copy (Secret `eval-gate`, key `anthropic-api-key`, copied by
   `scripts/provision-eval-gate-vault.sh --copy-anthropic`, §T.112). It is not an agent and does
   not run in a sandbox. Giving it a key of its own is a follow-up.
6. **The Anthropic console limit stays** as the monthly backstop.

## Non-goals

- Budgets per human user, or limits on requests or tokens per minute.
- Model routing or fallback, guardrails, or caching at the gateway.
- Callers outside kagent's agents. eval-gate's judge uses its own key (`eval-gate` Secret) and
  stays as it is.
- Monthly budgets in the gateway. The console limit covers the month.
- agentgateway's Kubernetes controller and CRDs. Its API has no `budgets` field (see Decisions).
- Showing the gateway on the hk-demo page's Model API box. That is a follow-up.
- Changing the Inference Endpoints row itself. That row does not live in this repo; the owner
  updates it.

## Anti-goals (a failure even if everything works)

- **A model call that is charged nothing.** A model with no price in the gateway's catalog is
  never blocked: the docs say its budget "never rejects a request". Every model a ModelConfig
  names must have a price.
- **A path around the gateway that still works.** After the migration, a call straight to
  `api.anthropic.com` must fail. It does today by construction: kagent adds `api.anthropic.com`
  to every actor's allow-list with no credential, so such a call gets a 401. Nothing may add a
  real key back.
- **A key value in git.** The virtual keys live in Vault, like every other secret.
- **One agent's spend blocking another.** The one exception is the build group, which the owner
  accepted.
- **A budget alert that starts an agent investigation.** An investigation spends money from the
  very budgets the alert is about.
- **A blocked run that hangs instead of failing.** The Anthropic Go SDK (v1.80.0, which the Go
  ADK uses) retries a 429 twice and waits for `Retry-After` with no upper limit
  (`internal/requestconfig/requestconfig.go:249-271,296-346,380-390`). agentgateway sets
  `Retry-After` on a budget refusal to the seconds left in the window
  (`http/budget/mod.rs:464-470`). Left alone, a blocked run would sleep until midnight, twice.
- **A rollout that breaks every agent at once.** Agents move over one group at a time.
- **An eval run, blocked by a budget, counted as a regression.** That would hold back the
  agent's automated work for a reason that has nothing to do with its behaviour (see §6).

## Decisions taken with the owner (2026-10-23)

- **Behaviour.** Only the over-budget agent stops; its run fails with a "budget reached" error.
  Slack hears at 80% and again when the agent is blocked. Budgets are daily and reset at
  midnight UTC. Raising one takes a pull request, with no override. The console limit stays.
- **The build group shares one budget.** build-orchestrator, k8s-agent and istio-agent use one
  key. The two specialists are Shared subagents compiled into build-orchestrator's actor, and
  kagent refuses two different Secrets for the same host and header in one actor ("conflicting
  credentials", kagent `go/core/internal/egress/credentials.go:52-53` @ v1.0.0-alpha14). The
  owner said that is fine.
- **The gateway is standalone agentgateway v1.6.0.** The owner asked whether this is how people
  use agentgateway for LLM calls. It is the documented pattern:
  - one virtual key per client, with a budget on each key;
  - the gateway "replaces each virtual key with the provider key";
  - a database holds the counts;
  - a price is needed for every model.

  Sources: agentgateway docs, standalone
  [per-key budgets](https://agentgateway.dev/docs/standalone/latest/llm/cost-controls/budget-limits/per-key/)
  and [virtual keys](https://agentgateway.dev/docs/standalone/main/documentation/llm/cost-controls/virtual-keys/).

  It has to be standalone: the Kubernetes API "does not carry the `budgets` or `allowedModels`
  fields". We already run standalone agentgateway for the MCP adapters.
- **The alert ships first, on its own.** The gateway follows in later pull requests.

## Calls I made (the owner can change any of them)

- The budget sizes in §2.
- Two gateway replicas, with the budget counts in a new database on the existing CNPG cluster.
- Namespace and Argo CD application `model-gateway`.
- The 80% warning is measured over a rolling 24 hours, not since midnight. PromQL cannot sum
  "since midnight", so the warning can fire a little early or late compared with the gateway's
  fixed day. The "blocked" alert is exact, because it counts the gateway's real refusals.
- How eval-gate treats a budget refusal (§6).
- How the gateway learns the virtual keys (§3.3).
- **The console limit is the monthly backstop. It is not sized against the sum of the budgets.**
  The budgets add up to $15.50 a day, about $480 a month, far above any sensible console limit.
  A group stuck at its cap every day is announced every day by the "blocked" alert, so it cannot
  run unnoticed for long. The owner confirms the console limit leaves room for normal spend plus
  about a week of the largest budget (about $35). The cost if this is wrong: after several days of
  ignored alerts, the console limit trips and every agent stops, as on 2026-10-14.
- **eval-gate's copy of the real key is a stated exception** (goal 5).
- **A budget refusal is not retried** (§3.2).

## Design

### 1. The request path

Today:

```
sandbox ──▶ atenet-egress (ate-system) ──▶ api.anthropic.com
             replaces x-api-key with the kagent-anthropic Secret,
             matched on the host and port of the ModelConfig's endpoint
```

After:

```
sandbox ──▶ atenet-egress ──▶ model-gateway.model-gateway.svc:8080 ──▶ api.anthropic.com:443
             replaces x-api-key        apiKey policy: virtual key → group,
             with the group's          budget checked before the call, charged after it;
             virtual key               provider key replaces x-api-key; TLS to Anthropic
```

The facts this rests on. Each was read in source unless marked otherwise.

**kagent v1.0.0-alpha14:**
- The ModelConfig has `spec.anthropic.baseUrl`
  (`go/api/v1alpha3/modelconfig_types.go:129-132`).
- kagent binds the credential to the host and port in `baseUrl`, with header `x-api-key`
  (`go/core/internal/translator/credentials.go:27-34,132-136`). The actor's egress policy then
  gets an HTTP rule that replaces that header for that host
  (`go/core/internal/substrate/policy.go:58-82`).
- The Go ADK will not start without an `ANTHROPIC_API_KEY` placeholder, so the header is always
  there to be replaced (`go/adk/pkg/models/anthropic.go:55-61`).
- `api.anthropic.com:443` is always added to the allow-list with no credential
  (`adkconfig/builder.go:330-331`). That is the 401 the anti-goals rely on.

**Substrate's egress:**
- It replaces the header only when the request carries it (`egress.rs:180-192` in agentgateway
  `168b05d`, the egress build).
- It reaches in-cluster Services: Loki shows sandbox → `brave-search-mcp.kagent:3000` returning
  200.

**What this design deliberately does not rely on:**
- The egress adds no identity of its own.
- `ate-system` is not in the ambient mesh, so the gateway sees only the egress pod's address.
- So the virtual key is the only thing that identifies the agent. Because the egress injects it,
  a sandbox cannot present another group's key.

### 2. Groups and budgets

One group means one virtual key, one Vault property, one Secret in `kagent`, and one ModelConfig
per model the group uses.

| Group | Agents | Models | Daily budget |
|---|---|---|---|
| `hk` | homelab-knowledge | Sonnet 5.5 | $3.00 |
| `build` | build-orchestrator, k8s-agent, istio-agent | Sonnet 5.5 | $5.00 |
| `implementer` | implementer | Sonnet 5.5 | $2.00 |
| `restarter` | restarter | Sonnet 5.5 | $2.00 |
| `docs-checker` | docs-checker | Sonnet 5.5 + Haiku 5.5 (from its own runtime) | $1.50 |
| `k8s-reader` | k8s-reader | Haiku 5.5 | $0.50 |
| `skill-suggester` | skill-suggester | Haiku 5.5 | $0.50 |
| `dcc` | dungeon-crawler-carl-agent | Haiku 5.5 | $0.50 |
| `ui-default` | the kagent chart's `default-model-config` (agents created in the UI) | Haiku 5.5 | $0.50 |

The worst case is $15.50 a day if every group spends everything.

**Where the sizes come from.** Spend from 2026-10-22 01:32Z to 2026-10-23 was $1.06 for 10
homelab-knowledge runs (about $0.10 a question), $0.06 for istio-agent and under a cent for
k8s-reader. The sizes are a guess on top of that. Before the first group moves, the builder
measures what one full eval-gate run costs each group (§6) and raises any budget that would not
cover a normal day plus two eval runs. The new figures go in the pull request.

**The table.** It lives in `base-apps/model-gateway/budgets.yaml`. That file is the only place
the numbers are written. The gateway config, the alert thresholds and the docs table are
generated from it or checked against it by a test. The builder picks which; hand-copied numbers
are not allowed.

### 3. The gateway

#### 3.1 Workload

- **App and placement.** `base-apps/model-gateway/`, Argo CD application `model-gateway`,
  namespace `model-gateway`.
- **Image.** Our own agentgateway image, the one the MCP adapters use:
  `852893458518.dkr.ecr.us-east-2.amazonaws.com/agentgateway:v1.6.0@sha256:083adf216093ed57813debcfdbb5f6aa9cb119741b373455233f6c0605faca2d`.
  It is built FROM scratch and runs as 65532. The config file comes from a ConfigMap. Mount an
  emptyDir if the gateway needs a writable directory.
- **Two replicas.** Spread across the workers, with a PodDisruptionBudget of `minAvailable: 1`.
  Every model call depends on the gateway, just as it depends on Substrate's egress today.
- **Telemetry.**
  - Prometheus annotations on `:15020/metrics`, the pattern from §T.134.
  - Traces to `otel-collector.tracing.svc.cluster.local:4317`. A run's gateway spans must join
    its kept trace, so check that the collector's `traces/agent-platform` pipeline and sampling
    keep them.
  - Logs go to stdout, and Alloy ships them to Loki.
- **NetworkPolicy.**
  - Ingress on 8080 only from `ate-system` pods labelled `app=atenet-egress`. Metrics on 15020
    only from Prometheus in `logging`.
  - Egress to DNS, `api.anthropic.com:443`, Postgres and the collector.
  - The virtual key crosses from the egress to the gateway in plain HTTP. That is accepted
    because of this policy, and because the key is worth nothing outside the gateway.

#### 3.1a What the local proof changed (2026-10-23)

These are the results of `tests/model-gateway/proof/` (results in `results-2026-10-23.md`). Each
one changed the design.

- **The fallback was needed.** A response rule does not reach a 429 the gateway generates
  itself. The design uses two listeners in one process:
  - a front listener on 8080, which the egress connects to;
  - the budget listener on `127.0.0.1:8081`, in agentgateway's `llm` mode, which holds the keys,
    the budgets and the provider key.

  The front sets `x-should-retry: false` only on a 429 without Anthropic's `request-id` header,
  so only on the gateway's own refusal.
  - That refusal's `Retry-After` was about 49,000 s in the proof.
  - The Anthropic Go SDK v1.80.0 then failed in 0.0 s, with `budget_exceeded` in its error.
- **`llm` mode, not hand-built routes.** The docs' own shape: `llm.models[]` with
  `provider: anthropic`, `passthrough: detect`, and `params.apiKey.file`.
  - Passthrough forwards the body unchanged: `cache_control`, `thinking` and unknown fields
    survive.
  - It still reads usage, including from streamed responses.
- **A key with a budget needs `metadata.name`.** The gateway refuses to start without it. The
  budget counter is keyed by the key's hash, so rotating a key starts its group's day at zero.
- **Base URLs.** The gateway adds `/messages` to the base URL's path, so a custom upstream is
  `…/v1`. kagent's `baseUrl` is the gateway root: the SDK adds `/v1/messages`.
- **Sharing between replicas is unreliable.** In about one proof run in three, one replica's
  spend never reached Postgres, and no error was logged. The other runs shared within 4 s.
  - The cause was not found; agentgateway 1.6.0 has no matching upstream report.
  - So the Service pins the egress to one replica (`sessionAffinity: ClientIP`, 86400 s), and the
    second replica is a standby that serves after a failover.
  - Each replica always enforces the budget on its own counts. After a failover the standby
    starts from what reached Postgres, so the worst case is about twice a group's budget on that
    day.
- **Prices match.** Haiku's base tier and its over-100K tier, and Sonnet with cache reads, were
  charged exactly as the collector computes them.

#### 3.2 Configuration

The key names below come from agentgateway v1.6.0's `schema/config.md`. The builder confirms
each one against the binary.

- **Listener.** Port 8080, one route.
  - `policies.apiKey`: `mode: strict`, `location.header.name: x-api-key`.
  - One entry per group, each carrying:
    - `metadata: {group: <name>}`;
    - `allowedModels`: the group's models;
    - `budgets: [{name: daily, limit: {unit: USD, amount: <budget>}, window: {rolling: 24h},
      onBudgetExceeded: Block}]`.
  - `Block` answers 429.
  - Windows count from the Unix epoch, so `24h` runs from 00:00 to 00:00 UTC
    (`crates/agentgateway/src/http/budget/mod.rs:227-233,307-318`).
- **Backend.**
  - `ai.provider.anthropic`.
  - Set `ai.policies.ai.routes` to
    `{"/v1/messages": messages, "/v1/messages/count_tokens": anthropicTokenCount}`. Without it,
    the route expects OpenAI's completions format (`crates/agentgateway/src/llm/policy/mod.rs:759-774`).
  - The provider key is read from a file mounted from the Secret. The docs show the backend's
    `params.apiKey`; the source shows `ai.policies.backendAuth.key.file`. Use whichever 1.6.0
    accepts. Either way the key is written as `x-api-key` and replaces the incoming one
    (`llm/mod.rs:1569-1595`).
- **`config.database.url`.** Postgres, database `model_gateway` on `postgresql-cluster`, with
  the password taken from the environment (`$VAR`).
  - Postgres is required, not SQLite: with two replicas, each one adds its spend to Postgres
    every 5 s and reads back the totals (`http/budget/database.rs:155-257`, an additive upsert in
    `postgres_upsert.sql`). The two replicas therefore share one budget, and the overshoot is
    about 5 s of spend plus any streams already running.
  - SQLite per replica would count each budget twice.
  - The gateway will not start without its database.
- **`config.modelCatalog`.**
  - Inline prices for `claude-sonnet-5-5` and `claude-haiku-5-5`. The bundled catalog has no
    Haiku 5.5.
  - Use the collector's numbers from §V.105, in $/MTok for input, cache write, cache read and
    output: Sonnet 5.5 is 2, 2.5, 0.2, 10; Haiku 5.5 is 0.1, 0.125, 0.01, 0.5.
  - A test asserts the two price lists are equal. So the dashboards' estimate and the budget use
    the same prices.
- **`config.metrics.fields.add`.** Adds a `group` label from the API key's metadata, so the
  gateway's own metrics can be read per group.
- **A budget refusal must not be retried.**
  - What a refusal looks like: HTTP 429 with `Retry-After` set to the seconds left in the window,
    and the JSON body `{"error": {"message": "Budget exceeded", "type": "rate_limit_error",
    "code": "budget_exceeded"}}` (`proxy/mod.rs:567-581`).
  - What the gateway must do: send `x-should-retry: false` on that response. The Go SDK obeys the
    header ahead of the status code (`requestconfig.go:262-284`), so the call fails at once.
  - The failed call's error carries the method, URL, status and that JSON body
    (`internal/apierror/apierror.go:64-76`). The agent's run fails, and its message contains
    `budget_exceeded`.
  - The first choice is a route `responseHeaderModifier` or `transformations.response` rule that
    sets the header only on the gateway's own 429. The local proof (§7 step 3) shows whether such
    rules reach a response the gateway generates itself.
  - If they don't, the fallback is two listeners in the same pod: a front listener on 8080
    proxies to a budget listener on `127.0.0.1:8081`. To the front listener the budget 429 is an
    upstream response, so its response rules certainly apply.
  - Anthropic's own 429s and 529s keep their normal retries: the header goes only on responses
    whose body has `code: budget_exceeded`, or only on 429s from the inner listener.
  - docs-checker's Python SDK uses `Retry-After` only when it is at most 60 s, and otherwise falls
    back to backoff, so it fails within seconds either way.

#### 3.3 Keys and Vault

- **The real key.** An ExternalSecret in `model-gateway` reads the same Vault entry kagent uses
  today: `k8s-secrets/kagent-anthropic`, property `anthropic-api-key`. It reads it through a new
  SecretStore and service account, with Vault role `model-gateway` scoped to that path.
- **The virtual keys.**
  - Vault `k8s-secrets/model-gateway-keys`, one property per group, each 32 random bytes,
    hex-encoded.
  - In `kagent`, one ExternalSecret per group produces Secret `model-gateway-key-<group>` with
    key `ANTHROPIC_API_KEY`. Their SecretStores must pass the `agent-identity` admission rules,
    which require scoped stores; follow the existing kagent stores.
  - In `model-gateway`, one ExternalSecret carries every group's key into the gateway's
    environment, and the config reads each with `$VAR`.
  - **My call:** env expansion rather than `keyHash` in git. With hashes in git, the owner would
    have to paste the hash output into a PR after running the Vault script; with env, the only
    owner step is the script. If 1.6.0 does not expand `$VAR` inside `apiKey.keys[].key`, fall
    back to `keyHash: sha256:<hex>`, written to git from the script's output. The hash of a
    32-byte random key is safe to publish.
- **The database password.** Vault `k8s-secrets/model-gateway-db`, plus an
  `init-model-gateway-db` Job in `base-apps/postgresql/`, following `init-substrate-db.yaml`.
- **The owner's script.** `scripts/provision-model-gateway-vault.sh`, following the other
  `provision-*-vault.sh` scripts.
  - It creates the Vault policies and roles, the database password, and any group key that is
    missing.
  - It never rotates an existing key unless asked with a flag.
  - It never prints a key value.

### 4. kagent changes

- **ModelConfigs.** One per group and model, for example `anthropic-claude-sonnet-5-5-hk`.
  - Each is the same as today's config for its model (`stream`, `maxTokens: 16000`, prompt
    caching, `cacheTTL: "5m"`), plus
    `spec.anthropic.baseUrl: http://model-gateway.model-gateway.svc.cluster.local:8080` and
    `apiKeySecret: model-gateway-key-<group>`.
  - A test asserts that every Anthropic ModelConfig in git uses the gateway URL and a group
    Secret, and that each is otherwise identical to the reference for its model. Generating
    them is the builder's choice.
- **AgentTemplates.** Each `spec.modelConfig.name` points at its group's config. k8s-agent and
  istio-agent point at the `build` group's config, because they also run inside
  build-orchestrator.
- **docs-checker.**
  - Its byo runtime calls the API itself, choosing Haiku or Sonnet per graph node, with the key
    and caching settings from its ModelConfig.
  - It honours `ANTHROPIC_BASE_URL` (LangChain `ChatAnthropic`; claude-agents
    `docs-checker/src/docs_checker/models.py:53-86`).
  - Its Harness gets `ANTHROPIC_BASE_URL` as a literal env value, unless kagent already passes
    the ModelConfig's `baseUrl` to byo runtimes; the builder checks which.
  - The egress credential binding follows the ModelConfig's `baseUrl` either way.
- **The chart's `default-model-config`** (`base-apps/kagent.yaml`, `providers.anthropic`). It
  moves to the `ui-default` key and the gateway URL, if the chart accepts a base URL there.
  - If the chart cannot take one, it keeps the real key until the next step, the default
    config's key is removed, and UI-created agents have no working model until someone gives
    them a git ModelConfig.
  - That second outcome is acceptable: no agent in git uses the default.
- **Last step.** Once every group has moved:
  - remove the old ModelConfigs;
  - remove `kagent-anthropic` from `kagent`: ExternalSecret, SecretStore, the ESO service
    account and the Vault role binding;
  - fix the eval corpus question that cites `kagent-anthropic-secret-store.yaml`
    (`tests/eval-corpus/homelab-knowledge.yaml:72-81`; regenerate the eval-gate ConfigMap);
  - keep `scripts/provision-eval-gate-vault.sh --copy-anthropic` working: it reads the Vault
    entry, not the Secret.

  After this step the real key exists only in `model-gateway`.

### 5. Alerts

The rules go in a new group `agent-budgets` in `base-apps/logging/grafana-alerting.yaml`, using
the Prometheus datasource `PBFA97CFB590B2093`, labels `severity` and `pillar: cost`, and
`execErrState: KeepLast`. Each rule's thresholds come from `budgets.yaml` (§2).

- **"Agent near its daily budget"** (warning).
  - Fires when a group's spend over the last 24 hours reaches 80% of its budget.
  - Before the gateway exists, spend is the collector's estimate: `agent_cost_estimate_usd_total`
    by `service_name`, summed per group, read with `window_total` from
    `scripts/gen-agent-dashboards.py`. Never `increase()` on these counters; see §V.106.
  - After the gateway exists, it reads the gateway's own cost metric by `group`.
- **"Agent blocked by its daily budget"** (critical).
  - Before the gateway: the same estimate reaching 100%, titled "would have been blocked".
  - After the gateway: any budget refusal (429) in the last 5 minutes, per group. Read it from
    the gateway's metrics, or from its "Budget exceeded" log line in Loki; the builder picks the
    signal that names the group.
- **Annotations** name the group, its agents, the spend and the budget, and link the runbook.
  Never prompts or answers.
- **The dispatcher.** `dispatch-table.yaml` gets one row per rule title with `route: none` and
  `why: an investigation would spend from the budgets this alert is about`.
  `tests/dispatch/test_table.py` already requires a row for every title.
- **"Model gateway down"** (critical): fewer than one ready replica for 2 minutes, from
  Prometheus `up` or kube-state metrics. It gets a dispatcher row `none` as well.

### 6. Eval-gate and budgets

eval-gate scores every agent after any sync that changes an agent's definition. Each migration
pull request in §7 is such a change. Its model calls are the agents' own, so they are charged to
each group's budget.

- **Sizing.** Measure one full eval run's cost per group from the dashboards before the first
  group moves, and size budgets to fit (§2).
- **My call: a budget refusal is "could not score".**
  - The trigger: any captured answer, or any failed task message, of an agent being scored that
    contains `budget_exceeded`. One is enough; eval-gate does not wait for every capture to fail.
  - The result: that agent is "could not score", never a regression. The JSON line says `budget`
    and the Job exits 1, which is the existing "could not score" path, so Argo shows the hook
    failed. The release gate keeps the previous result for that agent.
  - eval-gate gets no new network path. It reads the reason from the agent's own failed run (§3.2).
  - This changes eval-gate's code (`scripts/eval_gate.py` and its generated ConfigMap) and its
    tests.

### 7. Rollout (one pull request per step)

1. **The alerts on the estimate**, `budgets.yaml` and the dispatcher rows. This runs nothing
   new.
2. **The owner runs `scripts/provision-model-gateway-vault.sh`.** It must happen before step 3.
3. **The gateway**: app, database Job, ExternalSecrets, NetworkPolicy, alerts, docs. No agent
   uses it yet.
   - Before this pull request, prove the config locally. Run the v1.6.0 image against a mock
     Anthropic server that returns `usage`, streamed and not streamed, on a local Postgres.
   - Show that:
     - a known key passes and reaches the mock with the provider key;
     - an unknown key gets 401;
     - a key over its budget gets 429;
     - a streamed call is charged;
     - two gateway processes sharing the database block at the shared total;
     - Haiku is priced.
   - Record the commands and results in the pull request.
4. **k8s-reader moves first**, because it is the cheapest. Ask it a question. Verify that:
   - the gateway's metrics show group `k8s-reader` charged;
   - the gateway's budget status (`/api/budgets` on the admin port, through a port-forward)
     shows the spend;
   - the Agents dashboards still show the run and its cost.

   Then the block drill, which the owner may skip: a pull request sets k8s-reader's budget to
   $0.001. Ask k8s-reader a question and show that the run fails with the budget error, Slack
   gets "Agent blocked…", and homelab-knowledge still answers. Then revert.
5. **The other groups**: the Haiku groups together, then the Sonnet groups, then docs-checker,
   then `ui-default`. Until step 6, undoing one group means pointing its templates back at the
   old ModelConfig.
6. **Removal**, as in §4's last step. The "blocked" alert's estimate rule goes too, if step 3
   has not already replaced it.

### 8. Docs, Backstage and SPEC

- **The app's docs.** `base-apps/model-gateway/docs.md` and `docs/index.md` (`gen-techdocs.py`),
  `runbook.md` and `catalog-info.yaml`, in the house style.
- **The runbook covers:**
  - raising a budget;
  - reading a group's spend;
  - an agent blocked;
  - the gateway down;
  - Postgres down: the gateway keeps serving but cannot start;
  - rotating a key: a new key starts its group's count at zero for the day.
- **kagent's docs.** "Model configs" in kagent `docs.md` and its runbook describe the new path,
  and the agents table names each agent's group.
- **SPEC.**
  - A task row, §T.137.
  - Invariants, §V.108: every Anthropic ModelConfig goes through the gateway on a group Secret;
    every group has a budget and every model has a price; the gateway's prices equal the
    collector's; the alert thresholds equal `budgets.yaml`; a budget refusal carries
    `x-should-retry: false`; no `kagent-anthropic` in `kagent` after step 6 (eval-gate's judge
    key is the one exception).
  - §B rows for whatever breaks on the way.

### 9. Done when

- Every agent's model calls go through the gateway, and the gateway's metrics show each group.
- The block drill (or, if skipped, the local proof) shows:
  - a blocked group gets 429 and its run fails with the budget error;
  - Slack gets both alerts;
  - other agents keep answering.
- No ModelConfig, Harness or sandbox holds or reaches the real key; the only copy in `kagent` is
  eval-gate's judge key. A sandbox call straight to `api.anthropic.com` gets a 401.
- A blocked run fails within seconds, and its message contains `budget_exceeded`.
- The Agents dashboards still show tokens and cost per agent.
- CI is green: the new tests plus the existing `agent-identity`, `agent-capability`, `dispatch`,
  `eval-corpus` and admission-policy suites.

### 10. Left to the builder

- The exact v1.6.0 config keys: the provider key field, and whether `$VAR` works inside keys.
- The gateway's metric names and labels for the alerts.
- Resources, probes and Argo CD waves.
- Whether the ModelConfigs and the alert rules are generated or checked by tests.
- File layout, and how the tests are split.

## Risks

- **The console limit.** See "Calls I made". The owner checks it before the first group moves.

- **The whole combination has not run end to end.** That means the `apiKey` policy on
  `x-api-key`, the provider key swap, budgets with the Anthropic routes, and streaming. Step 3's
  local proof exists to catch this before any agent depends on it.
- **A Substrate or agentgateway upgrade may change behaviour.** That includes how the egress
  injects headers, or how budgets and the catalog work. The local proof becomes a regression
  check worth keeping.
- **Eval runs could spend more than expected.** §6 sizes for it, and a refusal is "could not
  score", not a regression.
- **The gateway becomes a single dependency for every model call.** Two replicas, a
  PodDisruptionBudget and the "Model gateway down" alert limit this. A Postgres outage alone
  does not stop it serving, but a restart during one does.
