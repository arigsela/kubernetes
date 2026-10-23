# Model Gateway Budgets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every kagent agent group a daily dollar budget that a model gateway enforces, with
Slack alerts at 80% and on a block.

**Architecture:**
- **One table drives everything.** `base-apps/model-gateway/budgets.yaml` is the only place the
  budgets are written. One generator, `scripts/gen-model-budgets.py`, writes everything derived
  from it: the Grafana alert rules, the gateway's config, the kagent ExternalSecrets for the
  virtual keys, and the per-group ModelConfigs.
- **The gateway.** A standalone agentgateway v1.6.0 in namespace `model-gateway`. It runs two
  replicas and keeps its budget counts in Postgres.
- **The key path.** Substrate's egress injects each group's virtual key, the gateway checks the
  budget, and it swaps in the real key.

**Tech Stack:** Argo CD, Kubernetes manifests, agentgateway v1.6.0 (standalone `config.yaml`),
External Secrets and Vault, CNPG Postgres, Grafana 11.6 unified alerting, Prometheus 3.0.1
(promtool), Python 3.12 with pytest and pyyaml, bash.

**Spec:** `docs/superpowers/specs/2026-10-23-model-gateway-budgets-design.md`. Read §1-§7 before
starting.

## Global Constraints

- **GitOps only.** Never run kubectl apply, patch or delete, and never `apply` from a laptop.
  Read-only kubectl and port-forwards are allowed.
- **Secrets.** Never commit a secret, never print a key value, and leave the untracked
  `app-token.txt` alone.
- **Owner-only steps:** merges, `scripts/provision-model-gateway-vault.sh`, and the Atlantis
  apply (none is needed here).
- **The image.** The gateway runs
  `852893458518.dkr.ecr.us-east-2.amazonaws.com/agentgateway:v1.6.0@sha256:083adf216093ed57813debcfdbb5f6aa9cb119741b373455233f6c0605faca2d`.
- **Prices.** They come from the collector's `transform/genai-cost` in
  `base-apps/tracing/collector.yaml`, in $/MTok for input, cache write, cache read and output:
  - `claude-sonnet-5-5`: 2, 2.5, 0.2, 10.
  - `claude-haiku-5-5`: 0.1, 0.125, 0.01, 0.5, and 0.5, 0.625, 0.05, 2.5 when the prompt is over
    100000 tokens.
- **Budgets.** Group names and dollar amounts exactly as the spec's §2 table. A window is `24h`.
  The warning fires at 0.8 of the budget.
- **The gateway URL** is `http://model-gateway.model-gateway.svc.cluster.local:8080`.
- **Validators** to run before every PR, each test directory separately because of a conftest
  clash:
  - `python3 -m pytest -q tests/<dir>` for every directory you touched, plus `tests/dispatch`,
    `tests/dashboards`, `tests/agent-identity`, `tests/agent-capability`,
    `tests/admission-policies`, `tests/catalog-refs`, `tests/techdocs`, `tests/okf` and
    `tests/eval_gate`;
  - `scripts/validate-agent-docs.py`, `scripts/gen-techdocs.py --check`,
    `scripts/gen-okf.py --check` and `scripts/validate-catalog-refs.py`;
  - `uvx --quiet yamllint==1.35.1 -c .yamllint <files>`.
- **Commits** end with the session's Co-Authored-By and Claude-Session lines.
- **PRs.** Each PR body ends with the Claude Code line. PRs are opened, never merged.

## Review Focus

1. **A budget refusal retried for hours.** The Go SDK obeys `Retry-After` with no limit, so a
   blocked call must fail within seconds. Pinned by the Task 5 proof and the Task 6 config test.
2. **A model with no price**, which is never blocked. Pinned in Task 1 (every model in the table
   is priced in the collector) and Task 6 (every model is in the gateway's catalog, including
   Haiku's over-100K tier).
3. **Two replicas counting twice.** Pinned by the Task 5 proof, with two gateway processes on one
   Postgres.
4. **An agent in no group, or a subagent in a different group from its parent.** kagent refuses
   "conflicting credentials" for that. Pinned in Task 1.
5. **A group's spend dropped by the alert query.** That happens when a series is new in the
   window, or an agent is unknown. Pinned by the Task 2 promtool case.

---

## PR A: budgets and alerts on the estimate

Branch `feature/model-gateway-budgets`, starting from the design branch so the design and this
plan travel with it.

### Task 1: The budget table and its loader

**Files:**
- Create: `base-apps/model-gateway/budgets.yaml`
- Create: `scripts/model_budgets.py`
- Test: `tests/model-gateway/test_budgets.py`, plus `tests/model-gateway/conftest.py`, which sets
  `REPO` and puts `scripts/` on `sys.path`.

**Interfaces:**
- **Produces**, in `scripts/model_budgets.py`:
  - `load(path: Path = BUDGETS) -> dict`, the parsed table, validated or raising `ValueError`.
  - `group_of() -> dict[str, str]`, mapping agent to group.
  - `collector_prices(path: Path = COLLECTOR) -> dict[str, list[dict]]`, mapping model to tiers.
    Each tier is `{"over": int, "input", "cache_write", "cache_read", "output"}`, with `over`
    being 0 for the base rate.
  - `env_name(group: str) -> str`, giving `MODEL_GATEWAY_KEY_<GROUP>` with `-` replaced by `_`.
  - The constants `GATEWAY_URL` and `BUDGETS`.
- **The table's format:**
  - Top-level keys: `version: 1`, `window: 24h`, `warn_at: 0.8`, and `groups`.
  - `groups.<name>` is `{agents: [...], models: [...], daily_usd: <float>, gateway: <bool>}`.
    `gateway` is false in PR A; Task 11 sets it true group by group.
  - `ui-default` has `agents: []` and `chart_default: true`.

- [ ] **Step 1: Write the failing tests**
  - `test_every_agent_template_is_in_exactly_one_group`: compare the set of
    `base-apps/kagent/agent-templates/*.yaml` `metadata.name` values with the union of the groups'
    `agents`, and check that no agent appears twice.
  - `test_subagents_share_their_parents_group`: for every template, each `tools[].subAgent.templateRef.name`
    has `group_of()[child] == group_of()[parent]`.
  - `test_every_model_is_priced_by_the_collector`: each group's `models` is a subset of
    `collector_prices()`.
  - `test_collector_prices_parse`: `collector_prices()["claude-haiku-5-5"]` equals the two tiers
    in Global Constraints, and Sonnet has one tier.
  - `test_budgets_match_the_spec`: `{g: daily_usd}` equals hk 3.0, build 5.0, implementer 2.0,
    restarter 2.0, docs-checker 1.5, k8s-reader 0.5, skill-suggester 0.5, dcc 0.5,
    ui-default 0.5. The window is `24h` and `warn_at` is 0.8.
  - `test_load_rejects`: a group name that does not match `^[a-z][a-z0-9-]{0,30}$`,
    `daily_usd <= 0`, and `warn_at` outside (0, 1) each raise `ValueError`. Feed these in as
    tmp_path files.
- [ ] **Step 2:** Run `python3 -m pytest -q tests/model-gateway`. Expected: they FAIL on import.
- [ ] **Step 3:** Write `budgets.yaml` with the spec's §2 groups and every `gateway: false`, with
  a header comment saying it is the only place budgets live and that `gen-model-budgets.py`
  derives the rest. Then write `model_budgets.py`. `collector_prices` parses the
  `set(attributes["agent.cost.estimate_usd"], …)` statements with a regex: the four multipliers,
  the model in the `where` clause, and the `cache["input"] > N` tier.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit with the message `feat(model-gateway): the budget table (§T.137)`.

### Task 2: The alert rules on the estimate, and their dispatcher rows

**Files:**
- Create: `scripts/gen-model-budgets.py`, run as `--write` or `--check`; it exits 1 and names the
  file on drift.
- Modify:
  - `base-apps/logging/grafana-alerting.yaml`: a generated group block between
    `# BEGIN agent-budgets (scripts/gen-model-budgets.py)` and `# END agent-budgets`, inside
    `rules.yaml`'s `groups:`.
  - `base-apps/logging/grafana-deployment.yaml`: the `checksum/alerting` value.
  - `base-apps/dispatcher/dispatch-table.yaml`.
  - `.github/workflows/validate.yaml`: a `model-gateway` job running `tests/model-gateway`, and
    promtool installed the way the dashboards job does it.
- Test: `tests/model-gateway/test_alerts.py`

**Interfaces:**
- **Consumes:** Task 1's `load` and `group_of`, and `window_total` from
  `scripts/gen-agent-dashboards.py`, loaded with importlib as `tests/dashboards` does.
- **Produces:**
  - `spend_by_group_expr() -> str`: a label_replace chain that maps each agent's `service_name`
    to its `group`, applied to
    `window_total("agent_cost_estimate_usd_total", "service_name", "24h")`, then
    `sum by (group)` with `{group!=""}` kept.
  - `budget_vector_expr() -> str`: `label_replace(vector(<usd>), "group", "<g>", "", "")` for each
    group, joined with `or`.
  - `ratio_expr() -> str`: `(spend) / on(group) (budgets)`.
  - The rule titles `Agent near its daily budget` and `Agent over its daily budget`.

- [ ] **Step 1: Write the failing tests**
  - `test_generated_block_is_current`: `gen-model-budgets.py --check` exits 0.
  - `test_rules_follow_the_house_rules`: group `agent-budgets`, folder `Agent Budgets`,
    interval `5m`, datasource uid `PBFA97CFB590B2093`, `execErrState: KeepLast`,
    `noDataState: OK`, and labels `pillar: cost`.
    - The near rule is severity `warning` with threshold `gt 0.8`.
    - The over rule is severity `critical` with threshold `gt 0.999`.
    - Each rule's query refId `ratio` equals `ratio_expr()`.
    - The annotation `summary` uses `$labels.group` and the spend query's value.
  - `test_ratio_through_promtool`, skipped when promtool is absent, as in `tests/dashboards`.
    The input series, over 25 h, for `agent_cost_estimate_usd_total`:
    - `homelab-knowledge` rises to 2.5 inside the window, from a series that is new in it;
    - `k8s-agent` reaches 2.0 and `istio-agent` 2.5;
    - an unknown `service_name="mystery"` reaches 9.
    Expected at the end: `{group="hk"}` ≈ 0.8333 and `{group="build"}` = 0.9, with no `mystery`
    series and no series for groups that have no data.
  - `test_every_budget_rule_has_a_dispatch_row_with_route_none`: for both titles, a row with
    `route: "none"` and a `why` containing "spend".
- [ ] **Step 2:** Run `python3 -m pytest -q tests/model-gateway`. Expected: the new tests FAIL.
- [ ] **Step 3:** Write the generator and run `scripts/gen-model-budgets.py --write`.
  - Each rule gets two queries: refId `ratio`, which is the condition through a threshold
    expression, and refId `spend`, used only by the annotation.
  - The generator also rewrites `checksum/alerting`, using the same computation as
    `tests/dashboards/test_grafana_alerting.py`.
  - Add the two dispatcher rows by hand (the table is hand-written JSON).
  - Add the CI job.
- [ ] **Step 4:** Run `tests/model-gateway`, `tests/dispatch`, `tests/dashboards`. Expected: PASS.
  Run promtool locally from the scratchpad: download Prometheus 3.0.1 to
  `/private/tmp/claude-501/-Users-arisela-git-claude-agents/d294c51b-cbcb-403c-83da-1fe696267195/scratchpad/promtool-3.0.1/`
  and put it on PATH for the run. Expected: the promtool test runs, not skipped.
- [ ] **Step 5:** Commit with the message
  `feat(logging): agent budget alerts on the cost estimate (§T.137, §V.108)`.

### Task 3: SPEC rows, logging docs and opening PR A

**Files:** Modify `SPEC.md` (add T137 with status `~`, and V108), `base-apps/logging/docs.md`
(its alert list), and the generated `base-apps/logging/docs/index.md` (`gen-techdocs.py`).

- [ ] **Step 1:** Write the SPEC rows in caveman style.
  - **T137** cites the spec and lists PR A as done, with B and C pending.
  - **V108** holds the spec §8 invariants. Its status is "enforced from PR A where tests exist".
- [ ] **Step 2:** Run every validator in Global Constraints. Expected: all pass.
- [ ] **Step 3:** Commit, push, and open PR A.
  - Title: `feat(model-gateway): budget table and alerts on the estimate (§T.137)`.
  - The body says it is a no-runtime-risk change, links the spec and plan, and lists the
    budgets.

## PR B: the gateway, with no agent using it yet

Branch `feature/model-gateway`, stacked on PR A (its base is PR A's branch).

### Task 4: The provisioning script

**Files:**
- Create: `scripts/provision-model-gateway-vault.sh`
- Test: `tests/model-gateway/test_provisioning.py`

**Interfaces:**
- Writes Vault `k8s-secrets/model-gateway-keys`: one property per group, named after the group,
  32 random bytes as hex from `openssl rand -hex 32`, written only when missing.
- Writes Vault `k8s-secrets/model-gateway-db` with property `password`.
- **Policies and roles:**
  - `model-gateway`: reads `kagent-anthropic`, `model-gateway-keys` and `model-gateway-db`. Bound
    to SA `eso-model-gateway` in namespace `model-gateway`.
  - `model-gateway-keys`: reads `model-gateway-keys`. Bound to SA `eso-model-gateway-keys` in
    `kagent`.
  - `model-gateway-db`: reads `model-gateway-db`. Bound to SA `eso-model-gateway-db` in
    `postgresql`.
- Flags: `--rotate <group>` and `--dry-run`. It needs `VAULT_ADDR` and checks
  `vault token lookup`. It never echoes a value.

- [ ] **Step 1: Write the failing tests**
  - `test_script_parses`: `bash -n` passes.
  - `test_names_match_manifests`: every role, SA and namespace above appears in the script and in
    the Task 7 manifests. Until Task 7 lands, mark the manifest half `xfail(strict=True)`; Task 7
    removes the marker.
  - `test_groups_come_from_the_table`: the script reads the group names with
    `python3 scripts/model_budgets.py --groups`. Add that CLI to Task 1's module in this task,
    with its own test. The script never hard-codes a group.
  - `test_never_prints_values`: no `echo "$key"`, `vault kv get -field` output goes only to
    variables, and there is no `set -x`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Write the script in the style of `scripts/provision-implementer-vault.sh`.
- [ ] **Step 4:** Run the tests. Expected: PASS, with the manifest half xfail.
- [ ] **Step 5:** Commit with the message `feat(model-gateway): Vault provisioning script (§T.137)`.

### Task 5: The local proof, the riskiest unknown first

**Files:** Create `tests/model-gateway/proof/run-proof.sh`, `mock_anthropic.py` (stdlib only),
`README.md`, and `results-2026-10-23.md`, which holds the recorded output.

**Interfaces:**
- **Consumes:** the gateway config produced by Task 6's `gateway_config(keys: dict[str, str],
  db_url: str) -> dict`. Write Task 6's function first if needed; the proof must run the
  generated config, not a hand-written one.
- **What `run-proof.sh` does:**
  - Starts `postgres:16` and the mock in Docker.
  - Runs two gateway processes, A and B, on one database. It uses the v1.6.0 linux binary from
    the scratchpad `dl16/`, whose sha256 it checks against the pin in
    `scripts/build-agentgateway-image.sh`, inside a debian:bookworm-slim container.
  - The mock serves `/v1/messages`, streamed and not, with a `usage` block that has cache tokens,
    and records the `x-api-key` header it received.

- [ ] **Step 1: Write the proof script with one assertion per behaviour.** It exits non-zero on
  the first failure. The behaviours:
  1. A known key is answered 200, and the mock saw the provider key, not the virtual key.
  2. An unknown or missing key gets 401.
  3. A streamed call is charged. `/api/budgets` on the admin port shows `used` > 0 within 10 s.
  4. Haiku is charged at the catalog price, both at or under 100K and over it.
  5. A key over its budget gets 429 with `code: budget_exceeded` **and** `x-should-retry: false`.
  6. A real upstream 429 from the mock keeps its `retry-after` and has **no**
     `x-should-retry: false`.
  7. Spend charged through A blocks the same key on B within 10 s.
  8. Requests to `/v1/messages/count_tokens` are routed as `anthropicTokenCount`.
- [ ] **Step 2:** Run it. If behaviours 5 and 6 fail with a route-level
  `responseHeaderModifier` or `transformations.response`, switch Task 6 to the two-listener
  fallback in spec §3.2 and run it again. Expected: every behaviour passes.
  Record the output in `results-2026-10-23.md`, along with the mechanism that won.
- [ ] **Step 3:** Commit with the message
  `test(model-gateway): local proof of keys, budgets, no-retry and shared counts (§T.137)`.

### Task 6: The gateway config, generated

**Files:**
- Modify `scripts/gen-model-budgets.py` so it also writes `base-apps/model-gateway/configmap.yaml`
  (ConfigMap `model-gateway-config`, key `config.yaml`).
- Test: `tests/model-gateway/test_gateway_config.py`

**Interfaces:**
- **Produces:**
  - `gateway_config(keys: dict[str, str], db_url: str) -> dict`. The ConfigMap renders it with
    `keys = {g: "$" + env_name(g)}` and `db_url = "$MODEL_GATEWAY_DB_URL"`.
  - The provider key file is read from `/var/run/model-gateway/anthropic/api-key`.
  - The admin and metrics ports are agentgateway's defaults, recorded as constants `ADMIN_PORT`
    and `METRICS_PORT` after checking them in the proof.

- [ ] **Step 1: Write the failing tests**
  - `test_configmap_is_current`: `--check` exits 0.
  - `test_one_key_per_group_with_its_budget`. For each group:
    - the key is `$MODEL_GATEWAY_KEY_<G>` (or a `keyHash`, if the proof showed `$VAR` is not
      expanded in keys);
    - `metadata.group` equals the group name;
    - `allowedModels` equals the group's `models`;
    - there is one budget `{name: daily, limit: {unit: USD, amount: daily_usd}, window: {rolling: 24h}, onBudgetExceeded: Block}`.
  - `test_api_key_is_read_from_x_api_key_strictly`.
  - `test_anthropic_routes_are_set`: the `/v1/messages` and `/v1/messages/count_tokens` routes.
  - `test_catalog_prices_equal_the_collector`: the inline catalog's rates and tiers equal
    `collector_prices()` for every model in any group.
  - `test_budget_refusal_is_not_retried`: the mechanism Task 5 proved is present. Assert its
    exact shape.
  - `test_database_and_metrics_fields`: `config.database.url` is `$MODEL_GATEWAY_DB_URL`, and
    `config.metrics.fields.add.group` is a CEL expression over the api key's metadata.
  - `test_tracing_to_the_collector`: the same shape as the MCP adapters' `frontendPolicies.tracing`.
- [ ] **Steps 2-5:** Run the tests (they FAIL), implement, write the ConfigMap with `--write`,
  run again (they PASS), and re-run the Task 5 proof against the final config. Commit with the
  message `feat(model-gateway): generated gateway config (§T.137)`.

### Task 7: The manifests

**Files:**
- **App and workload:**
  - Create `base-apps/model-gateway.yaml`: an Application like `hk-demo.yaml`, with `exclude`
    set to `'{catalog-info.yaml,mkdocs.yml,budgets.yaml}'` and automated prune and selfHeal.
  - In `base-apps/model-gateway/`, create:
    - `namespace.yaml`;
    - `serviceaccounts.yaml`: `model-gateway` and `eso-model-gateway`;
    - `secret-store.yaml`: `vault-model-gateway`, role `model-gateway`;
    - `external-secrets.yaml`: Secret `model-gateway-anthropic` with key `api-key`, from
      `kagent-anthropic`/`anthropic-api-key`, and Secret `model-gateway-env`, holding one
      `MODEL_GATEWAY_KEY_<G>` per group from `model-gateway-keys` plus `MODEL_GATEWAY_DB_URL`
      templated from `model-gateway-db`;
    - `deployment.yaml`: 2 replicas, a topologySpreadConstraint on hostname, runAsNonRoot 65532,
      a read-only root filesystem with an emptyDir at `/tmp`, readiness on the admin port,
      `checksum/config` from `configmap.yaml`, `envFrom` the `model-gateway-env` Secret, and the
      provider key mounted from `model-gateway-anthropic`;
    - `service.yaml`: port 8080 for traffic, plus metrics;
    - `pdb.yaml`: minAvailable 1;
    - `network-policy.yaml`: the spec's §3.1.
- **kagent side, generated from the table:** `base-apps/kagent/model-gateway-keys.yaml`, holding
  SA `eso-model-gateway-keys`, SecretStore `vault-model-gateway-keys` (role `model-gateway-keys`,
  scoped like `kagent-anthropic-secret-store.yaml`), and one ExternalSecret per group producing
  `model-gateway-key-<g>` with key `ANTHROPIC_API_KEY` from property `<g>`.
- **Postgres:**
  - `base-apps/postgresql/init-model-gateway-db.yaml`, following `init-substrate-db.yaml`:
    database `model_gateway`, owner `model_gateway`, password from Secret `model-gateway-db`;
  - `base-apps/postgresql/model-gateway-db-secret-store.yaml`,
    `eso-model-gateway-db-serviceaccount.yaml` and `external-secrets-model-gateway-db.yaml`.
- **Test:** `tests/model-gateway/test_manifests.py`.

- [ ] **Step 1: Write the failing tests**
  - The image equals the pin.
  - Replicas 2, a PDB of minAvailable 1, and `checksum/config` equal to the sha256 of the
    ConfigMap data.
  - NetworkPolicy ingress on 8080 admits only `namespaceSelector` `kubernetes.io/metadata.name: ate-system`
    together with `podSelector` `app: atenet-egress`, and the metrics port only from `logging`.
  - Every group in the table has a kagent ExternalSecret and a key in `model-gateway-env`.
  - The env Secret's `MODEL_GATEWAY_DB_URL` points at `postgresql-cluster-rw.postgresql.svc`,
    database `model_gateway`. Check the CNPG service name in `base-apps/postgresql/services.yaml`.
  - Remove the xfail from Task 4's test.
- [ ] **Step 2:** Run `tests/model-gateway`, `tests/agent-identity` and
  `tests/admission-policies`. Expected: the new tests FAIL, the others PASS.
- [ ] **Step 3:** Write the manifests, and extend the generator to write the kagent ExternalSecrets.
- [ ] **Step 4:** Run every validator. The kagent stores must pass `agent-identity`'s scoped-store
  rule; add a good fixture if that rule's tests list real stores. Expected: PASS.
- [ ] **Step 5:** Commit with the message
  `feat(model-gateway): the gateway app, its database and keys (§T.137)`.

### Task 8: The gateway's alerts

**Files:** Modify `scripts/gen-model-budgets.py` (two more rules), `grafana-alerting.yaml`, the
deployment checksum, and `dispatch-table.yaml`. Test: extend `tests/model-gateway/test_alerts.py`.

- **"Agent blocked by its daily budget"** (critical): the gateway's 429 budget refusals by
  `group` in the last 5 m > 0. The metric and labels come from the Task 5 proof's `/metrics`
  scrape; record them in the generator as constants.
- **"Model gateway down"** (critical): `sum(up{namespace="model-gateway"}) < 1` for 2 m, or the
  kube-state equivalent the proof shows is scraped. `noDataState: Alerting`, because no data
  means no gateway once PR B is merged.

- [ ] **Steps 1-5:** Write the tests (titles, severities, dispatch rows `none`, the exact
  expressions), see them fail, generate, see them pass, and commit with the message
  `feat(logging): gateway refusal and gateway-down alerts (§T.137)`.

### Task 9: eval-gate counts a budget refusal as "could not score"

**Files:** Modify `scripts/capture-eval-answers.py` (the row keeps `error`: the failed task's
`status_text` or the exception text, cut to 300 characters) and `scripts/eval_gate.py`, then
regenerate `base-apps/kagent/eval-gate-code-configmap.yaml` with `scripts/gen-eval-gate-configmap.py`.
Test: `tests/eval_gate/test_eval_gate.py`.

- [ ] **Step 1: Write the failing tests**
  - `test_budget_refusal_is_not_scored`: a fake `send` where one of three entries fails with
    status text containing `budget_exceeded`. Expected:
    - `run()` returns 1;
    - the log has `{"agent": a, "error": "budget refused; not scored", "budget": true}`;
    - `state.hashes`, `state.baselines` and `state.release` for that agent are unchanged;
    - no commit status is posted.
  - `test_other_failures_still_score`: a failure without `budget_exceeded` is scored as today.
- [ ] **Steps 2-5:** See them fail; implement (the check goes right after `capture_full`, before
  the existing every-capture-failed check); regenerate the ConfigMap; run `tests/eval_gate` and
  `tests/eval-corpus`; commit with the message
  `fix(eval-gate): a budget refusal is not scored (§T.137)`.

### Task 10: Docs, Backstage, SPEC, and opening PR B

**Files:**
- **Create in `base-apps/model-gateway/`:** `docs.md`, `runbook.md` (with the spec §8 runbook
  topics), `catalog-info.yaml` (a Component in system `platform-observability`, owner
  `group:default/platform`, with `docs.md`'s frontmatter like hk-demo's) and `mkdocs.yml`, plus
  the generated `docs/index.md` and `docs/runbook.md`.
- **Modify:**
  - kagent `docs.md`, "Model configs": the planned path, and a note that agents move in PR C;
  - `SPEC.md` T137 progress;
  - `scripts/gen-okf.py` output.

- [ ] **Step 1:** Write the docs. Regenerate techdocs and okf.
- [ ] **Step 2:** Run every validator. Expected: PASS.
- [ ] **Step 3:** Commit, push, and open PR B against PR A's branch.
  - Title: `feat(model-gateway): the model gateway, with no agent on it yet (§T.137)`.
  - The body starts with the owner's steps in order:
    1. Merge PR A.
    2. Run `scripts/provision-model-gateway-vault.sh` from this branch.
    3. Merge PR B.
    4. Check that `model-gateway` is Healthy and the `init-model-gateway-db` Job succeeded.
  - It also carries the Task 5 proof results.

## PR C: k8s-reader moves first (opened as a draft)

Branch `feature/model-gateway-k8s-reader`, stacked on PR B.

### Task 11: Per-group ModelConfigs, and k8s-reader on the gateway

**Files:** Modify `budgets.yaml` (`k8s-reader: gateway: true`), `scripts/gen-model-budgets.py`
(it writes `base-apps/kagent/model-configs/anthropic-<model>-<group>.yaml` for every group with
`gateway: true`), and `base-apps/kagent/agent-templates/k8s-reader.yaml` (`modelConfig.name`).
Test: `tests/model-gateway/test_model_configs.py`.

- [ ] **Step 0: Measure one eval run per group (spec §2, §6).**
  - Find the newest eval-gate run in Loki (`{namespace="kagent"} |= "eval-gate"`) and its time
    span.
  - Sum `agent_cost_estimate_usd_total` per agent over that span with `window_total` through a
    port-forward to Prometheus, and map each agent to its group.
  - Raise any budget that does not cover the largest normal day plus two eval runs. Record the
    figures in the PR body and in `budgets.yaml`'s header comment.
- [ ] **Step 1: Write the failing tests.** For every group with `gateway: true` and every model:
  - the generated ModelConfig equals the reference config for that model (`anthropic-claude-<model>.yaml`)
    except `metadata.name`, `spec.anthropic.baseUrl = GATEWAY_URL` and
    `spec.apiKeySecret = model-gateway-key-<g>`;
  - every agent of that group references it;
  - every agent of a group with `gateway: false` still references the reference config;
  - `tests/agent-capability` and `tests/admission-policies` still pass.
- [ ] **Steps 2-4:** See them fail, implement and generate, then run every validator.
- [ ] **Step 5:** Commit. Push. Open PR C as a **draft**. Its body carries the spec §7 step 4
  checks to run after merging, the optional block drill, and the reminder to merge only once
  PR B is Healthy.

## Later (not executed until PR C is verified live)

- **PR D:** the other groups, in the spec §7 step 5 order. That means `gateway: true` per group,
  docs-checker's `ANTHROPIC_BASE_URL` in its Harness unless kagent passes `baseUrl` to byo
  runtimes, and `ui-default` through `base-apps/kagent.yaml` `providers.anthropic` if the chart
  accepts a base URL.
- **PR E:** the removal step (spec §4, last bullet). The near rule then reads the gateway's cost
  metric, the "over (estimate)" rule goes, and the eval corpus question is updated.
