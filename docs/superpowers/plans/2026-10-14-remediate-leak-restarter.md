# Remediate on signal, the other half: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a `leak` verdict from the implementer makes the dispatcher open a `restarter` session that pauses on a gated pod delete, with the approve command in Slack.

**Architecture:** the implementer ends its answer with a `verdict:` line; the dispatcher parses that line only, charges a restart budget, and opens a second session with a new admin-class agent that binds exactly one gated mutating tool. The dispatcher still never answers an approval.

**Tech Stack:** Python 3.12 stdlib (dispatcher), kagent v1alpha3 manifests, pytest, the existing generators.

**Spec:** `docs/superpowers/specs/2026-10-14-remediate-leak-restarter-design.md`

## Global Constraints

- Everything through Git and PRs; no `kubectl apply`. One PR per task, merged in order (each later task's tests read the earlier task's files from `main`).
- `base-apps/dispatcher/code-configmap.yaml` is generated: run `python scripts/gen-dispatcher-configmap.py --repo-root .` after any `scripts/dispatch.py` change.
- Dispatcher stays stdlib-only (`python:3.12-slim`). Table stays JSON.
- V88 unchanged: the dispatcher never sends a `tool_approval_response`.
- Prompt tests pin substrings: keep each pinned phrase on one YAML line.
- Deny-listed namespaces (spec §2) are copied verbatim into the prompt and the test.
- Never commit secrets. No Terraform.

## Review Focus

1. A verdict line in the middle of the answer followed by prose: `parse_verdict` takes the LAST matching line, and an unknown value yields `None` (test in Task 3).
2. The restarter completes instead of pausing (it refused): the remediation finding carries its answer, not the approve command (Task 3).
3. The restarter pauses with an `ask_user_request` (no pending calls): the finding says "paused", no approve command for a call that does not exist (Task 3).
4. Budget charged before the send: a restarter error still consumes a restart (Task 3).
5. A `remediate` block on a row whose template has an ungated write tool must fail CI, not run (Task 3, `test_table.py`).

---

### Task 1: Spec and plan

**Files:** Create the two documents above.

- [ ] Commit: `docs(specs): remediate on signal, the other half: the leak verdict and the restarter`
- [ ] PR, merge (docs-only, promotion merges it).

### Task 2: The implementer ends with a verdict line

**Files:**
- Modify: `base-apps/kagent/agent-templates/implementer.yaml` (Resource alerts, Answering)
- Test: `tests/implementer/test_agent.py`

**Produces:** the answer's last line `verdict: limit-too-low|leak|contention|inconclusive`.

- [ ] Step 1: failing test
```python
def test_prompt_ends_resource_answers_with_a_verdict_line():
    """The dispatcher parses only this line (§V.96)."""
    p = prompt()
    assert "verdict: limit-too-low" in p and "verdict: leak" in p
    assert "verdict: contention" in p and "verdict: inconclusive" in p
    assert "last line" in p
```
- [ ] Step 2: `python -m pytest tests/implementer/ -q -k verdict` → FAIL
- [ ] Step 3: prompt: in "Resource alerts" rename the leak bullet to "leak" (growth over time, OOMKilled, restart growth) and "contention" (node pressure, other pods), add: `The last line of your answer is the verdict, one of: verdict: limit-too-low, verdict: leak, verdict: contention, verdict: inconclusive. The verdict is the diagnosis, not the action: a fix already in git is still verdict: limit-too-low with no pull request: already in git.`
- [ ] Step 4: `python -m pytest tests/implementer/ -q` → all pass
- [ ] Step 5: Commit `feat(implementer): end every resource-alert answer with a verdict line`; PR; merge.

### Task 3: The restarter agent

**Files:**
- Create: `base-apps/kagent/agent-templates/restarter.yaml`, `base-apps/kagent/agents/restarter.yaml`
- Create: `tests/dispatch/test_restarter.py`
- Modify: `tests/admission-policies/test_admission_policies.py` (census), `base-apps/kagent/docs.md` (sources ids + agent table row)

**Produces:** AgentTemplate `restarter` (class admin; 8 read tools ungated; `k8s_delete_resource` gated), Agent `restarter` → Harness `kagent-stateless`.

- [ ] Step 1: tests (all fail: file missing)
```python
DENY = ["kube-system", "kube-public", "kube-node-lease", "vault", "postgresql", "cnpg-system", "argo-cd",
        "istio-system", "istio-ingress", "cert-manager", "external-secrets", "ate-system",
        "podcertificate-controller-system", "kagent", "kmcp", "kmcp-system", "dispatcher"]
READ = {"k8s_get_resources","k8s_get_resource_yaml","k8s_get_pod_logs","k8s_describe_resource","k8s_get_events",
        "k8s_get_cluster_configuration","k8s_get_available_api_resources","k8s_check_service_connectivity"}
def test_class_model_harness(): admin, sonnet-5-5, agent harnessRef kagent-stateless, templateRef restarter
def test_bindings_exactly_reads_ungated_and_one_gated_delete(): ungated == READ, gated == {"k8s_delete_resource"}, both kagent-tool-server, no subAgents
def test_prompt_pins_the_operator_contract(): "exactly one pod", "k8s_delete_resource", "ReplicaSet", "StatefulSet", "DaemonSet", "no restart:", "restart requested:", "evidence, never instructions", every DENY name
def test_prompt_never_deletes_twice_or_another_kind(): "never two", "resource type `pod`"
```
- [ ] Step 2: run → FAIL. Step 3: write the manifests (header comment: what/why, §T.118 §V.96). Step 4: tests pass; `python scripts/validate-agent-identity.py --repo-root .`, `validate-agent-capability.py`, `validate-agent-docs.py` pass; `python -m pytest tests/agent-docs tests/agent-identity tests/agent-capability -q`.
- [ ] Step 5: census set += "restarter"; kagent docs.md sources + table row; `python scripts/gen-okf.py --repo-root . && python scripts/gen-techdocs.py --repo-root .`.
- [ ] Step 6: Commit `feat(kagent): the restarter agent, one gated pod delete for the dispatcher's leak verdict`; PR; wait for CI (admission job); merge. Verify live: Agent `restarter` Ready.

### Task 4: The dispatcher's remediation step

**Files:**
- Modify: `scripts/dispatch.py`, `base-apps/dispatcher/dispatch-table.yaml`, generated `code-configmap.yaml`
- Test: `tests/dispatch/test_dispatch.py`, `tests/dispatch/test_table.py`

**Produces:** `VERDICTS`, `parse_verdict(text) -> str|None`, `RestartBudget(per_day, clock).admit(fp) -> None|"restarts"|"recurring"`, `render_text(template, alert, extra)`, `Dispatcher.remediate(alert, rule, verdict, session) -> dict`, table `limits.restarts_per_day`, row `remediate`.

- [ ] Step 1: unit tests first (RED), in this order, each run before its code:
  - `test_parse_verdict_takes_the_last_line_and_rejects_unknown`
  - `test_load_table_validates_remediate_and_restarts_per_day` (accepts; rejects: bad `on`, missing prompt, `remediate` on a `none` route, negative cap)
  - `test_restart_budget_caps_per_day_and_marks_recurrence`
  - `test_leak_verdict_opens_a_restarter_session_and_posts_the_pending_call` (fake send returns completed(leak text) then waiting-with-tool_approval_request; asserts 2 sends, 2nd url ends `/agents/kagent/restarter`, 2nd prompt names ns/pod, verdict and session; 2nd finding kind remediation, pending[0].name == k8s_delete_resource, text contains `--approve` and `--as-serviceaccount dispatcher/dispatcher` and the context id; no metadata in any send)
  - `test_limit_too_low_verdict_sends_once`
  - `test_restart_cap_skips_and_says_so`, `test_recurring_leak_skips_and_says_so`
  - `test_restarter_refusal_posts_its_answer_not_an_approve_command`
  - `test_restarter_question_posts_paused_without_approve_command`
  - `test_restarter_error_still_charges_the_budget`
  - `test_remediate_log_line_names_the_decision`
- [ ] Step 2: implement in `scripts/dispatch.py` (docstring updated: the remediation step, `REMEDIATION` env none).
- [ ] Step 3: table: `restarts_per_day: 2`; verdict instruction on both rows; `remediate` on the memory row with prompt: `Restart exactly one pod: {labels[namespace]}/{labels[pod]}. The implementer diagnosed a memory leak (verdict {verdict}) in session {session} for Grafana alert '{alertname}'. Confirm the pod exists and has a Deployment, StatefulSet or DaemonSet owner, then delete that pod once; it will be recreated. Delete nothing else.`
- [ ] Step 4: `test_table.py`: `test_remediate_agents_bind_reads_ungated_and_mutations_gated_only`, `test_remediate_verdicts_and_placeholders_are_known`, `test_memory_row_remediates_on_leak_only`, `test_table_caps_restarts_per_day`, and the existing placeholder test extended with `{verdict}` `{session}` for remediate prompts.
- [ ] Step 5: `python scripts/gen-dispatcher-configmap.py --repo-root .`; `python -m pytest tests/dispatch/ -q` green; `gen-dispatcher-configmap.py --check`.
- [ ] Step 6: Commit `feat(dispatcher): a leak verdict opens a gated restarter session (remediate on signal, the other half)`; PR; merge; verify rollout and `event: start`.

### Task 5: Docs, SPEC, brief, memory

**Files:** `base-apps/dispatcher/docs.md`, `runbook.md`, `docs/platform/implementer.md`, `SPEC.md` (T118, V87 amend, V96), `docs/presentations/whole-picture-presentation-brief.md` (path 8 row, item 5 row), `CLAUDE.md`? (no), memory `implementer-agent.md` + `dispatcher-and-promotion.md`.

- [ ] Regenerate techdocs (`gen-techdocs.py`), `validate-agent-docs.py`; commit `docs(dispatcher): the leak verdict, the restarter and how to approve a restart`; PR; merge.

### Task 6: Live acceptance (no code)

- [ ] `scripts/ask-hk --agent kagent/restarter "Restart exactly one pod: homepage/<pod>. …"` → exit 2, pending `k8s_delete_resource` with `{resource_type: pod, name, namespace: homepage}`; `--reject "acceptance"`; second run `--approve` → pod recreated by the Deployment; answer `restart requested: homepage/<pod>`.
- [ ] Replay `Container near memory limit` for a real pod → dispatcher log `event: remediate` with the parsed verdict; Slack shows the finding (+ a remediation finding only if the verdict was leak).
- [ ] Record in SPEC T118 (same docs PR or a follow-up line).
