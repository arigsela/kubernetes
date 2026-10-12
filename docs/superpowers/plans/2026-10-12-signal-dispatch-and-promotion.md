# Signal Dispatch and Promotion Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route Grafana alerts to a read-only agent investigation by a declared table (ADP paths 1 and 7), and merge one declared class of pull requests without a human under a policy read at the PR's base commit (path 5).

**Architecture:** Track A is a new in-scope app `dispatcher`: a stdlib-only Python HTTP service (`scripts/dispatch.py`, shipped in a generated ConfigMap beside `scripts/kagent_a2a.py`) that receives a second copy of every Grafana notification, matches it against a JSON table in a ConfigMap, and, for `agent:` routes, opens a new A2A session as `X-User-Id: dispatcher` and posts the answer to a new n8n webhook that formats it for Slack. Track B is `scripts/promote.py` plus `.github/promotion-policy.yaml` and a `workflow_run`-triggered workflow that merges `review:skip` Renovate PRs by rebase using a dedicated GitHub App. Both tracks are independent; A1–A9 and B1–B5 can run in parallel.

**Tech Stack:** Python 3.12 standard library (`http.server`, `urllib`, `json`, `threading`); `pyyaml` only in generators and tests; pytest 8.3.3. Kubernetes manifests (Deployment, Service, ConfigMaps, Argo CD Application); Grafana provisioning; n8n workflow JSON; GitHub Actions with `actions/create-github-app-token`.

**Spec:** `docs/superpowers/specs/2026-10-12-signal-dispatch-and-promotion-design.md`. The plan argues from it; read it first. Its §V rows (V86–V90) are the acceptance criteria.

## Global Constraints

- Python 3.12. Base deps for tests `pyyaml==6.0.2 pytest==8.3.3`. `scripts/dispatch.py` and `scripts/promote.py` import only the standard library (plus `kagent_a2a`), so the dispatcher container runs `python:3.12-slim` with no `pip install`.
- `scripts/kagent_a2a.py` is **not** changed. `dispatch.py` imports it by `sys.path.insert(0, <its own dir>)`, exactly as `scripts/ask-hk` does.
- `base-apps/dispatcher/code-configmap.yaml` is **generated** by `scripts/gen-dispatcher-configmap.py` from `scripts/dispatch.py` and `scripts/kagent_a2a.py`; CI runs `--check`. Never hand-edit it.
- `base-apps/dispatcher/dispatch-table.yaml` is the source of truth for routing. Tests parse the ConfigMap's `dispatch.json` key.
- The dispatcher never forwards to n8n's `grafana-alerts` webhook (V86) and never sends `tool_approval_response` / `ask_user_response` metadata (V88). Grep-able: `approval_metadata` and `answer_metadata` do not appear in `dispatch.py`.
- Every A2A send uses `user="dispatcher"` and no `context_id` (a new session per investigation, V88).
- Log lines are JSON on stdout and never contain the agent's answer text.
- Policy files are read at the PR's **base** SHA via the GitHub contents API (V89).
- `.github/**` is in `deny_prefixes`; `promote.yaml` triggers only on `workflow_run` (never `pull_request`), so the `main` version of the workflow always runs.
- Manifests: `yamllint -c .yamllint.yaml <files>`, then kubeconform with the CRDs-catalog schema location from `AGENTS.md`; skip `mkdocs.yml`.
- New app contract: `docs.md`, `runbook.md`, `catalog-info.yaml` per `templates/agent-docs/README.md`; the Application sets `directory.exclude: '{catalog-info.yaml,mkdocs.yml}'`; add `dispatcher` to `scripts/agent-docs-scope.txt`; regenerate with `scripts/gen-techdocs.py --repo-root .` and `scripts/gen-okf.py --repo-root .`.
- Commits: Conventional Commits with scope, e.g. `feat(dispatcher): …`, `feat(promotion): …`.
- Nothing is applied by hand to the cluster. Vault is not touched (neither track needs a credential in Vault).

## Review Focus

1. **A Grafana payload with no `alerts` array, or an alert with no `labels`** (Grafana's contact-point "Test" button sends a synthetic alert; a malformed body is possible). The dispatcher must answer 200 with zero decisions for the former and treat the latter as `unmatched`, never 500. Pinned in Task A3 (`test_handle_grafana_tolerates_missing_alerts_and_labels`).
2. **Two alerts for the same pod arrive in one notification and again five minutes later.** One investigation, the second and third are `skip-repeat`. Pinned in Task A2 (`test_budget_suppresses_repeat_fingerprint`) and A3 (`test_same_fingerprint_twice_investigates_once`).
3. **The agent pauses (`TASK_STATE_INPUT_REQUIRED`) or the send raises.** The finding is still posted (with "paused" text or the error), the service keeps serving, and no approval metadata is sent. Pinned in Task A3 (`test_waiting_reply_posts_paused_finding_without_answering`, `test_a2a_error_posts_error_finding`).
4. **A Renovate PR labeled `review:skip` whose files include one under a deny prefix, or whose head moved after the checks ran.** Both must be `owner`, and a merge must fail (not merge a newer head) because the merge call pins the head SHA. Pinned in Task B2 (`test_deny_prefix_wins_over_skip_label`) and B3 (`test_merge_sends_head_sha`).
5. **A check run still `in_progress` when `promote.py` runs** (pr-triage finished before validate). The decision is `owner` with reason `checks pending`, nothing is commented, and the next `workflow_run` re-evaluates. Pinned in Task B2 (`test_pending_check_is_owner_not_auto`).

---

## File structure

**Track A (dispatcher)**

- Create `scripts/dispatch.py` — table loading and validation, matching, prompt rendering, the budget, the `Dispatcher` (decide → investigate → post finding), the HTTP handler, `main()`.
- Create `scripts/gen-dispatcher-configmap.py` — embeds `dispatch.py` + `kagent_a2a.py` into `base-apps/dispatcher/code-configmap.yaml`; `--check`.
- Create `base-apps/dispatcher.yaml` (Argo CD Application) and `base-apps/dispatcher/`: `namespace.yaml`, `dispatch-table.yaml`, `code-configmap.yaml` (generated), `deployment.yaml`, `service.yaml`, `docs.md`, `runbook.md`, `catalog-info.yaml`.
- Create `tests/dispatch/test_table.py` (table contract vs. alert rules and agent templates), `tests/dispatch/test_dispatch.py` (unit + HTTP), `tests/dispatch/test_gen_configmap.py`.
- Modify `base-apps/logging/grafana-alerting.yaml` (second integration on the contact point) and `base-apps/logging/grafana-deployment.yaml` (checksum annotation).
- Modify `base-apps/n8n/workflows-configmap.yaml` (new `agent-findings.json`), `base-apps/n8n/deployments.yaml` (import + activate it), `base-apps/n8n/docs.md` (list it).
- Modify `.github/workflows/validate.yaml` (job `dispatch-validate`), `AGENTS.md` (generator table), `scripts/agent-docs-scope.txt`, `SPEC.md` (V86–V88, T105).

**Track B (promotion)**

- Create `.github/promotion-policy.yaml`, `scripts/promote.py`, `.github/workflows/promote.yaml`, `tests/promotion/test_promote.py`, `docs/platform/promotion.md`.
- Modify `.github/workflows/validate.yaml` (job `promotion-validate`), `CLAUDE.md` (PR section), `SPEC.md` (V89–V90, T106, T107 owner action).

---

## Track A — the dispatcher

### Task A1: The dispatch table and its contract tests

**Files:**
- Create: `base-apps/dispatcher/dispatch-table.yaml`
- Create: `tests/dispatch/test_table.py`

**Interfaces:**
- Produces: the ConfigMap `dispatcher-table` in namespace `dispatcher`, key `dispatch.json`, schema `{"version":1,"limits":{...},"rules":[...]}` as in the spec. Later tasks mount it at `/etc/dispatch/dispatch.json`.

- [ ] **Step 1: Write the failing contract tests**

```python
# tests/dispatch/test_table.py
"""The dispatch table is complete over the Grafana rules and routes only to read-only agents (V87)."""
from pathlib import Path
import json

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
TABLE = REPO / "base-apps/dispatcher/dispatch-table.yaml"
ALERTING = REPO / "base-apps/logging/grafana-alerting.yaml"
TEMPLATES = REPO / "base-apps/kagent/agent-templates"
TAXONOMY = REPO / "base-apps/admission-policies/agent-capability-taxonomy.yaml"


def load_table() -> dict:
    cm = [d for d in yaml.safe_load_all(TABLE.read_text()) if d and d.get("kind") == "ConfigMap"][0]
    assert cm["metadata"]["name"] == "dispatcher-table"
    assert cm["metadata"]["namespace"] == "dispatcher"
    return json.loads(cm["data"]["dispatch.json"])


def alert_titles() -> set[str]:
    cm = [d for d in yaml.safe_load_all(ALERTING.read_text()) if d and d.get("kind") == "ConfigMap"][0]
    rules = yaml.safe_load(cm["data"]["rules.yaml"])
    return {r["title"] for g in rules["groups"] for r in g["rules"]}


def read_tools() -> set[str]:
    cm = yaml.safe_load(TAXONOMY.read_text())
    return set(json.loads(cm["data"]["read"]))


def prompt_of(row: dict) -> str:
    """A prompt is a string or a list of strings (joined with spaces by the dispatcher)."""
    prompt = row.get("prompt") or ""
    return " ".join(p.strip() for p in prompt) if isinstance(prompt, list) else prompt


def template_tools(name: str) -> tuple[set[str], bool]:
    """(every bound tool, any binding with requireApproval true)."""
    doc = yaml.safe_load((TEMPLATES / f"{name}.yaml").read_text())
    tools, gated = set(), False
    for binding in doc["spec"].get("tools") or []:
        mcp = binding.get("mcp") or {}
        tools.update(mcp.get("tools") or [])
        gated = gated or bool(mcp.get("requireApproval"))
    return tools, gated


def test_table_has_version_limits_and_rules():
    table = load_table()
    assert table["version"] == 1
    assert table["limits"]["investigations_per_hour"] >= 1
    assert table["limits"]["repeat_after_hours"] >= 1
    assert table["rules"], "an empty table routes nothing; say so with explicit none rows"


def test_every_grafana_rule_title_has_a_row():
    rows = {r["match"].get("alertname") for r in load_table()["rules"] if r["source"] == "grafana"}
    missing = alert_titles() - rows
    assert not missing, f"add a row (route none is fine, with a why) for: {sorted(missing)}"


def test_every_row_has_an_id_a_reason_and_a_known_route():
    seen = set()
    for row in load_table()["rules"]:
        assert row["id"] not in seen, f"duplicate id {row['id']}"
        seen.add(row["id"])
        assert row["why"].strip(), f"{row['id']}: why is required"
        assert row["route"] == "none" or row["route"].startswith("agent:"), row["route"]
        if row["route"].startswith("agent:"):
            assert prompt_of(row).strip(), f"{row['id']}: an agent route needs a prompt"


def test_agent_routes_are_read_only_templates():
    read = read_tools()
    for row in load_table()["rules"]:
        if not row["route"].startswith("agent:"):
            continue
        name = row["route"].split(":", 1)[1]
        assert (TEMPLATES / f"{name}.yaml").exists(), f"{row['id']}: no AgentTemplate {name}"
        tools, gated = template_tools(name)
        assert tools, f"{name} binds no tools; nothing to investigate with"
        assert tools <= read, f"{name} binds non-read tools: {sorted(tools - read)}"
        assert not gated, f"{name} has a requireApproval binding; the dispatcher never answers approvals"


def test_prompts_only_use_known_placeholders():
    import string
    allowed = {"alertname", "status", "labels", "annotations", "starts_at"}
    for row in load_table()["rules"]:
        for _, field, _, _ in string.Formatter().parse(prompt_of(row)):
            if field:
                assert field.split("[")[0] in allowed, f"{row['id']}: unknown placeholder {field}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/dispatch/test_table.py -q`
Expected: FAIL with `FileNotFoundError` for `base-apps/dispatcher/dispatch-table.yaml`.

- [ ] **Step 3: Write the table**

```yaml
# base-apps/dispatcher/dispatch-table.yaml
---
# Dispatch table — THE single place that says which signal an agent looks at (ADP path 1).
#
# Humans get every alert regardless: Grafana's contact point delivers to n8n → Slack on its
# own integration (V86). This table only ADDS agent investigations. Rules are checked in
# order; the first whose `match` keys all equal the alert's labels wins. `route` is `none`
# or `agent:<AgentTemplate>`; agent routes need a `prompt`, and may only name templates whose
# every tool is `read` in the capability taxonomy with no requireApproval binding (V87,
# tests/dispatch/test_table.py). Every Grafana rule title must have a row: `none` with a
# `why` is a decision, a missing row fails CI.
#
# Placeholders in prompts: {alertname} {status} {starts_at} {labels[<name>]} {annotations[<name>]};
# a missing key renders as `?`. A prompt is a string, or a list of strings joined with spaces
# (JSON has no line continuation; the list keeps lines under 200 characters).
#
# JSON, not YAML, so the dispatcher needs no pip install (python:3.12-slim, stdlib only).
apiVersion: v1
kind: ConfigMap
metadata:
  name: dispatcher-table
  namespace: dispatcher
  labels:
    app: dispatcher
data:
  dispatch.json: |
    {
      "version": 1,
      "limits": {"investigations_per_hour": 4, "repeat_after_hours": 6},
      "rules": [
        {
          "id": "pod-cpu-starved",
          "source": "grafana",
          "match": {"alertname": "Pod CPU starved"},
          "route": "agent:k8s-reader",
          "prompt": [
            "Grafana alert '{alertname}' is firing for pod {labels[namespace]}/{labels[pod]}: {annotations[summary]}",
            "Investigate with your read-only tools: describe the pod, read its recent events and the last 100 log lines,",
            "and say whether the pod's own CPU limit or node contention is the likely cause, quoting what you saw.",
            "Do not propose running changes on the cluster; a fix is a pull request. Runbook hint: {annotations[runbook]}"
          ],
          "why": "A starved pod needs describe + events + logs read before anyone can act; the read-only investigator does exactly that and nothing else."
        },
        {
          "id": "container-near-memory-limit",
          "source": "grafana",
          "match": {"alertname": "Container near memory limit"},
          "route": "agent:k8s-reader",
          "prompt": [
            "Grafana alert '{alertname}' is firing for {labels[namespace]}/{labels[pod]} container {labels[container]}: {annotations[summary]}",
            "Investigate with your read-only tools: describe the pod, check for OOMKilled in its last state and events,",
            "read the last 100 log lines, and say whether this looks like a leak or a limit set too low, quoting what you saw.",
            "Do not propose running changes on the cluster; a fix is a pull request. Runbook hint: {annotations[runbook]}"
          ],
          "why": "Memory pressure has two causes with two fixes; the evidence (last state, events, logs) is readable without any write tool."
        },
        {
          "id": "agent-ungated-tool",
          "source": "grafana",
          "match": {"alertname": "Agent invoked a gated tool with NO approval"},
          "route": "none",
          "why": "A security finding about the agents themselves. A human reads the record behind the SELECT-only credential; no agent investigates the agents."
        },
        {
          "id": "agent-audit-risk-high",
          "source": "grafana",
          "match": {"alertname": "Agent session scored high risk"},
          "route": "none",
          "why": "Same as above: the risk UI and the acknowledged file are the human's tools, and the record is not readable by any in-cluster agent."
        },
        {
          "id": "falco-sensitive-detection",
          "source": "grafana",
          "match": {"alertname": "Falco detected a sensitive-file read or unexpected API access"},
          "route": "none",
          "why": "Falco output can name sensitive paths; until a redaction rule for its lines is agreed, no summary of them goes to Slack through an agent. Next source candidate."
        },
        {
          "id": "substrate-ca-expiry",
          "source": "grafana",
          "match": {"alertname": "Substrate CA expires in under 30 days"},
          "route": "none",
          "why": "The runbook is deterministic (rotate the CAs); there is nothing to investigate."
        }
      ]
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/dispatch/test_table.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Lint and commit**

```bash
yamllint -c .yamllint.yaml base-apps/dispatcher/dispatch-table.yaml
git add base-apps/dispatcher/dispatch-table.yaml tests/dispatch/test_table.py
git commit -m "feat(dispatcher): the dispatch table, complete over the alert rules and read-only by test"
```

### Task A2: Table loading, matching, prompt rendering and the budget

**Files:**
- Create: `scripts/dispatch.py` (first half)
- Create: `tests/dispatch/test_dispatch.py` (first tests)

**Interfaces:**
- Produces: `load_table(text: str) -> dict` (raises `TableError`), `match_rule(table, source, labels) -> dict | None`, `render_prompt(rule, alert) -> str`, `class Budget(per_hour: int, repeat_after_hours: int, clock=time.time)` with `admit(fingerprint) -> str | None` (`None` = admitted, else `"repeat"` or `"budget"`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/dispatch/test_dispatch.py
"""scripts/dispatch.py: table → decision → investigation → finding, with a fake agent."""
from pathlib import Path
import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("dispatch", REPO / "scripts" / "dispatch.py")
dp = importlib.util.module_from_spec(_spec)
sys.modules["dispatch"] = dp   # dataclasses resolve the module by name at class creation
_spec.loader.exec_module(dp)
a2a = dp.a2a

TABLE = {
    "version": 1,
    "limits": {"investigations_per_hour": 2, "repeat_after_hours": 6},
    "rules": [
        {"id": "cpu", "source": "grafana", "match": {"alertname": "Pod CPU starved"},
         "route": "agent:k8s-reader", "prompt": "Pod {labels[namespace]}/{labels[pod]}: {annotations[summary]}",
         "why": "test"},
        {"id": "quiet", "source": "grafana", "match": {"alertname": "Quiet"}, "route": "none", "why": "test"},
    ],
}


def alert(name="Pod CPU starved", status="firing", fingerprint="fp1", **labels):
    labels.setdefault("namespace", "n8n")
    labels.setdefault("pod", "n8n-0")
    return {"status": status, "fingerprint": fingerprint, "startsAt": "2026-10-12T10:00:00Z",
            "labels": {"alertname": name, **labels}, "annotations": {"summary": "waiting 40% for CPU"}}


# ---------------------------------------------------------------- table

def test_load_table_accepts_the_shape():
    assert dp.load_table(json.dumps(TABLE))["limits"]["investigations_per_hour"] == 2


@pytest.mark.parametrize("mutate, message", [
    (lambda t: t.update(version=2), "version"),
    (lambda t: t["limits"].pop("repeat_after_hours"), "repeat_after_hours"),
    (lambda t: t["rules"][0].pop("why"), "why"),
    (lambda t: t["rules"][0].update(route="human:slack"), "route"),
    (lambda t: t["rules"][0].pop("prompt"), "prompt"),
    (lambda t: t["rules"].append(dict(t["rules"][1])), "duplicate"),
])
def test_load_table_rejects_bad_tables(mutate, message):
    table = json.loads(json.dumps(TABLE))
    mutate(table)
    with pytest.raises(dp.TableError, match=message):
        dp.load_table(json.dumps(table))


def test_prompt_list_is_joined_with_spaces():
    table = json.loads(json.dumps(TABLE))
    table["rules"][0]["prompt"] = ["Pod {labels[pod]}:", " look at it. "]
    assert dp.load_table(json.dumps(table))["rules"][0]["prompt"] == "Pod {labels[pod]}: look at it."
    table["rules"][0]["prompt"] = ["ok", 3]
    with pytest.raises(dp.TableError, match="only strings"):
        dp.load_table(json.dumps(table))


def test_match_rule_first_match_wins_and_all_keys_must_equal():
    table = dp.load_table(json.dumps(TABLE))
    assert dp.match_rule(table, "grafana", {"alertname": "Pod CPU starved", "pod": "x"})["id"] == "cpu"
    assert dp.match_rule(table, "grafana", {"alertname": "Other"}) is None
    assert dp.match_rule(table, "github-check", {"alertname": "Pod CPU starved"}) is None


def test_render_prompt_fills_labels_and_marks_missing_keys():
    rule = TABLE["rules"][0]
    assert dp.render_prompt(rule, alert()) == "Pod n8n/n8n-0: waiting 40% for CPU"
    assert dp.render_prompt(rule, {"labels": {"alertname": "x"}, "annotations": {}}) == "Pod ?/?: ?"


# ---------------------------------------------------------------- budget

def test_budget_suppresses_repeat_fingerprint():
    now = [1000.0]
    b = dp.Budget(per_hour=5, repeat_after_hours=6, clock=lambda: now[0])
    assert b.admit("fp1") is None
    assert b.admit("fp1") == "repeat"
    now[0] += 6 * 3600
    assert b.admit("fp1") is None


def test_budget_caps_investigations_per_hour():
    now = [1000.0]
    b = dp.Budget(per_hour=2, repeat_after_hours=6, clock=lambda: now[0])
    assert b.admit("a") is None and b.admit("b") is None
    assert b.admit("c") == "budget"
    now[0] += 3601
    assert b.admit("c") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/dispatch/test_dispatch.py -q`
Expected: FAIL with `FileNotFoundError` for `scripts/dispatch.py`.

- [ ] **Step 3: Write the first half of `scripts/dispatch.py`**

```python
#!/usr/bin/env python3
"""Signal dispatcher — routes a Grafana alert to a read-only agent investigation, by table.

    POST /signal/grafana      Grafana contact-point webhook payload (its second integration)
    GET  /healthz             process is up
    GET  /readyz              the table loaded

Humans get every alert regardless (Grafana → n8n → Slack on its own integration, V86);
this service only ADDS investigations. For each alert: match the table
(/etc/dispatch/dispatch.json, see base-apps/dispatcher/dispatch-table.yaml), check the
budget, and for an `agent:<name>` route open a NEW A2A session as X-User-Id `dispatcher`
with the rendered prompt, then post the finding to n8n's agent-findings webhook (→ Slack).

Never forwards the alert to n8n, never answers an approval or a question (V88): if the
agent pauses, the finding says so and points at the kagent UI. Logs one JSON line per
decision and per finding, never the answer text.

Environment (all have defaults for the cluster):
    DISPATCH_TABLE          /etc/dispatch/dispatch.json
    KAGENT_A2A_BASE         http://kagent-controller.kagent.svc.cluster.local:8083
    N8N_WEBHOOK_BASE        http://n8n.n8n.svc.cluster.local:5678/webhook
    DISPATCH_USER           dispatcher
    INVESTIGATION_TIMEOUT   300   (seconds the A2A send may block)
    KAGENT_UI_SESSION_URL   https://kagent.arigsela.com/agents/{session}/chat
    PORT                    8080
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kagent_a2a as a2a  # noqa: E402

ANSWER_LIMIT = 3000       # chars of the agent's answer forwarded to Slack
SOURCES = {"grafana"}


class TableError(ValueError):
    """The dispatch table is malformed; refuse to serve rather than route wrongly."""


# ------------------------------------------------------------------ the table

def load_table(text: str) -> dict:
    table = json.loads(text)
    if table.get("version") != 1:
        raise TableError("version must be 1")
    limits = table.get("limits") or {}
    for key in ("investigations_per_hour", "repeat_after_hours"):
        if not isinstance(limits.get(key), int) or limits[key] < 0:
            raise TableError(f"limits.{key} must be a non-negative integer")
    seen = set()
    for rule in table.get("rules") or []:
        rid = rule.get("id") or "?"
        for key in ("id", "source", "match", "route", "why"):
            if key not in rule:
                raise TableError(f"rule {rid}: missing {key}")
        if rule["id"] in seen:
            raise TableError(f"duplicate rule id {rule['id']}")
        seen.add(rule["id"])
        if rule["source"] not in SOURCES:
            raise TableError(f"rule {rid}: unknown source {rule['source']!r}")
        if not isinstance(rule["match"], dict) or not rule["match"]:
            raise TableError(f"rule {rid}: match must be a non-empty object")
        route = rule["route"]
        if route != "none" and not (isinstance(route, str) and route.startswith("agent:")):
            raise TableError(f"rule {rid}: route must be 'none' or 'agent:<name>', got {route!r}")
        prompt = rule.get("prompt")
        if isinstance(prompt, list):          # a list of strings keeps the JSON lines short
            if not all(isinstance(p, str) for p in prompt):
                raise TableError(f"rule {rid}: prompt list must contain only strings")
            rule["prompt"] = prompt = " ".join(p.strip() for p in prompt)
        if route.startswith("agent:") and not str(prompt or "").strip():
            raise TableError(f"rule {rid}: an agent route needs a prompt")
        if not str(rule["why"]).strip():
            raise TableError(f"rule {rid}: why is required")
    table.setdefault("rules", [])
    return table


def match_rule(table: dict, source: str, labels: dict) -> dict | None:
    """First rule of this source whose match keys all equal the alert's labels."""
    for rule in table["rules"]:
        if rule["source"] != source:
            continue
        if all(labels.get(k) == v for k, v in rule["match"].items()):
            return rule
    return None


class _Safe(dict):
    """A mapping that renders a missing key as '?' instead of raising."""

    def __missing__(self, key):
        return "?"


def render_prompt(rule: dict, alert: dict) -> str:
    labels = _Safe(alert.get("labels") or {})
    return rule["prompt"].format_map(_Safe(
        alertname=labels.get("alertname", "?"),
        status=alert.get("status") or "?",
        starts_at=alert.get("startsAt") or "?",
        labels=labels,
        annotations=_Safe(alert.get("annotations") or {}),
    ))


# ------------------------------------------------------------------ the budget

class Budget:
    """Bounds investigations: at most `per_hour` sends, and one per fingerprint per
    `repeat_after_hours`. In memory: a restart forgets, which errs on the side of looking."""

    def __init__(self, per_hour: int, repeat_after_hours: int, clock=time.time):
        self.per_hour = per_hour
        self.repeat_s = repeat_after_hours * 3600
        self.clock = clock
        self._starts: list[float] = []
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def admit(self, fingerprint: str) -> str | None:
        now = self.clock()
        with self._lock:
            self._starts = [t for t in self._starts if now - t < 3600]
            last = self._seen.get(fingerprint)
            if last is not None and now - last < self.repeat_s:
                return "repeat"
            if len(self._starts) >= self.per_hour:
                return "budget"
            self._starts.append(now)
            self._seen[fingerprint] = now
            return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/dispatch/test_dispatch.py -q`
Expected: `12 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/dispatch.py tests/dispatch/test_dispatch.py
git commit -m "feat(dispatcher): table loading, matching, prompt rendering and the budget"
```

### Task A3: Decide, investigate, post the finding

**Files:**
- Modify: `scripts/dispatch.py` (append)
- Modify: `tests/dispatch/test_dispatch.py` (append)

**Interfaces:**
- Consumes: Task A2's functions; `a2a.send_message`, `a2a.parse_result`, `a2a.resolve_endpoint`.
- Produces: `@dataclass Config(table_path, a2a_base, n8n_base, user, timeout, ui_url)` with `Config.from_env(env) -> Config`; `class Dispatcher(table, config, *, budget=None, send=a2a.send_message, post=None, log=None, run_async=True)` with `decide(alert) -> dict`, `handle_grafana(payload) -> list[dict]`, `investigate(alert, rule) -> dict`; module function `post_json(url, obj, timeout=10.0) -> None`; `log_line(obj)`.

- [ ] **Step 1: Write the failing tests (append to `tests/dispatch/test_dispatch.py`)**

```python
# ---------------------------------------------------------------- dispatcher

def completed_task(text="The pod is throttled by its own 100m limit.", session="sess-1"):
    return {"task": {"id": "task-1", "contextId": session,
                     "status": {"state": a2a.STATE_COMPLETED},
                     "artifacts": [{"parts": [{"text": text}]}]}}


def waiting_task(session="sess-2"):
    return {"task": {"id": "task-2", "contextId": session,
                     "status": {"state": "TASK_STATE_INPUT_REQUIRED",
                                "message": {"parts": [{"text": "Which namespace?"}]}}}}


class Fake:
    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error
        self.sent, self.posted, self.logged = [], [], []

    def send(self, url, text, **kw):
        self.sent.append({"url": url, "text": text, **kw})
        if self.error:
            raise self.error
        return self.reply

    def post(self, url, obj, timeout=10.0):
        self.posted.append((url, obj))

    def log(self, obj):
        self.logged.append(obj)


def make(fake, table=TABLE, per_hour=2):
    t = json.loads(json.dumps(table))
    t["limits"]["investigations_per_hour"] = per_hour
    cfg = dp.Config(table_path="/dev/null", a2a_base="http://a2a:8083", n8n_base="http://n8n:5678/webhook",
                    user="dispatcher", timeout=5.0, ui_url="https://ui/agents/{session}/chat")
    return dp.Dispatcher(dp.load_table(json.dumps(t)), cfg, send=fake.send, post=fake.post,
                         log=fake.log, run_async=False)


def test_firing_matched_alert_is_investigated_as_dispatcher_in_a_new_session():
    fake = Fake(reply=completed_task())
    d = make(fake)
    decisions = d.handle_grafana({"alerts": [alert()]})
    assert [x["decision"] for x in decisions] == ["investigate"]
    sent = fake.sent[0]
    assert sent["url"] == "http://a2a:8083/agents/kagent/k8s-reader"
    assert sent["user"] == "dispatcher"
    assert sent.get("context_id") is None and sent.get("task_id") is None and sent.get("metadata") is None
    assert sent["text"] == "Pod n8n/n8n-0: waiting 40% for CPU"
    url, finding = fake.posted[0]
    assert url == "http://n8n:5678/webhook/agent-findings"
    assert finding["session"] == "sess-1" and finding["agent"] == "k8s-reader"
    assert finding["ui"] == "https://ui/agents/sess-1/chat"
    assert finding["text"].startswith("The pod is throttled")
    assert finding["error"] is None
    events = [x["event"] for x in fake.logged]
    assert events == ["dispatch", "finding"]
    assert all("text" not in x for x in fake.logged)


def test_none_resolved_and_unmatched_are_logged_not_sent():
    fake = Fake(reply=completed_task())
    d = make(fake)
    decisions = d.handle_grafana({"alerts": [alert("Quiet"), alert(status="resolved"), alert("Nope")]})
    assert [x["decision"] for x in decisions] == ["none", "resolved", "unmatched"]
    assert fake.sent == [] and fake.posted == []


def test_same_fingerprint_twice_investigates_once():
    fake = Fake(reply=completed_task())
    d = make(fake)
    first = d.handle_grafana({"alerts": [alert(), alert()]})
    second = d.handle_grafana({"alerts": [alert()]})
    assert [x["decision"] for x in first + second] == ["investigate", "skip-repeat", "skip-repeat"]
    assert len(fake.sent) == 1


def test_budget_exhaustion_is_logged_as_skip_budget():
    fake = Fake(reply=completed_task())
    d = make(fake, per_hour=1)
    out = d.handle_grafana({"alerts": [alert(fingerprint="a"), alert(fingerprint="b", pod="other")]})
    assert [x["decision"] for x in out] == ["investigate", "skip-budget"]


def test_waiting_reply_posts_paused_finding_without_answering():
    fake = Fake(reply=waiting_task())
    make(fake).handle_grafana({"alerts": [alert()]})
    _, finding = fake.posted[0]
    assert finding["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert "paused" in finding["text"]
    assert finding["ui"] == "https://ui/agents/sess-2/chat"
    assert len(fake.sent) == 1, "the dispatcher never answers a paused task"


def test_a2a_error_posts_error_finding():
    fake = Fake(error=a2a.A2AError("A2A error -32000: boom"))
    make(fake).handle_grafana({"alerts": [alert()]})
    _, finding = fake.posted[0]
    assert finding["error"].startswith("A2A error")
    assert finding["session"] is None and finding["text"] is None
    assert fake.logged[-1]["event"] == "finding" and fake.logged[-1]["error"].startswith("A2A error")


def test_handle_grafana_tolerates_missing_alerts_and_labels():
    fake = Fake(reply=completed_task())
    d = make(fake)
    assert d.handle_grafana({}) == []
    out = d.handle_grafana({"alerts": [{"status": "firing"}]})
    assert out[0]["decision"] == "unmatched" and out[0]["alertname"] == "?"


def test_config_from_env_defaults_and_overrides():
    cfg = dp.Config.from_env({})
    assert cfg.a2a_base == a2a.IN_CLUSTER_BASE
    assert cfg.user == "dispatcher" and cfg.timeout == 300.0
    cfg = dp.Config.from_env({"INVESTIGATION_TIMEOUT": "12", "DISPATCH_USER": "x"})
    assert cfg.timeout == 12.0 and cfg.user == "x"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/dispatch/test_dispatch.py -q`
Expected: FAIL with `AttributeError: module 'dispatch' has no attribute 'Config'`.

- [ ] **Step 3: Append to `scripts/dispatch.py`**

```python
# ------------------------------------------------------------------ the service

@dataclass
class Config:
    table_path: str
    a2a_base: str
    n8n_base: str
    user: str
    timeout: float
    ui_url: str

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        return cls(
            table_path=env.get("DISPATCH_TABLE", "/etc/dispatch/dispatch.json"),
            a2a_base=env.get("KAGENT_A2A_BASE", a2a.IN_CLUSTER_BASE),
            n8n_base=env.get("N8N_WEBHOOK_BASE", "http://n8n.n8n.svc.cluster.local:5678/webhook"),
            user=env.get("DISPATCH_USER", "dispatcher"),
            timeout=float(env.get("INVESTIGATION_TIMEOUT", "300")),
            ui_url=env.get("KAGENT_UI_SESSION_URL", "https://kagent.arigsela.com/agents/{session}/chat"),
        )


def log_line(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True), flush=True)


def post_json(url: str, obj: dict, timeout: float = 10.0) -> None:
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


class Dispatcher:
    def __init__(self, table: dict, config: Config, *, budget: Budget | None = None,
                 send=a2a.send_message, post=post_json, log=log_line, run_async: bool = True):
        self.table = table
        self.config = config
        limits = table["limits"]
        self.budget = budget or Budget(limits["investigations_per_hour"], limits["repeat_after_hours"])
        self.send, self.post, self.log, self.run_async = send, post, log, run_async

    # One decision per alert; investigations start here (in a thread when serving HTTP).
    def handle_grafana(self, payload: dict) -> list[dict]:
        decisions = []
        for alert in payload.get("alerts") or []:
            if not isinstance(alert, dict):
                continue
            decision = self.decide(alert)
            decisions.append(decision)
            self.log(decision)
            if decision["decision"] == "investigate":
                rule = next(r for r in self.table["rules"] if r["id"] == decision["rule"])
                if self.run_async:
                    threading.Thread(target=self.investigate, args=(alert, rule), daemon=True).start()
                else:
                    self.investigate(alert, rule)
        return decisions

    def decide(self, alert: dict) -> dict:
        labels = alert.get("labels") or {}
        name = labels.get("alertname") or "?"
        fingerprint = alert.get("fingerprint") or name
        base = {"event": "dispatch", "source": "grafana", "alertname": name,
                "fingerprint": fingerprint, "status": alert.get("status"), "rule": None, "route": "none"}
        rule = match_rule(self.table, "grafana", labels)
        if rule is None:
            return {**base, "decision": "unmatched"}
        base.update(rule=rule["id"], route=rule["route"])
        if rule["route"] == "none":
            return {**base, "decision": "none"}
        if alert.get("status") != "firing":
            return {**base, "decision": "resolved"}
        skipped = self.budget.admit(fingerprint)
        if skipped:
            return {**base, "decision": f"skip-{skipped}"}
        return {**base, "decision": "investigate"}

    def investigate(self, alert: dict, rule: dict) -> dict:
        labels = alert.get("labels") or {}
        agent = rule["route"].split(":", 1)[1]
        url = a2a.resolve_endpoint(self.config.a2a_base, f"kagent/{agent}")
        finding = {"rule": rule["id"], "alertname": labels.get("alertname") or "?", "labels": labels,
                   "agent": agent, "session": None, "state": None, "text": None, "ui": None, "error": None}
        try:
            # A new session every time, as the dispatcher: no context_id, no task_id, no metadata (V88).
            reply = a2a.parse_result(self.send(url, render_prompt(rule, alert), user=self.config.user,
                                               timeout=self.config.timeout))
            finding["session"], finding["state"] = reply.context_id, reply.state
            if reply.context_id:
                finding["ui"] = self.config.ui_url.format(session=reply.context_id)
            if reply.completed:
                finding["text"] = (reply.answer or "")[:ANSWER_LIMIT]
            elif reply.waiting:
                finding["text"] = ("The agent paused for input (an approval or a question). "
                                   "The dispatcher never answers; continue it in the kagent UI.")
            else:
                finding["error"] = f"task ended in {reply.state}: {(reply.status_text or '')[:300]}"
        except (a2a.A2AError, OSError, ValueError) as err:
            finding["error"] = str(err)[:500]
        try:
            self.post(self.config.n8n_base.rstrip("/") + "/agent-findings", finding)
        except (OSError, ValueError) as err:
            finding["error"] = (finding["error"] or "") + f" | post failed: {str(err)[:200]}"
        self.log({"event": "finding", "rule": rule["id"], "alertname": finding["alertname"],
                  "agent": agent, "session": finding["session"], "state": finding["state"],
                  "error": finding["error"]})
        return finding
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/dispatch/test_dispatch.py -q`
Expected: `20 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/dispatch.py tests/dispatch/test_dispatch.py
git commit -m "feat(dispatcher): decide, investigate as X-User-Id dispatcher, post the finding"
```

### Task A4: The HTTP server

**Files:**
- Modify: `scripts/dispatch.py` (append)
- Modify: `tests/dispatch/test_dispatch.py` (append)

**Interfaces:**
- Produces: `make_handler(dispatcher) -> type[BaseHTTPRequestHandler]`, `serve(dispatcher, port) -> ThreadingHTTPServer` (started in a daemon thread, returned so tests can stop it), `main(argv=None) -> int`.

- [ ] **Step 1: Write the failing tests (append)**

```python
# ---------------------------------------------------------------- http

@pytest.fixture
def server():
    fake = Fake(reply=completed_task())
    d = make(fake)
    srv = dp.serve(d, port=0)
    yield fake, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _post(url, body: bytes):
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def test_healthz_and_readyz(server):
    _, base = server
    for path in ("/healthz", "/readyz"):
        with urllib.request.urlopen(base + path, timeout=5) as resp:
            assert resp.status == 200


def test_signal_grafana_returns_decisions(server):
    fake, base = server
    status, body = _post(base + "/signal/grafana", json.dumps({"alerts": [alert()]}).encode())
    assert status == 200
    assert body == {"decisions": ["investigate"]}


def test_signal_grafana_rejects_bad_json_and_unknown_paths(server):
    _, base = server
    assert _post(base + "/signal/grafana", b"not json")[0] == 400
    assert _post(base + "/signal/other", b"{}")[0] == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/dispatch/test_dispatch.py -q -k "healthz or signal"`
Expected: FAIL with `AttributeError: module 'dispatch' has no attribute 'serve'`.

- [ ] **Step 3: Append to `scripts/dispatch.py`**

```python
# ------------------------------------------------------------------ http

def make_handler(dispatcher: Dispatcher):
    class Handler(BaseHTTPRequestHandler):
        server_version = "dispatcher/1"

        def _reply(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path in ("/healthz", "/readyz"):
                self._reply(200, {"ok": True})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path != "/signal/grafana":
                self._reply(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("payload must be an object")
            except ValueError as err:
                self._reply(400, {"error": f"bad json: {err}"})
                return
            decisions = dispatcher.handle_grafana(payload)
            self._reply(200, {"decisions": [d["decision"] for d in decisions]})

        def log_message(self, fmt, *args):  # access log as JSON, like everything else
            log_line({"event": "http", "path": self.path, "message": fmt % args})

    return Handler


def serve(dispatcher: Dispatcher, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(dispatcher))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main(argv=None) -> int:
    config = Config.from_env()
    try:
        table = load_table(Path(config.table_path).read_text())
    except (OSError, ValueError) as err:
        log_line({"event": "fatal", "error": f"cannot load {config.table_path}: {err}"})
        return 1
    dispatcher = Dispatcher(table, config)
    port = int(os.environ.get("PORT", "8080"))
    server = serve(dispatcher, port)
    log_line({"event": "start", "port": port, "rules": len(table["rules"]),
              "a2a_base": config.a2a_base, "user": config.user})
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest tests/dispatch/ -q`
Expected: `28 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/dispatch.py tests/dispatch/test_dispatch.py
git commit -m "feat(dispatcher): HTTP endpoints for the Grafana webhook and probes"
```

### Task A5: The generator that ships the script in a ConfigMap

**Files:**
- Create: `scripts/gen-dispatcher-configmap.py`
- Create: `base-apps/dispatcher/code-configmap.yaml` (generated)
- Create: `tests/dispatch/test_gen_configmap.py`

**Interfaces:**
- Produces: ConfigMap `dispatcher-code` in namespace `dispatcher` with keys `dispatch.py` and `kagent_a2a.py`; `scripts/gen-dispatcher-configmap.py [--check] [--repo-root .]` exits 1 on drift.

- [ ] **Step 1: Write the failing test**

```python
# tests/dispatch/test_gen_configmap.py
"""base-apps/dispatcher/code-configmap.yaml is generated from the two scripts; CI fails on drift."""
from pathlib import Path
import subprocess
import sys

import yaml

REPO = Path(__file__).resolve().parents[2]
GEN = REPO / "scripts" / "gen-dispatcher-configmap.py"
OUT = REPO / "base-apps" / "dispatcher" / "code-configmap.yaml"


def test_generated_configmap_is_in_sync():
    result = subprocess.run([sys.executable, str(GEN), "--check", "--repo-root", str(REPO)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_configmap_carries_both_scripts_verbatim():
    doc = yaml.safe_load(OUT.read_text())
    assert doc["metadata"] == {"name": "dispatcher-code", "namespace": "dispatcher", "labels": {"app": "dispatcher"}}
    assert doc["data"]["dispatch.py"] == (REPO / "scripts" / "dispatch.py").read_text()
    assert doc["data"]["kagent_a2a.py"] == (REPO / "scripts" / "kagent_a2a.py").read_text()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/dispatch/test_gen_configmap.py -q`
Expected: FAIL (`FileNotFoundError` for the generator).

- [ ] **Step 3: Write the generator**

```python
#!/usr/bin/env python3
"""Generate base-apps/dispatcher/code-configmap.yaml from the real source files.

    ./scripts/gen-dispatcher-configmap.py            # regenerate
    ./scripts/gen-dispatcher-configmap.py --check    # CI: fail if stale

Same pattern and reasoning as gen-agent-audit-cronjob.py: the dispatcher has to run
scripts/dispatch.py in the cluster; an image build per edit is a drift trap, a hand-copied
ConfigMap is a worse one. So the ConfigMap is generated from the one tested file (and the
A2A client it imports), and CI fails if the committed manifest falls behind.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

import yaml

SOURCES = {"dispatch.py": "scripts/dispatch.py", "kagent_a2a.py": "scripts/kagent_a2a.py"}
OUT = "base-apps/dispatcher/code-configmap.yaml"

HEADER = """\
---
# Dispatcher code (ADP path 1, dispatch work).
#
# !!! GENERATED FILE — DO NOT EDIT BY HAND !!!
# Sources:    scripts/dispatch.py
#             scripts/kagent_a2a.py
# Regenerate: ./scripts/gen-dispatcher-configmap.py
# CI fails if this file drifts from either source (tests/dispatch/test_gen_configmap.py).
#
# The Deployment (deployment.yaml) mounts this at /opt/dispatch and runs dispatch.py with
# python:3.12-slim and no pip install: both files are standard-library only.
"""


class _Literal(str):
    pass


def _literal(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.add_representer(_Literal, _literal)


def render(repo: Path) -> str:
    data = {key: _Literal((repo / path).read_text()) for key, path in SOURCES.items()}
    doc = {"apiVersion": "v1", "kind": "ConfigMap",
           "metadata": {"name": "dispatcher-code", "namespace": "dispatcher", "labels": {"app": "dispatcher"}},
           "data": data}
    return HEADER + yaml.dump(doc, sort_keys=False, width=10_000, allow_unicode=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed manifest is stale")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)
    repo = Path(args.repo_root).resolve()
    want = render(repo)
    out = repo / OUT
    if args.check:
        have = out.read_text() if out.exists() else ""
        if have != want:
            sys.stdout.writelines(difflib.unified_diff(have.splitlines(True), want.splitlines(True),
                                                       fromfile=OUT, tofile=OUT + " (regenerated)"))
            print(f"{OUT} is stale; run scripts/gen-dispatcher-configmap.py", file=sys.stderr)
            return 1
        print(f"{OUT} in sync")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(want)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Generate, then run the tests**

Run: `python scripts/gen-dispatcher-configmap.py --repo-root . && python -m pytest tests/dispatch/test_gen_configmap.py -q`
Expected: `wrote base-apps/dispatcher/code-configmap.yaml`, then `2 passed`.

- [ ] **Step 5: Add the generator to `AGENTS.md`**

In the "Generated files" table add the row:

```markdown
| `scripts/gen-dispatcher-configmap.py --repo-root .` | `base-apps/dispatcher/code-configmap.yaml` | `scripts/dispatch.py`, `scripts/kagent_a2a.py` |
```

- [ ] **Step 6: Commit**

```bash
git add scripts/gen-dispatcher-configmap.py base-apps/dispatcher/code-configmap.yaml tests/dispatch/test_gen_configmap.py AGENTS.md
git commit -m "feat(dispatcher): ship the script in a generated ConfigMap, drift-checked"
```

### Task A6: The app: Application, Deployment, Service, docs contract

**Files:**
- Create: `base-apps/dispatcher.yaml`, `base-apps/dispatcher/namespace.yaml`, `base-apps/dispatcher/deployment.yaml`, `base-apps/dispatcher/service.yaml`, `base-apps/dispatcher/docs.md`, `base-apps/dispatcher/runbook.md`, `base-apps/dispatcher/catalog-info.yaml`
- Modify: `scripts/agent-docs-scope.txt` (append `dispatcher`)
- Generated: `base-apps/dispatcher/mkdocs.yml`, `base-apps/index.md`, `docs/index.md`, `docs/runbook.md`

- [ ] **Step 1: Write the Application**

```yaml
# base-apps/dispatcher.yaml
---
# Signal dispatcher: routes Grafana alerts to a read-only agent investigation by the table in
# base-apps/dispatcher/dispatch-table.yaml (ADP path 1). Design:
# docs/superpowers/specs/2026-10-12-signal-dispatch-and-promotion-design.md. §T.105, §V.86-§V.88.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  name: dispatcher
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/dispatcher
    directory:
      # The agent-docs contract: catalog-info.yaml and the generated mkdocs.yml are not manifests.
      exclude: '{catalog-info.yaml,mkdocs.yml}'
  destination:
    server: https://kubernetes.default.svc
    namespace: dispatcher
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [ ] **Step 2: Write the namespace, Deployment and Service**

```yaml
# base-apps/dispatcher/namespace.yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: dispatcher
  labels:
    app.kubernetes.io/part-of: agent-guardrails
```

```yaml
# base-apps/dispatcher/deployment.yaml
---
# One replica: the budget and the repeat window are in memory (scripts/dispatch.py, Budget).
# A second replica would double the budget; a restart forgets it, which errs toward looking.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dispatcher
  namespace: dispatcher
  labels:
    app: dispatcher
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: dispatcher
  template:
    metadata:
      labels:
        app: dispatcher
    spec:
      automountServiceAccountToken: false   # it talks to kagent and n8n over HTTP, never to the API server
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: dispatcher
          # Renovate pins the digest on its next run (kubernetes manager, pinDigests for base-apps).
          image: python:3.12-slim
          command: ["python", "-u", "/opt/dispatch/dispatch.py"]
          env:
            - name: DISPATCH_TABLE
              value: /etc/dispatch/dispatch.json
            - name: KAGENT_A2A_BASE
              value: http://kagent-controller.kagent.svc.cluster.local:8083
            - name: N8N_WEBHOOK_BASE
              value: http://n8n.n8n.svc.cluster.local:5678/webhook
            - name: DISPATCH_USER
              value: dispatcher
            - name: INVESTIGATION_TIMEOUT
              value: "300"
            - name: PORT
              value: "8080"
          ports:
            - name: http
              containerPort: 8080
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 30
          resources:
            requests:
              cpu: 20m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: code
              mountPath: /opt/dispatch
              readOnly: true
            - name: table
              mountPath: /etc/dispatch
              readOnly: true
      volumes:
        - name: code
          configMap:
            name: dispatcher-code
        - name: table
          configMap:
            name: dispatcher-table
```

```yaml
# base-apps/dispatcher/service.yaml
---
apiVersion: v1
kind: Service
metadata:
  name: dispatcher
  namespace: dispatcher
  labels:
    app: dispatcher
spec:
  selector:
    app: dispatcher
  ports:
    - name: http
      port: 8080
      targetPort: http
```

- [ ] **Step 3: Write the docs contract**

```markdown
# base-apps/dispatcher/docs.md
---
type: "Kubernetes App Guide"
title: "Dispatcher"
description: "Routes Grafana alerts to a read-only agent investigation by a declared table; humans keep every alert (ADP path 1, dispatch work)"
app: dispatcher
catalog_entity: dispatcher
kind: docs
namespace: dispatcher
last_reviewed: 2026-10-12
status: stable
tags: [agents, dispatch, grafana, a2a]
sources:
  - base-apps/dispatcher.yaml
  - base-apps/dispatcher/dispatch-table.yaml
  - base-apps/dispatcher/deployment.yaml
  - base-apps/dispatcher/service.yaml
  - base-apps/dispatcher/code-configmap.yaml
  - scripts/dispatch.py
  - scripts/gen-dispatcher-configmap.py
  - base-apps/logging/grafana-alerting.yaml
  - base-apps/n8n/workflows-configmap.yaml
  - docs/superpowers/specs/2026-10-12-signal-dispatch-and-promotion-design.md
---

# Dispatcher

## What it is
One Python process (`scripts/dispatch.py`, standard library only, shipped in the generated
ConfigMap `dispatcher-code`) that receives a second copy of every Grafana alert notification,
matches it against the table in `dispatch-table.yaml`, and for an `agent:<name>` route opens a
new A2A session with that agent as `X-User-Id: dispatcher`, then posts the answer to n8n's
`agent-findings` webhook, which formats it for Slack `#oncall-alerts`.

## What it is not
- Not in the human path. Grafana's contact point `n8n` has two integrations: n8n (→ Slack,
  unchanged) and this service. If the dispatcher is down, Slack is unaffected (§V.86).
- Not an operator. It may only route to agents whose every tool is `read` in the capability
  taxonomy with no `requireApproval` binding; `tests/dispatch/test_table.py` enforces it
  (§V.87). It never answers an approval or a question (§V.88).
- Not a second source of truth for Slack: n8n holds the Slack token; this service holds no
  credential at all.

## The table
`dispatch-table.yaml` is a ConfigMap whose `dispatch.json` key is the table: `limits`
(`investigations_per_hour`, `repeat_after_hours`) and ordered `rules` (`id`, `source`, `match`
on alert labels, `route`, `prompt` for agent routes, `why`). Every Grafana rule title must
have a row; `none` with a `why` is a decision. Only `firing` alerts are routed.

## Identity and record
Sessions appear in the kagent UI under `dispatcher`; the daily audit attributes their tool
calls; `scripts/ask-hk --user dispatcher --context <id>` continues one by hand.

## Observability
One JSON line per decision (`event: dispatch`, `decision` ∈ investigate / none / resolved /
unmatched / skip-budget / skip-repeat) and per finding (`event: finding`, with the session id,
never the answer text). Loki: `{namespace="dispatcher"} | json | event="dispatch"`.
```

```markdown
# base-apps/dispatcher/runbook.md
---
type: "Kubernetes App Runbook"
title: "Dispatcher"
description: "Runbook: no investigation after an alert, a wrong route, the budget, replaying a signal"
app: dispatcher
catalog_entity: dispatcher
kind: runbook
namespace: dispatcher
last_reviewed: 2026-10-12
status: stable
tags: [agents, dispatch, grafana, a2a]
sources:
  - base-apps/dispatcher/dispatch-table.yaml
  - base-apps/dispatcher/deployment.yaml
  - scripts/dispatch.py
---

# Dispatcher runbook

## An alert fired in Slack but no investigation followed
1. Was it routed? `kubectl logs -n dispatcher deploy/dispatcher | grep '"event": "dispatch"'`
   and read `decision`: `none` or `unmatched` means the table said so (add or change a row);
   `skip-budget` / `skip-repeat` means the limits held; `resolved` means the notification was
   the all-clear.
2. Did the investigation finish? `grep '"event": "finding"'`: `error` names an A2A failure
   or a post failure; `session` is the kagent session to open in the UI
   (`https://kagent.arigsela.com/agents/<session>/chat`).
3. Is Grafana delivering at all? Grafana → Alerting → Contact points → `n8n` → the second
   integration's last delivery. A 404 there means the Service or the pod is gone.

## The agent paused instead of answering
The finding says "paused". The dispatcher never answers (§V.88): open the session in the UI
and decide there. If a read-only agent pauses, something bound a gated tool to it;
`tests/dispatch/test_table.py` would have failed, so check what changed.

## Replay a signal by hand (also the acceptance test)
From inside the cluster, post a synthetic payload for a real pod:
```
kubectl run -n dispatcher curl --rm -i --restart=Never --image=curlimages/curl:8.11.1 -- \
  curl -s -X POST http://dispatcher.dispatcher.svc.cluster.local:8080/signal/grafana \
  -H 'Content-Type: application/json' \
  -d '{"alerts":[{"status":"firing","fingerprint":"manual-1","labels":{"alertname":"Pod CPU starved","namespace":"n8n","pod":"<a real pod>"},"annotations":{"summary":"manual replay","runbook":"none"}}]}'
```
Expect `{"decisions":["investigate"]}`, then a Slack message within five minutes and a
session under `dispatcher` in the UI. Use a new `fingerprint` each time, or wait
`repeat_after_hours`.

## Change the table
Edit `dispatch-table.yaml`, run `python -m pytest tests/dispatch/ -q`, open a PR. The pod
reads the file at start: Argo CD's sync updates the ConfigMap and kubelet refreshes the
mount within a minute, but the process keeps its loaded copy; `kubectl rollout restart
deploy/dispatcher -n dispatcher` after the sync, or wait for the next Deployment change.
```

```yaml
# base-apps/dispatcher/catalog-info.yaml
# Structured facts for the dispatcher. Authoritative for owner, dependencies, namespace and
# lifecycle; the markdown docs do not restate them.
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: dispatcher
  namespace: dispatcher
  description: Routes Grafana alerts to a read-only agent investigation by a declared table; humans keep every alert
  annotations:
    agent-docs/path: docs.md
    backstage.io/techdocs-ref: dir:.
    backstage.io/kubernetes-label-selector: 'app=dispatcher'
    backstage.io/kubernetes-namespace: dispatcher
    github.com/project-slug: arigsela/kubernetes
  tags: [agents, dispatch, grafana]
spec:
  type: service
  lifecycle: production
  owner: group:default/platform
  system: default/platform-observability
  dependsOn:
    - component:kagent/kagent
    - component:n8n/n8n
    - component:logging/grafana
```

Check the three `dependsOn` targets exist with `python scripts/validate-catalog-refs.py --repo-root .`; use the entity names it reports if they differ (the validator fails on a dangling ref).

- [ ] **Step 4: Enter scope, regenerate, validate**

```bash
echo dispatcher >> scripts/agent-docs-scope.txt
python scripts/gen-techdocs.py --repo-root .
python scripts/gen-okf.py --repo-root .
python scripts/validate-agent-docs.py --repo-root .
python scripts/validate-catalog-refs.py --repo-root .
python -m pytest tests/agent-docs/ tests/techdocs/ tests/okf/ tests/catalog-refs/ -q
yamllint -c .yamllint.yaml base-apps/dispatcher.yaml base-apps/dispatcher/
kubeconform -summary -strict -ignore-missing-schemas -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  -kubernetes-version 1.33.0 base-apps/dispatcher.yaml base-apps/dispatcher/namespace.yaml \
  base-apps/dispatcher/deployment.yaml base-apps/dispatcher/service.yaml \
  base-apps/dispatcher/dispatch-table.yaml base-apps/dispatcher/code-configmap.yaml
```
Expected: validators pass, `base-apps/index.md` gains a dispatcher row, no kubeconform errors.

- [ ] **Step 5: Commit**

```bash
git add base-apps/dispatcher.yaml base-apps/dispatcher/ base-apps/index.md docs/index.md docs/runbook.md scripts/agent-docs-scope.txt
git commit -m "feat(dispatcher): the app, in agent-docs scope"
```

### Task A7: Grafana's second integration and the n8n `agent-findings` workflow

**Files:**
- Modify: `base-apps/logging/grafana-alerting.yaml` (contact point), `base-apps/logging/grafana-deployment.yaml:90` (checksum)
- Modify: `base-apps/n8n/workflows-configmap.yaml`, `base-apps/n8n/deployments.yaml:44-60`, `base-apps/n8n/docs.md`

- [ ] **Step 1: Add the second integration to the contact point**

In `grafana-alerting.yaml`, `contactpoints.yaml`, replace the `receivers:` block with:

```yaml
        receivers:
          - uid: n8n-webhook
            type: webhook
            settings:
              # In-cluster: no ingress, no TLS, no auth to manage. n8n decides where
              # this actually goes.
              url: http://n8n.n8n.svc.cluster.local:5678/webhook/grafana-alerts
              httpMethod: POST
            disableResolveMessage: false
          # Second copy to the dispatcher (base-apps/dispatcher/), which may add an agent
          # investigation by its table. A separate integration on purpose (§V.86): Grafana
          # delivers to both, so the dispatcher being down never costs a Slack alert.
          - uid: dispatcher-webhook
            type: webhook
            settings:
              url: http://dispatcher.dispatcher.svc.cluster.local:8080/signal/grafana
              httpMethod: POST
            disableResolveMessage: false
```

- [ ] **Step 2: Recompute the checksum annotation**

```bash
python - <<'EOF'
import hashlib, json, yaml
docs = [d for d in yaml.safe_load_all(open("base-apps/logging/grafana-alerting.yaml")) if d and d.get("kind") == "ConfigMap"]
print(hashlib.sha256(json.dumps(docs[0]["data"], sort_keys=True).encode()).hexdigest())
EOF
```
Put the printed value in `base-apps/logging/grafana-deployment.yaml` at `checksum/alerting:` (line 90).

Run: `python -m pytest tests/dashboards/ -q`
Expected: all pass (the checksum test is the one that would fail).

- [ ] **Step 3: Add the `agent-findings` workflow to n8n**

In `base-apps/n8n/workflows-configmap.yaml`, add a `data` key `agent-findings.json` with this JSON (single-quoted strings inside `jsCode` are deliberate; keep the JSON valid):

```json
{
  "id": "agent-findings",
  "name": "Agent Findings to Slack",
  "active": true,
  "settings": {"executionOrder": "v1"},
  "nodes": [
    {"id": "wh", "name": "Findings Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
     "position": [0, 0],
     "parameters": {"httpMethod": "POST", "path": "agent-findings", "responseMode": "onReceived"}},
    {"id": "fmt", "name": "Format Finding", "type": "n8n-nodes-base.code", "typeVersion": 2,
     "position": [220, 0],
     "parameters": {"jsCode": "const p = $json.body ?? $json;\nconst esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');\nconst l = p.labels || {};\nconst where = [l.namespace, l.pod].filter(Boolean).join('/') + (l.container ? ' (' + l.container + ')' : '');\nconst head = (p.error ? ':x: ' : ':mag: ') + '*Investigation: ' + esc(p.alertname) + '*' + (where ? ' ' + esc(where) : '') + ' by `' + esc(p.agent) + '`';\nconst body = p.error ? 'Failed: ' + esc(p.error) : esc((p.text || '').slice(0, 3000));\nconst link = p.ui ? '\\nSession: ' + p.ui : '';\nreturn [{ json: { slack: { channel: '#oncall-alerts', text: head + '\\n' + body + link, unfurl_links: false } } }];"}},
    {"id": "slack", "name": "Post to Slack", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
     "position": [440, 0],
     "parameters": {"method": "POST", "url": "https://slack.com/api/chat.postMessage",
       "sendHeaders": true,
       "headerParameters": {"parameters": [
         {"name": "Authorization", "value": "=Bearer {{ $env.SLACK_BOT_TOKEN }}"},
         {"name": "Content-Type", "value": "application/json"}]},
       "sendBody": true, "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json.slack) }}", "options": {}}}
  ],
  "connections": {
    "Findings Webhook": {"main": [[{"node": "Format Finding", "type": "main", "index": 0}]]},
    "Format Finding": {"main": [[{"node": "Post to Slack", "type": "main", "index": 0}]]}
  }
}
```

Copy the `typeVersion` and `position` conventions from the existing `grafana-alerts.json` entry if they differ.

In `base-apps/n8n/deployments.yaml`, after the `newsletter-digest-send` lines of the `import-workflows` initContainer, add:

```yaml
          n8n import:workflow --input=/workflows/agent-findings.json
          n8n update:workflow --id=agent-findings --active=true
```

In `base-apps/n8n/docs.md`, "GitOps-managed workflows", add the bullet:

```markdown
- **Agent Findings to Slack** (`agent-findings.json`, id `agent-findings`) — the `POST /webhook/agent-findings` endpoint the dispatcher (`base-apps/dispatcher/`) posts an agent's investigation to; formats it for `#oncall-alerts` with the kagent session link. In-cluster caller only.
```

- [ ] **Step 4: Validate**

```bash
python - <<'EOF'
import json, yaml
cm = yaml.safe_load(open("base-apps/n8n/workflows-configmap.yaml"))
wf = json.loads(cm["data"]["agent-findings.json"]); print(wf["id"], [n["name"] for n in wf["nodes"]])
EOF
yamllint -c .yamllint.yaml base-apps/n8n/workflows-configmap.yaml base-apps/n8n/deployments.yaml base-apps/logging/grafana-alerting.yaml base-apps/logging/grafana-deployment.yaml
python scripts/gen-techdocs.py --repo-root . && python -m pytest tests/dashboards/ tests/techdocs/ -q
```

- [ ] **Step 5: Commit**

```bash
git add base-apps/logging/grafana-alerting.yaml base-apps/logging/grafana-deployment.yaml base-apps/n8n/ 
git commit -m "feat(dispatcher): Grafana's second integration and the agent-findings Slack workflow"
```

### Task A8: CI job and the ledger

**Files:**
- Modify: `.github/workflows/validate.yaml` (new job after `agent-audit-validate`)
- Modify: `SPEC.md` (§V rows V86–V88, §T row T105)

- [ ] **Step 1: Add the job**

```yaml
  # The dispatch table must stay complete over the Grafana rules and route only to read-only
  # agents (§V.87); the dispatcher's ConfigMap must match the tested script. See
  # tests/dispatch/ and scripts/gen-dispatcher-configmap.py.
  dispatch-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
      - name: Set up Python
        uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install pyyaml==6.0.2 pytest==8.3.3
      - name: Run dispatcher tests
        run: python -m pytest tests/dispatch/ -q
      - name: Check the dispatcher ConfigMap is in sync with the scripts
        run: python scripts/gen-dispatcher-configmap.py --check --repo-root .
```

Keep the `actions/checkout` and `setup-python` SHAs identical to the neighbouring jobs (Renovate pins them together).

- [ ] **Step 2: Ledger rows**

Append to `## §V INVARIANTS`:

```
V86: ∀ Grafana alert → n8n (Slack) via its own contact-point integration; dispatcher ∉ that path & ⊥ forwards to grafana-alerts. dispatcher down ⇒ ⊥ lost Slack alert.
V87: dispatch table routes only → AgentTemplate whose ∀ bound tool ∈ taxonomy `read` & ⊥ `requireApproval: true` binding; ∀ Grafana rule title has a row. CI: tests/dispatch/test_table.py.
V88: dispatcher sends A2A as `X-User-Id: dispatcher`, new session per investigation, ⊥ `tool_approval_response` / `ask_user_response` ever.
```

Append to `## §T TASKS` (before the §B header):

```
T105|.|dispatcher (ADP path 1): `base-apps/dispatcher/` + `scripts/dispatch.py` route Grafana alerts → read-only agent investigation by table; findings → n8n `agent-findings` → Slack. spec `docs/superpowers/specs/2026-10-12-signal-dispatch-and-promotion-design.md`|V86,V87,V88
```

Add `§T.105` to the goal line's `open:` list.

- [ ] **Step 3: Validate the workflow file and commit**

```bash
yamllint -c .yamllint.yaml .github/workflows/validate.yaml
git add .github/workflows/validate.yaml SPEC.md
git commit -m "ci(dispatcher): dispatch-validate job; ledger V86-V88, T105"
```

### Task A9: Open the PR, merge, accept live

- [ ] **Step 1: Push and open the PR**

```bash
git push -u origin feat/dispatcher
gh pr create --base main --title "feat(dispatcher): route Grafana alerts to a read-only agent investigation by table" --body-file - <<'EOF'
## What
ADP path 1 (dispatch work) and the probabilistic half of path 7 (observe). A new in-scope app `dispatcher` receives a second copy of every Grafana alert and, by the table in `base-apps/dispatcher/dispatch-table.yaml`, opens a read-only agent investigation as `X-User-Id: dispatcher`, posting the answer to Slack through a new n8n workflow. Humans keep every alert on the existing integration (§V.86). Two rows route to `k8s-reader` (CPU starved, near memory limit); four are explicit `none` with reasons. Design: `docs/superpowers/specs/2026-10-12-signal-dispatch-and-promotion-design.md`.

## Validation
- `tests/dispatch/` (table contract, unit, HTTP, generator drift), `tests/dashboards/`, `tests/techdocs/`, `tests/okf/`, `tests/agent-docs/`, `tests/catalog-refs/`: pass
- `validate-agent-docs`, `validate-catalog-refs`, `gen-techdocs --check`, `gen-okf --check`, `gen-dispatcher-configmap --check`: clean
- yamllint + kubeconform on the new manifests

## Impact
Deployment: new namespace `dispatcher`, one pod. Grafana restarts (checksum) and n8n restarts (new workflow). No DNS, IAM or secret changes; the dispatcher holds no credential.
EOF
```

- [ ] **Step 2: After the owner merges, accept live**

Wait for Argo CD: `kubectl get app -n argo-cd dispatcher n8n logging` all Synced/Healthy. Then run the "Replay a signal by hand" command from the runbook with a real pod name (for example the n8n pod). Confirm, in order:
1. the dispatcher log has `"decision": "investigate"` then an `event: finding` with a session id;
2. a `:mag: Investigation: Pod CPU starved` message in `#oncall-alerts` with a session link;
3. the kagent UI lists the session under `dispatcher`, and `scripts/ask-hk --user dispatcher --agent kagent/k8s-reader --context <id> "thanks, anything else?"` continues it;
4. the next day's audit summary attributes the session's read calls (`scripts/agent-audit.py --agent k8s_reader --since 1d`).

Record the Slack message and the log lines as the recorded screen for the whole-picture deck's path 1, and flip T105 to `x` with the date.

---

## Track B — promotion policy

### Task B1: The policy file and its loader

**Files:**
- Create: `.github/promotion-policy.yaml`
- Create: `scripts/promote.py` (loader only)
- Create: `tests/promotion/test_promote.py` (first tests)

**Interfaces:**
- Produces: `load_policy(text: str) -> dict` (raises `PolicyError`), `class PolicyError(ValueError)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/promotion/test_promote.py
"""scripts/promote.py: the one class of PR that merges without a human, decided from the base commit."""
from pathlib import Path
import importlib.util
import json
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("promote", REPO / "scripts" / "promote.py")
pm = importlib.util.module_from_spec(_spec)
sys.modules["promote"] = pm   # dataclasses resolve the module by name at class creation
_spec.loader.exec_module(pm)

POLICY_FILE = REPO / ".github" / "promotion-policy.yaml"


def test_repo_policy_loads_and_denies_the_dangerous_prefixes():
    policy = pm.load_policy(POLICY_FILE.read_text())
    assert policy["version"] == 1
    assert policy["auto"]["authors"] == ["arigsela-renovate[bot]"]
    assert policy["auto"]["require_labels"] == ["review:skip"]
    for must in ("terraform/", ".github/", "appsets/", "base-apps/vault", "base-apps/kagent", "base-apps/admission-policies"):
        assert must in policy["auto"]["deny_prefixes"], must
    assert policy["auto"]["merge_method"] == "rebase"
    assert policy["auto"]["max_changed_files"] <= 4


def policy_text(**auto):
    """A block-style policy (the only YAML shape promote.py's parser accepts) with overrides."""
    fields = {"authors": "[b]", "require_labels": "[x]", "deny_prefixes": "[a]",
              "max_changed_files": "1", "merge_method": "rebase"}
    fields.update(auto)
    return "version: 1\nauto:\n" + "".join(f"  {k}: {v}\n" for k, v in fields.items())


@pytest.mark.parametrize("text, message", [
    ("version: 2\nauto:\n  authors: [b]\n", "version"),
    (policy_text(authors="[]"), "authors"),
    (policy_text(require_labels="[]"), "require_labels"),
    (policy_text(merge_method="squash"), "merge_method"),
    (policy_text(max_changed_files="0"), "max_changed_files"),
])
def test_load_policy_rejects_bad_policies(text, message):
    with pytest.raises(pm.PolicyError, match=message):
        pm.load_policy(text)


def test_parser_reads_block_lists_and_ignores_comments():
    text = "version: 1  # the schema\nauto:\n  authors: [b]\n  require_labels: [x]\n  deny_prefixes:\n    - a/   # first\n    - b\n  max_changed_files: 1\n  merge_method: rebase\nowner:\n  approvers: [me]\n"
    policy = pm.load_policy(text)
    assert policy["auto"]["deny_prefixes"] == ["a/", "b"]
    assert policy["owner"]["approvers"] == ["me"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/promotion/test_promote.py -q`
Expected: FAIL (`FileNotFoundError` for `scripts/promote.py`).

- [ ] **Step 3: Write the policy file**

```yaml
# .github/promotion-policy.yaml
# Promotion policy (ADP path 5): the one class of pull request that merges WITHOUT a human.
# Read by scripts/promote.py at the PR's BASE commit, like review-policy.yaml, so a PR cannot
# loosen its own promotion. Spec: docs/superpowers/specs/2026-10-12-signal-dispatch-and-promotion-design.md
# (§V.89, §V.90). Changing this file is itself `review:read` (.github/**) and never `auto`.
version: 1
auto:                                   # merged by .github/workflows/promote.yaml, no human
  authors: ["arigsela-renovate[bot]"]   # the API login; `gh` shows app/arigsela-renovate
  require_labels: ["review:skip"]       # triage found nothing to read: docs-only, or an image-tag bump of <= 4 lines
  deny_prefixes:                        # never automatic, whatever triage said (path starts-with)
    - terraform/                        # applies before merge, via Atlantis
    - .github/                          # the workflows and the policies themselves
    - appsets/
    - ansible/
    - node-config/
    - base-apps/vault                   # covers vault.yaml and vault/
    - base-apps/postgresql
    - base-apps/kagent
    - base-apps/substrate
    - base-apps/admission-policies
    - base-apps/istio-
    - base-apps/dex
    - base-apps/cluster-rbac
    - base-apps/dispatcher
  max_changed_files: 2
  merge_method: rebase                  # the only method the main ruleset allows
owner:                                  # everything else; the triage label says how closely to read
  approvers: ["arigsela"]
```

- [ ] **Step 4: Write the loader (`scripts/promote.py`, first part)**

```python
#!/usr/bin/env python3
"""Promotion: merge the one declared class of pull request without a human (ADP path 5).

    scripts/promote.py --repo arigsela/kubernetes --pr 123          # decide and, if auto, merge
    scripts/promote.py --repo arigsela/kubernetes --pr 123 --dry-run

Reads .github/promotion-policy.yaml at the PR's BASE commit (a PR cannot loosen its own
promotion), decides `auto` or `owner` with reasons, and for `auto` merges by rebase pinned to
the head SHA it evaluated, then leaves one sticky comment. For `owner` it logs and exits 0:
the triage comment already tells the human what to read. Exit 0 on a decision, 1 on an error.

Token: GITHUB_TOKEN (the promotion GitHub App's installation token in the workflow). The
policy is YAML but this script must run in the workflow with no pip: it parses the small
subset the policy uses (see _parse_policy_yaml). pyyaml is used only in the tests.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API = "https://api.github.com"
COMMENT_MARKER = "<!-- promotion -->"
OK_CONCLUSIONS = {"success", "skipped", "neutral"}


class PolicyError(ValueError):
    pass


# ------------------------------------------------------------------ the policy

def _parse_policy_yaml(text: str) -> dict:
    """A two-level YAML subset: `key:`, `  key: value`, `  key: [a, b]`, `    - item`, `#` comments.
    Enough for promotion-policy.yaml, and nothing else; a richer file must fail loudly."""
    doc: dict = {}
    section: dict | None = None
    list_key: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        if indent == 0:
            key, _, value = body.partition(":")
            value = value.strip()
            if value:
                doc[key] = _scalar(value)
                section = None
            else:
                section = doc.setdefault(key, {})
            list_key = None
        elif body.startswith("- "):
            if section is None or list_key is None:
                raise PolicyError(f"list item outside a list: {raw!r}")
            section[list_key].append(_scalar(body[2:].strip()))
        else:
            if section is None:
                raise PolicyError(f"indented key outside a section: {raw!r}")
            key, _, value = body.partition(":")
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                section[key] = [_scalar(v.strip()) for v in value[1:-1].split(",") if v.strip()]
                list_key = None
            elif value:
                section[key] = _scalar(value)
                list_key = None
            else:
                section[key] = []
                list_key = key
    return doc


def _scalar(value: str):
    if value and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    if value.isdigit():
        return int(value)
    return value


def load_policy(text: str) -> dict:
    policy = _parse_policy_yaml(text)
    if policy.get("version") != 1:
        raise PolicyError("version must be 1")
    auto = policy.get("auto")
    if not isinstance(auto, dict):
        raise PolicyError("auto section is required")
    for key in ("authors", "require_labels", "deny_prefixes"):
        if not isinstance(auto.get(key), list) or not auto[key]:
            raise PolicyError(f"auto.{key} must be a non-empty list")
    if auto.get("merge_method") != "rebase":
        raise PolicyError("auto.merge_method must be rebase (the main ruleset allows nothing else)")
    if not isinstance(auto.get("max_changed_files"), int) or auto["max_changed_files"] < 1:
        raise PolicyError("auto.max_changed_files must be a positive integer")
    policy.setdefault("owner", {})
    return policy
```

- [ ] **Step 5: Run the tests, commit**

Run: `python -m pytest tests/promotion/test_promote.py -q`
Expected: `7 passed`.

```bash
git add .github/promotion-policy.yaml scripts/promote.py tests/promotion/test_promote.py
git commit -m "feat(promotion): the policy file and its loader"
```

### Task B2: The decision

**Files:**
- Modify: `scripts/promote.py` (append)
- Modify: `tests/promotion/test_promote.py` (append)

**Interfaces:**
- Produces: `@dataclass Decision(klass: str, reasons: list[str], head_sha: str)`; `decide(pr: dict, files: list[dict], check_runs: list[dict], policy: dict) -> Decision`. `pr` is the REST pull object (`state`, `draft`, `mergeable`, `user.login`, `labels[].name`, `head.sha`, `head.repo.full_name`, `base.repo.full_name`); `files` the `/files` list (`filename`); `check_runs` the `/check-runs` list (`status`, `conclusion`, `name`).

- [ ] **Step 1: Write the failing tests (append)**

```python
# ---------------------------------------------------------------- decide

def policy():
    return pm.load_policy(POLICY_FILE.read_text())


def pr(**over):
    base = {"number": 760, "state": "open", "draft": False, "mergeable": True, "merged": False,
            "user": {"login": "arigsela-renovate[bot]"}, "labels": [{"name": "renovate"}, {"name": "review:skip"}],
            "head": {"sha": "abc123", "repo": {"full_name": "arigsela/kubernetes"}},
            "base": {"sha": "base456", "repo": {"full_name": "arigsela/kubernetes"}}}
    base.update(over)
    return base


def files(*names):
    return [{"filename": n} for n in names]


def checks(*conclusions, pending=0):
    runs = [{"name": f"job-{i}", "status": "completed", "conclusion": c} for i, c in enumerate(conclusions)]
    runs += [{"name": f"pending-{i}", "status": "in_progress", "conclusion": None} for i in range(pending)]
    return runs


def test_renovate_skip_green_is_auto():
    d = pm.decide(pr(), files("base-apps/donetick/deployment.yaml"), checks("success", "skipped"), policy())
    assert d.klass == "auto" and d.reasons == [] and d.head_sha == "abc123"


def test_deny_prefix_wins_over_skip_label():
    d = pm.decide(pr(), files("base-apps/vault/statefulsets.yaml"), checks("success"), policy())
    assert d.klass == "owner" and any("deny" in r for r in d.reasons)
    d = pm.decide(pr(), files("terraform/roots/asela-cluster/ecr.tf"), checks("success"), policy())
    assert d.klass == "owner"


def test_pending_check_is_owner_not_auto():
    d = pm.decide(pr(), files("base-apps/donetick/deployment.yaml"), checks("success", pending=1), policy())
    assert d.klass == "owner" and "checks pending" in d.reasons


def test_failed_or_absent_checks_are_owner():
    assert pm.decide(pr(), files("x.md"), checks("success", "failure"), policy()).klass == "owner"
    assert pm.decide(pr(), files("x.md"), [], policy()).klass == "owner"


@pytest.mark.parametrize("over, reason", [
    ({"user": {"login": "arigsela"}}, "author"),
    ({"labels": [{"name": "renovate"}, {"name": "review:skim"}]}, "label"),
    ({"draft": True}, "draft"),
    ({"state": "closed"}, "not open"),
    ({"mergeable": False}, "mergeable"),
    ({"head": {"sha": "abc123", "repo": {"full_name": "someone/fork"}}}, "fork"),
])
def test_each_condition_is_a_reason(over, reason):
    d = pm.decide(pr(**over), files("x.md"), checks("success"), policy())
    assert d.klass == "owner" and any(reason in r for r in d.reasons), d.reasons


def test_too_many_files_is_owner():
    d = pm.decide(pr(), files("a.md", "b.md", "c.md"), checks("success"), policy())
    assert d.klass == "owner" and any("files" in r for r in d.reasons)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/promotion/test_promote.py -q -k decide`
Expected: FAIL with `AttributeError: module 'promote' has no attribute 'decide'`.

- [ ] **Step 3: Append the decision**

```python
# ------------------------------------------------------------------ the decision

@dataclass
class Decision:
    klass: str                       # "auto" or "owner"
    reasons: list[str] = field(default_factory=list)
    head_sha: str = ""


def decide(pr: dict, files: list[dict], check_runs: list[dict], policy: dict) -> Decision:
    """`auto` only when every condition holds; otherwise `owner` with every failed one listed."""
    auto = policy["auto"]
    reasons: list[str] = []
    head_sha = ((pr.get("head") or {}).get("sha")) or ""
    if pr.get("state") != "open" or pr.get("merged"):
        reasons.append("not open")
    if pr.get("draft"):
        reasons.append("draft")
    if pr.get("mergeable") is not True:
        reasons.append("GitHub does not report it mergeable")
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    base_repo = ((pr.get("base") or {}).get("repo") or {}).get("full_name")
    if not head_repo or head_repo != base_repo:
        reasons.append("head is a fork")
    author = (pr.get("user") or {}).get("login")
    if author not in auto["authors"]:
        reasons.append(f"author {author!r} is not in auto.authors")
    labels = {lab.get("name") for lab in (pr.get("labels") or [])}
    for need in auto["require_labels"]:
        if need not in labels:
            reasons.append(f"label {need} missing")
    names = [f.get("filename") or "" for f in files]
    if len(names) > auto["max_changed_files"]:
        reasons.append(f"{len(names)} changed files > max {auto['max_changed_files']}")
    for name in names:
        hit = next((p for p in auto["deny_prefixes"] if name.startswith(p)), None)
        if hit:
            reasons.append(f"{name} is under deny prefix {hit}")
    if not check_runs:
        reasons.append("no check runs on the head commit")
    pending = [c.get("name") for c in check_runs if c.get("status") != "completed"]
    if pending:
        reasons.append("checks pending")
    bad = [c.get("name") for c in check_runs
           if c.get("status") == "completed" and c.get("conclusion") not in OK_CONCLUSIONS]
    if bad:
        reasons.append(f"checks not green: {', '.join(sorted(str(b) for b in bad))}")
    return Decision("auto" if not reasons else "owner", reasons, head_sha)
```

- [ ] **Step 4: Run the tests, commit**

Run: `python -m pytest tests/promotion/test_promote.py -q`
Expected: `18 passed`.

```bash
git add scripts/promote.py tests/promotion/test_promote.py
git commit -m "feat(promotion): decide auto or owner with every failed condition as a reason"
```

### Task B3: The GitHub client, the merge, the comment, `main()`

**Files:**
- Modify: `scripts/promote.py` (append)
- Modify: `tests/promotion/test_promote.py` (append)

**Interfaces:**
- Produces: `class GitHub(repo, token, transport=None)` with `get(path) -> dict|list`, `get_text(path, accept) -> str`, `put(path, body) -> dict`, `post(path, body) -> dict`, `patch(path, body) -> dict`; `policy_at(gh, ref) -> dict`; `merge(gh, number, head_sha, method) -> dict`; `upsert_comment(gh, number, body) -> None`; `run(repo, number, token, *, dry_run=False, transport=None) -> int`.
  `transport(method, url, headers, body: bytes|None) -> (status: int, body: bytes)` is injectable.

- [ ] **Step 1: Write the failing tests (append)**

```python
# ---------------------------------------------------------------- github + run

class FakeGitHub:
    """Records every call; answers from a small table keyed by (method, path)."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, method, url, headers, body):
        path = url.replace("https://api.github.com", "")
        self.calls.append((method, path, json.loads(body) if body else None, headers.get("Accept")))
        status, payload = self.answers[(method, path)]
        return status, (payload if isinstance(payload, bytes) else json.dumps(payload).encode())


def answers_for(pr_obj, files_list, runs, policy_text=None):
    policy_text = POLICY_FILE.read_text() if policy_text is None else policy_text
    n = pr_obj["number"]
    return {
        ("GET", f"/repos/arigsela/kubernetes/pulls/{n}"): (200, pr_obj),
        ("GET", f"/repos/arigsela/kubernetes/pulls/{n}/files?per_page=100"): (200, files_list),
        ("GET", f"/repos/arigsela/kubernetes/commits/{pr_obj['head']['sha']}/check-runs?per_page=100"): (200, {"check_runs": runs}),
        ("GET", f"/repos/arigsela/kubernetes/contents/.github/promotion-policy.yaml?ref={pr_obj['base']['sha']}"): (200, policy_text.encode()),
        ("GET", f"/repos/arigsela/kubernetes/issues/{n}/comments?per_page=100"): (200, []),
        ("PUT", f"/repos/arigsela/kubernetes/pulls/{n}/merge"): (200, {"merged": True, "sha": "m1"}),
        ("POST", f"/repos/arigsela/kubernetes/issues/{n}/comments"): (201, {"id": 1}),
    }


def test_run_auto_merges_by_rebase_pinned_to_head_and_comments():
    fake = FakeGitHub(answers_for(pr(), files("base-apps/donetick/deployment.yaml"), checks("success")))
    rc = pm.run("arigsela/kubernetes", 760, "tok", transport=fake)
    assert rc == 0
    merge = next(c for c in fake.calls if c[0] == "PUT")
    assert merge[2] == {"merge_method": "rebase", "sha": "abc123"}
    comment = next(c for c in fake.calls if c[0] == "POST")
    assert comment[2]["body"].startswith(pm.COMMENT_MARKER)
    assert "auto" in comment[2]["body"] and "base456" in comment[2]["body"]


def test_merge_sends_head_sha():
    fake = FakeGitHub(answers_for(pr(head={"sha": "feedface", "repo": {"full_name": "arigsela/kubernetes"}}),
                                  files("x.md"), checks("success")))
    pm.run("arigsela/kubernetes", 760, "tok", transport=fake)
    assert next(c for c in fake.calls if c[0] == "PUT")[2]["sha"] == "feedface"


def test_run_owner_neither_merges_nor_comments():
    fake = FakeGitHub(answers_for(pr(labels=[{"name": "review:read"}]), files("x.md"), checks("success")))
    assert pm.run("arigsela/kubernetes", 760, "tok", transport=fake) == 0
    assert all(c[0] == "GET" for c in fake.calls)


def test_policy_is_read_at_the_base_sha():
    fake = FakeGitHub(answers_for(pr(), files("x.md"), checks("success")))
    pm.run("arigsela/kubernetes", 760, "tok", transport=fake)
    assert any("contents/.github/promotion-policy.yaml?ref=base456" in c[1] for c in fake.calls)


def test_bad_base_policy_is_owner_with_reason_and_no_merge():
    fake = FakeGitHub(answers_for(pr(), files("x.md"), checks("success"), policy_text="version: 9\n"))
    assert pm.run("arigsela/kubernetes", 760, "tok", transport=fake) == 0
    assert all(c[0] == "GET" for c in fake.calls)


def test_dry_run_decides_but_does_not_merge():
    fake = FakeGitHub(answers_for(pr(), files("x.md"), checks("success")))
    assert pm.run("arigsela/kubernetes", 760, "tok", dry_run=True, transport=fake) == 0
    assert all(c[0] == "GET" for c in fake.calls)


def test_already_merged_pr_is_a_noop():
    fake = FakeGitHub(answers_for(pr(merged=True, state="closed"), files("x.md"), checks("success")))
    assert pm.run("arigsela/kubernetes", 760, "tok", transport=fake) == 0
    assert all(c[0] == "GET" for c in fake.calls)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/promotion/test_promote.py -q -k "run or merge or policy_is"`
Expected: FAIL with `AttributeError: module 'promote' has no attribute 'run'`.

- [ ] **Step 3: Append the client and `main()`**

```python
# ------------------------------------------------------------------ github

def http_transport(method: str, url: str, headers: dict, body: bytes | None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


class GitHub:
    def __init__(self, repo: str, token: str, transport=None):
        self.repo, self.token = repo, token
        self.transport = transport or http_transport

    def _call(self, method: str, path: str, body: dict | None = None, accept: str = "application/vnd.github+json"):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": accept,
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "promote.py"}
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        status, raw = self.transport(method, API + path, headers, data)
        if status >= 400:
            raise RuntimeError(f"GitHub {method} {path} -> {status}: {raw[:300]!r}")
        return raw

    def get(self, path: str):
        return json.loads(self._call("GET", path) or b"null")

    def get_text(self, path: str, accept: str = "application/vnd.github.raw+json") -> str:
        return self._call("GET", path, accept=accept).decode()

    def put(self, path: str, body: dict):
        return json.loads(self._call("PUT", path, body) or b"null")

    def post(self, path: str, body: dict):
        return json.loads(self._call("POST", path, body) or b"null")

    def patch(self, path: str, body: dict):
        return json.loads(self._call("PATCH", path, body) or b"null")


def policy_at(gh: GitHub, ref: str) -> dict:
    """The policy as of `ref` (the PR's base SHA): a PR cannot loosen its own promotion."""
    text = gh.get_text(f"/repos/{gh.repo}/contents/.github/promotion-policy.yaml?ref={ref}")
    return load_policy(text)


def merge(gh: GitHub, number: int, head_sha: str, method: str) -> dict:
    """Merge pinned to the SHA that was evaluated; a push in between makes GitHub refuse (409)."""
    return gh.put(f"/repos/{gh.repo}/pulls/{number}/merge", {"merge_method": method, "sha": head_sha})


def upsert_comment(gh: GitHub, number: int, body: str) -> None:
    existing = gh.get(f"/repos/{gh.repo}/issues/{number}/comments?per_page=100")
    mine = next((c for c in existing if str(c.get("body", "")).startswith(COMMENT_MARKER)), None)
    if mine:
        gh.patch(f"/repos/{gh.repo}/issues/comments/{mine['id']}", {"body": body})
    else:
        gh.post(f"/repos/{gh.repo}/issues/{number}/comments", {"body": body})


def log(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True), flush=True)


def run(repo: str, number: int, token: str, *, dry_run: bool = False, transport=None) -> int:
    gh = GitHub(repo, token, transport)
    pr = gh.get(f"/repos/{repo}/pulls/{number}")
    if pr.get("merged"):
        log({"event": "promotion", "pr": number, "class": "noop", "reasons": ["already merged"]})
        return 0
    head_sha = pr["head"]["sha"]
    base_sha = pr["base"]["sha"]
    try:
        policy = policy_at(gh, base_sha)
    except (PolicyError, RuntimeError) as err:
        log({"event": "promotion", "pr": number, "class": "owner", "reasons": [f"policy at base unreadable: {err}"]})
        return 0
    files = gh.get(f"/repos/{repo}/pulls/{number}/files?per_page=100")
    runs = gh.get(f"/repos/{repo}/commits/{head_sha}/check-runs?per_page=100").get("check_runs") or []
    decision = decide(pr, files, runs, policy)
    log({"event": "promotion", "pr": number, "class": decision.klass, "reasons": decision.reasons,
         "head": head_sha, "policy_at": base_sha, "dry_run": dry_run})
    if decision.klass != "auto" or dry_run:
        return 0
    result = merge(gh, number, decision.head_sha, policy["auto"]["merge_method"])
    checks_line = ", ".join(sorted(str(r.get("name")) for r in runs))
    upsert_comment(gh, number, (
        f"{COMMENT_MARKER}\n### promotion: auto\n"
        f"Merged by the promotion policy ({policy['auto']['merge_method']}, head `{head_sha[:12]}`), "
        f"policy read at base `{base_sha[:12]}`.\n"
        f"**Why:** author in `auto.authors`, label(s) {', '.join(policy['auto']['require_labels'])} present, "
        f"{len(files)} file(s) outside every deny prefix, every check green.\n"
        f"<details><summary>Checks</summary>\n\n{checks_line}\n</details>\n"
        f"<sub>scripts/promote.py · .github/promotion-policy.yaml</sub>"))
    log({"event": "promotion", "pr": number, "merged": bool(result.get("merged")), "sha": result.get("sha")})
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--dry-run", action="store_true", help="decide and log, never merge or comment")
    args = ap.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("promote: GITHUB_TOKEN is required", file=sys.stderr)
        return 1
    try:
        return run(args.repo, args.pr, token, dry_run=args.dry_run)
    except RuntimeError as err:
        print(f"promote: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the suite, commit**

Run: `python -m pytest tests/promotion/ -q`
Expected: `25 passed`.

```bash
git add scripts/promote.py tests/promotion/test_promote.py
git commit -m "feat(promotion): merge by rebase pinned to the evaluated head, one sticky comment"
```

### Task B4: The workflow and the owner's three actions

**Files:**
- Create: `.github/workflows/promote.yaml`
- Modify: `.github/workflows/validate.yaml` (job `promotion-validate`)

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/promote.yaml
---
# Promotion (ADP path 5): merge the one class of PR the policy says needs no human.
# Runs AFTER validate or pr-triage completes on a PR, on `workflow_run` ONLY, so the version of
# this file on main is what runs, never the PR's. The decision and the merge are
# scripts/promote.py under .github/promotion-policy.yaml read at the PR's base commit.
# Identity: the arigsela-promotion GitHub App (bypass actor on the main ruleset, pull_request
# mode), distinct from Renovate's app by design (§V.90).
name: promote

on:
  workflow_run:
    workflows: [validate, pr-triage]
    types: [completed]

permissions: {}

concurrency:
  group: promote-${{ github.event.workflow_run.pull_requests[0].number || github.event.workflow_run.id }}
  cancel-in-progress: false

jobs:
  promote:
    # Same-repo PRs only (forks carry no pull_requests entry); any conclusion: the script
    # re-checks every check run itself and stays `owner` while anything is pending or red.
    if: github.event.workflow_run.pull_requests[0] != null && github.event.workflow_run.head_repository.full_name == github.repository
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
      - name: Set up Python
        uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5
        with:
          python-version: "3.12"
      - name: Mint a token from the promotion GitHub App
        id: app-token
        uses: actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1 # v3.2.0
        with:
          client-id: ${{ vars.PROMOTION_APP_CLIENT_ID }}
          private-key: ${{ secrets.PROMOTION_APP_PRIVATE_KEY }}
          owner: arigsela
          repositories: kubernetes
      - name: Decide, and merge if auto
        env:
          GITHUB_TOKEN: ${{ steps.app-token.outputs.token }}
        run: python scripts/promote.py --repo ${{ github.repository }} --pr ${{ github.event.workflow_run.pull_requests[0].number }}
```

Pin the three action SHAs to the same values the other workflows use (Renovate groups them).

- [ ] **Step 2: Add the CI job to `validate.yaml`**

```yaml
  # The promotion decision is the only thing that merges without a human (§V.89); its tests
  # are the proof that the deny list and the base-commit policy read hold.
  promotion-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
      - name: Set up Python
        uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install pyyaml==6.0.2 pytest==8.3.3
      - name: Run promotion tests
        run: python -m pytest tests/promotion/ -q
```

- [ ] **Step 3: Validate and commit**

```bash
yamllint -c .yamllint.yaml .github/workflows/promote.yaml .github/workflows/validate.yaml
git add .github/workflows/promote.yaml .github/workflows/validate.yaml
git commit -m "ci(promotion): the promote workflow (workflow_run only) and its test job"
```

- [ ] **Step 4: Owner actions (before the PR merges; record each in the PR body)**

1. GitHub → Settings → Developer settings → GitHub Apps → New: name `arigsela-promotion`; no webhook; permissions Contents **Read and write**, Pull requests **Read and write**, Checks **Read**, Metadata **Read**; "Only on this account". Install on `arigsela/kubernetes` only. Generate a private key.
2. Repository → Settings → Secrets and variables → Actions: variable `PROMOTION_APP_CLIENT_ID` = the app's Client ID; secret `PROMOTION_APP_PRIVATE_KEY` = the PEM.
3. Repository → Settings → Rules → `main` → Bypass list → add the app `arigsela-promotion`, mode **Pull requests only**. Verify:
   `gh api repos/arigsela/kubernetes/rulesets/11283229 --jq '.bypass_actors'` shows two actors.

### Task B5: Docs, ledger, acceptance

**Files:**
- Create: `docs/platform/promotion.md`
- Modify: `CLAUDE.md` (PR Review Labels section), `docs/index.md` (platform table row, if the index lists `docs/platform/`), `SPEC.md` (V89, V90, T106, T107)

- [ ] **Step 1: Write `docs/platform/promotion.md`**

```markdown
# Promotion policy

What merges without a human, and why that list is short. Spec:
`docs/superpowers/specs/2026-10-12-signal-dispatch-and-promotion-design.md`.

## The one automatic class
A pull request merges by itself when **all** of these hold (`scripts/promote.py`, policy
`.github/promotion-policy.yaml`):
- opened by the Renovate app (`arigsela-renovate[bot]`), from this repository, not a draft;
- labelled `review:skip` by triage (docs-only, or an image-tag bump of at most 4 lines);
- at most 2 changed files, none under a deny prefix (Terraform, `.github/`, appsets, Ansible,
  node-config, Vault, PostgreSQL, kagent, Substrate, admission policies, Istio, Dex,
  cluster-rbac, the dispatcher);
- every check run on the head commit completed green or skipped, none pending;
- the policy read at the PR's **base** commit parsed. A PR cannot loosen its own promotion.

Everything else is `owner`: the triage label says how closely to read, and the owner merges.

## How it runs
`.github/workflows/promote.yaml` triggers on `workflow_run` of `validate` and `pr-triage`
(so the `main` version of the workflow always runs), mints a token from the
`arigsela-promotion` GitHub App, and runs the script. On `auto` it merges by rebase pinned to
the evaluated head SHA and leaves one `<!-- promotion -->` comment. On `owner` it logs only.

## Identity
The app is a bypass actor on the `main` ruleset in pull-request mode: it can merge without
the one required approval and cannot push to `main`. It is not Renovate's app: author and
promoter are different principals, and the merge commit's committer shows it.

## Changing the policy
Edit `.github/promotion-policy.yaml` in a PR. It is `review:read` under triage and under a
deny prefix here, so a policy change is always read by a human and never auto-merged.

## Observability
`gh run list --workflow promote` lists decisions; each run logs one JSON line with `class`
and `reasons`. `gh pr list --search "commenter:app/arigsela-promotion"` lists what was
promoted.

## Widening it later
Candidates, each its own policy PR: `review:skim` Renovate PRs for non-stateful apps after
a month of clean `auto` merges; docs-only PRs by the owner's coding agent once it has its own
identity (path 3).
```

- [ ] **Step 2: `CLAUDE.md`**

In "PR Review Labels (pr-triage)", add after the "Next steps" bullet:

```markdown
- **Promotion:** `review:skip` PRs from Renovate that touch no deny-listed path and have every check green are merged by `.github/workflows/promote.yaml` under `.github/promotion-policy.yaml` (read at the PR's base commit). Nothing else merges without the owner. See `docs/platform/promotion.md`.
```

- [ ] **Step 3: Ledger**

Append to `## §V INVARIANTS`:

```
V89: PR merged ⊥ human ⇔ `scripts/promote.py` → `auto` under `.github/promotion-policy.yaml` @ PR BASE sha & author ∉ humans. `.github/**`, `terraform/**`, deny_prefixes ⊥ auto ever.
V90: promotion identity = GitHub App `arigsela-promotion` ≠ Renovate's app, installed on this repo only, ruleset bypass mode `pull_request` only.
```

Append to `## §T TASKS`:

```
T106|.|promotion policy (ADP path 5): `.github/promotion-policy.yaml` + `scripts/promote.py` + `promote.yaml` (workflow_run only) merge `review:skip` Renovate PRs by rebase; one sticky comment|V89,V90
T107|.|OWNER ACTION: create GitHub App `arigsela-promotion` (Contents RW, Pull requests RW, Checks R), install on this repo, set `PROMOTION_APP_CLIENT_ID` var + `PROMOTION_APP_PRIVATE_KEY` secret, add as ruleset bypass actor (pull_request mode)|V90
```

Add `§T.106`, `§T.107` to the goal line's `open:` list.

- [ ] **Step 4: Validate and commit**

```bash
python scripts/gen-techdocs.py --check --repo-root . && python -m pytest tests/techdocs/ -q
git add docs/platform/promotion.md CLAUDE.md SPEC.md docs/index.md
git commit -m "docs(promotion): the policy explained; ledger V89-V90, T106-T107"
```

- [ ] **Step 5: Open the PR, then accept live**

```bash
git push -u origin feat/promotion
gh pr create --base main --title "feat(promotion): merge review:skip Renovate PRs by policy, read at the base commit" --body-file - <<'EOF'
## What
ADP path 5, promotion as code. `.github/promotion-policy.yaml` declares the one class of PR that merges without a human: Renovate's, labelled `review:skip` by triage, at most 2 files, none under a deny prefix, every check green. `scripts/promote.py` decides (policy read at the PR's **base** commit) and merges by rebase pinned to the evaluated head; `.github/workflows/promote.yaml` runs it on `workflow_run` only, with a token from the new `arigsela-promotion` GitHub App. Everything else stays `owner`. Design: `docs/superpowers/specs/2026-10-12-signal-dispatch-and-promotion-design.md`.

Of the last 100 merged PRs, 40 were Renovate's and 17 of those were `review:skip`: that is the population this automates.

## Owner actions done before merge
- [ ] GitHub App `arigsela-promotion` created and installed on this repo only
- [ ] `PROMOTION_APP_CLIENT_ID` variable and `PROMOTION_APP_PRIVATE_KEY` secret set
- [ ] App added to the `main` ruleset bypass list, pull-request mode

## Validation
- `tests/promotion/` (loader, decision, client, run): pass; yamllint on both workflows
- No cluster, DNS, IAM, Vault or Terraform impact. A new GitHub App identity (above).
EOF
```

After the owner merges and the next Renovate run opens PRs: find one labelled `review:skip`
outside the deny list (`gh pr list --author app/arigsela-renovate --label review:skip`),
watch `gh run list --workflow promote --limit 5`, and confirm the PR shows the
`<!-- promotion -->` comment and a rebase merge by the app. Confirm one `review:skim` Renovate
PR was **not** merged and its promote run logged `"class": "owner"`. Record both as the
recorded screen for the whole-picture deck's path 5, and flip T106 and T107 to `x`.

---

## Self-review

**Spec coverage.** Dispatcher shape, table, completeness, read-only enforcement, budget,
identity, observability, failure modes → A1–A8; the second Grafana integration and
`agent-findings` → A7; V86–V88 → A8; acceptance → A9. Promotion policy file, decision,
identity, `workflow_run`-only trigger, base-commit read, comment, owner actions → B1–B5;
V89–V90 → B5. Non-goals (a GitHub-check source, chat approvals, Renovate automerge) have no
task, by design.

**Placeholders.** None: every step carries its file content or command.

**Type consistency.** `Dispatcher.handle_grafana` returns the list of decision dicts whose
`decision` key the HTTP handler flattens; `investigate(alert, rule)` is what the thread
target and the synchronous path both call; `Config.from_env(env)` is used by `main()` and
the test; `Decision(klass, reasons, head_sha)` is what `run()` reads; `FakeGitHub` answers
the exact paths `run()` requests (`?per_page=100` on files, check-runs and comments; `?ref=`
on contents).

**Review Focus.** Each of the five lines names its test and task above.
