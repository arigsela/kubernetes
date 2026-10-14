# Implement inside the platform, and remediate on signal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An in-cluster `implementer` agent that edits this repository and opens pull requests under its own GitHub App identity, and the two resource alerts routed to it so the first unprompted write on the platform is a pull request a human merges.

**Architecture:** A fourth capability class `propose` (branches and PRs only, ungated) is added to the taxonomy and every consumer. A second GitHub MCP server instance (`implementer-mcp`) runs with GitHub App auth and an explicit tool list; a `propose`-class AgentTemplate binds its tools plus the existing read tools. The dispatcher learns one limit (`open_prs_max`) and routes the two resource alerts to the new agent. Nothing merges without a human; a test pins that promotion decides `owner` for the app.

**Tech Stack:** kagent 1.0.0-alpha11 (`api.kagent.dev/v1alpha3`), kmcp `MCPServer`, `ghcr.io/github/github-mcp-server` v1.14.0 (stdio, GitHub App auth, `--tools`), External Secrets + Vault KV v2, Python 3.12 stdlib + PyYAML + pytest, Argo CD.

**Spec:** `docs/superpowers/specs/2026-10-14-implementer-and-remediate-design.md`

## Global Constraints

- Every change is a PR from a feature branch; CI (`.github/workflows/validate.yaml`) must be green; the owner merges (admin merge is authorised for this plan's PRs). Never `kubectl apply/patch`.
- Never commit a secret; the GitHub App private key reaches Vault only through `scripts/provision-implementer-vault.sh --key-file <path>` run by the owner. Never print it.
- Vault key `k8s-secrets/implementer-github-app`, properties `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY`; policy and role `implementer-mcp`; ESO ServiceAccount `eso-implementer-mcp` in `kagent`; SecretStore `vault-implementer-mcp`; Secret `implementer-github-app`.
- The implementer MCP server loads exactly: read `get_file_contents`, `search_code`, `list_commits`, `get_commit`, `list_branches`, `list_pull_requests`, `pull_request_read`; propose `create_branch`, `create_or_update_file`, `push_files`, `create_pull_request`, `update_pull_request`. Never `merge_pull_request`, `update_pull_request_branch`, `pull_request_review_write`, `add_comment_to_pending_review`, `delete_file`, `create_repository`, `delete_repository`, `fork_repository`.
- Taxonomy classes ordered `read < propose < write < destructive`; template classes `read`, `propose`, `write`, `admin`; `MUST_APPROVE` stays `{write, destructive}`.
- A SecretStore whose Vault role does not exist yet makes the whole `kagent-secrets` sync fail (Degraded resource): the ESO manifests merge only after the owner has run the provisioning script (Task 3 waits on the owner step).
- Generated files are never hand-edited: `base-apps/admission-policies/agent-capability.yaml` (gen-agent-capability-policy.py), `base-apps/postgresql/agent-audit-cronjob.yaml` (gen-agent-audit-cronjob.py), `base-apps/dispatcher/code-configmap.yaml` + the Deployment checksums (gen-dispatcher-configmap.py), `base-apps/index.md` (gen-okf.py), `docs/index.md` + mkdocs (gen-techdocs.py).
- Run pytest one suite at a time: `python3 -m pytest tests/<suite>/ -q`.
- Two git-mutating Bash calls never run in parallel.

## Review Focus

1. **A pod name with dots or uppercase in the alert labels** → the branch slug must still be a valid git ref: the prompt fixes the slug charset to `[a-z0-9-]` and says how to derive it (Task 4, `test_prompt_fixes_the_branch_slug`).
2. **The number lives in a Helm `valuesObject`, not a plain manifest** (most apps under `base-apps/<app>.yaml`) → the agent must edit the Application's values, not invent a Deployment: the prompt says so (Task 4, `test_prompt_covers_chart_rendered_apps`).
3. **GitHub returns 403 (rate limit) or times out while counting open PRs** → the dispatcher still routes the investigation and logs the failure; it never fails closed on a read (Task 5, `test_open_prs_count_failure_routes_and_logs`).
4. **The alert fires again after the fix merged but before the pod restarted** → the agent compares the manifest's limit with the live pod's and opens no second PR when they differ (Task 4, `test_prompt_handles_a_fix_already_in_git`).
5. **A typo in the explicit tool list** → the server fails at startup and the pod crashloops with the agent silently toolless: every `--tools` name is checked against a pinned list of names the v1.14.0 server knows (Task 3, `test_every_loaded_tool_name_is_a_known_server_tool`).

---

### Task 1: Taxonomy class `propose` in every consumer

**Files:**
- Modify: `base-apps/admission-policies/agent-capability-taxonomy.yaml`
- Modify: `scripts/validate-agent-capability.py:46-57,95,164-180`
- Modify: `scripts/gen-agent-capability-policy.py:50-80,154-230,280-297`
- Regenerate: `base-apps/admission-policies/agent-capability.yaml`
- Modify: `scripts/agent-audit-risk.py:103-111,138-141,158`
- Regenerate: `base-apps/postgresql/agent-audit-cronjob.yaml`
- Test: `tests/agent-capability/test_validate_agent_capability.py`
- Test: `tests/agent-audit/test_agent_audit_risk.py`, `tests/agent-audit/test_agent_audit_v1.py`
- Create fixtures: `tests/admission-policies/fixtures/agent-capability/good/template-propose-ungated.yaml`, `.../bad/agent-capability/bad-read-binds-propose.yaml`, `.../bad/agent-capability/bad-propose-binds-write.yaml`, `.../bad/agent-capability-delegation/bad-propose-delegates-to-admin.yaml`

**Interfaces:**
- Consumes: nothing.
- Produces: taxonomy `data.propose` (JSON list of 5 tool names); `vac.CLASS_RANK` with `propose: 1`; `vac.CLASS_MAY_BIND["propose"] == {"read", "propose"}`; generator `build_native(read, propose, write, destructive, runtime_images)` and `load_taxonomy()` returning `(read, propose, write, destructive, runtime_images)`; risk `load_gated_tools()` mapping propose tools to `"propose"`, `is_candidate` reason `propose:<tool>`, state counts key `propose`.

- [ ] **Step 1: Write the failing validator tests**

Append to `tests/agent-capability/test_validate_agent_capability.py` (and add `"propose": ["create_pull_request", "push_files"]` to the `TAXONOMY` dict at the top of the file, and `"list_pull_requests"` to its `read` list):

```python
# ------------------------------------------- class propose (branches and PRs only)

def test_read_template_binding_propose_tool_is_error(tmp_path):
    assert run(tmp_path, [
        template("reader", "read", tools=["k8s_get_resources", "create_pull_request"], approval=False),
    ]) == 1


def test_propose_template_binding_write_tool_is_error(tmp_path):
    assert run(tmp_path, [
        template("proposer", "propose", tools=["k8s_get_resources"], approval=False,
                 gated=["k8s_patch_resource"]),
    ]) == 1


def test_propose_template_binds_propose_tools_ungated(tmp_path):
    assert run(tmp_path, [
        template("proposer", "propose",
                 tools=["k8s_get_resources", "create_pull_request", "push_files"], approval=False),
    ]) == 0


def test_write_template_may_bind_propose_tools_ungated(tmp_path):
    assert run(tmp_path, [
        template("operator", "write", tools=["create_pull_request"], approval=False,
                 gated=["k8s_patch_resource"]),
    ]) == 0


def test_propose_template_with_admin_subagent_is_error(tmp_path):
    assert run(tmp_path, [
        template("proposer", "propose", tools=["create_pull_request"], approval=False,
                 subagents=["operator"]),
        template("operator", "admin", tools=["k8s_get_resources"], approval=False,
                 gated=["k8s_delete_resource"]),
    ]) == 1


def test_read_template_with_propose_subagent_is_error(tmp_path):
    assert run(tmp_path, [
        template("reader", "read", tools=["k8s_get_resources"], approval=False,
                 subagents=["proposer"]),
        template("proposer", "propose", tools=["create_pull_request"], approval=False),
    ]) == 1


def test_propose_is_ranked_between_read_and_write():
    assert vac.CLASS_RANK["read"] < vac.CLASS_RANK["propose"] < vac.CLASS_RANK["write"] < vac.CLASS_RANK["admin"]
    assert vac.CLASS_MAY_BIND["propose"] == {"read", "propose"}
    assert "propose" not in vac.MUST_APPROVE


def test_real_taxonomy_propose_list_is_exactly_the_five_pr_tools():
    tools, _ = vac.load_taxonomy(REPO)
    assert {t for t, c in tools.items() if c == "propose"} == {
        "create_branch", "create_or_update_file", "push_files", "create_pull_request", "update_pull_request"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/agent-capability/test_validate_agent_capability.py -q -k "propose"`
Expected: FAIL (the three "is_error" tests see exit 0 because the class is unknown → actually exit 1 with "invalid class"; the "ungated" and "may_bind" tests get 1; `test_propose_is_ranked...` KeyError; the real-taxonomy test gets an empty set). Read the output: at least the four positive tests fail.

- [ ] **Step 3: Add the class to the taxonomy**

In `base-apps/admission-policies/agent-capability-taxonomy.yaml`:
- Header comment: change "Three classifications, ordered read < write < destructive" to "Four classifications, ordered read < propose < write < destructive" and add after the `read` paragraph:
  ```
  #   propose      changes a PROPOSAL surface only: a branch or a pull request on
  #                arigsela/kubernetes, never a cluster, an account or a merge. May be
  #                bound without requireApproval; the merge is the human gate (§V.94).
  #                A `read` template may not bind it; `propose`, `write` and `admin` may.
  ```
- Append to the `read` list (after `"search_code",`): `"list_commits"`, `"get_commit"`, `"list_branches"`, `"list_pull_requests"`, `"pull_request_read"`. Update the `read (55)` count comment to `read (60)`.
- Add a new key between `read` and `write`:
  ```yaml
    # ------------------------------------------------------------- propose (5)
    # Branches and pull requests on arigsela/kubernetes, through the implementer GitHub MCP
    # server (base-apps/kagent/implementer-mcp.yaml). Nothing here touches a cluster or merges
    # anything: merge_pull_request, delete_file and the review tools are never loaded there.
    propose: |
      [
        "create_branch",
        "create_or_update_file",
        "push_files",
        "create_pull_request",
        "update_pull_request"
      ]
  ```

- [ ] **Step 4: Teach the validator the class**

In `scripts/validate-agent-capability.py`:
```python
# read < propose < write < admin
CLASS_RANK = {"read": 0, "propose": 1, "write": 2, "admin": 3}

# What each class may bind.
CLASS_MAY_BIND = {
    "read": {"read"},
    "propose": {"read", "propose"},
    "write": {"read", "propose", "write"},
    "admin": {"read", "propose", "write", "destructive"},
}
```
In `load_taxonomy`, change `for classification in ("read", "write", "destructive"):` to `("read", "propose", "write", "destructive")`. In `check_declared_class`, change the message to `Must be one of: read, propose, write, admin.` Update the module docstring's invariant list ("a `read` template binds only read tools; `propose` adds the proposal tools…") in one sentence.

- [ ] **Step 5: Run the validator tests**

Run: `python3 -m pytest tests/agent-capability/ -q`
Expected: all pass, including `test_the_real_repo_passes` (no template binds a propose tool yet).

- [ ] **Step 6: Teach the generator the class and regenerate**

In `scripts/gen-agent-capability-policy.py`:
- `load_taxonomy`: keys `("read", "propose", "write", "destructive", "runtime-images")`, return annotation five lists.
- `build_native(read, propose, write, destructive, runtime_images)`: `classified = read + propose + write + destructive`; `mutating = write + destructive` (unchanged); add variable `{"name": "propose", "expression": _cel_list(propose)}`; validations:
  - class check: `"variables.cls in ['read', 'propose', 'write', 'admin']"`, message "must be one of read, propose, write, admin".
  - read rule: `"variables.cls != 'read' || " + names.format(cond="!(n in variables.mutating) && !(n in variables.propose)")`, message "is class read but binds propose or mutating tools. A read agent may bind only read-classified tools."
  - new propose rule after it: `"variables.cls != 'propose' || " + names.format(cond="!(n in variables.mutating)")`, message "is class propose but binds write or destructive tools. A propose agent may bind read and propose tools only."
  - write and ungated rules unchanged.
- Delegation policy: object selector values `["read", "propose", "write"]`; `theirs` expression `... in ['read', 'propose', 'write'] ? ... : 'admin'`; keep the read rule; add `_deny("!(variables.mine == 'propose' && variables.delegatesToParam) || variables.theirs in ['read', 'propose']", tpl + "is class propose but binds subagent template '\" + params.metadata.name + \"' (class \" + variables.theirs + \"). A subagent runs inside the parent, so this is a privilege escalation.\"")`; keep the write rule.
- `render_native`: unpack five lists.
- Header comment: rule 1 lists the four classes; add "3b. a `propose` template binding any write/destructive tool" and "6b. a `propose` template with a subAgent whose template is `write` or `admin`".

Run: `python3 scripts/gen-agent-capability-policy.py --repo-root .` then `python3 scripts/gen-agent-capability-policy.py --repo-root . --check`
Expected: the policy file is rewritten; `--check` exits 0.

- [ ] **Step 7: Admission fixtures for the real API server**

Create `tests/admission-policies/fixtures/agent-capability/good/template-propose-ungated.yaml`:
```yaml
# A propose template binds read and propose tools with no approval gate (§V.94).
apiVersion: api.kagent.dev/v1alpha3
kind: AgentTemplate
metadata:
  name: good-propose-ungated
  namespace: kagent
  labels:
    capability.homelab/class: propose
spec:
  modelConfig:
    name: anthropic-claude-haiku-5-5
  systemPrompt: fixture
  tools:
    - mcp:
        server:
          kind: RemoteMCPServer
          name: agent-docs
        requireApproval: false
        tools:
          - get_file_contents
          - create_pull_request
```
Create `tests/admission-policies/fixtures/agent-capability/bad/agent-capability/bad-read-binds-propose.yaml` (same shape, `name: bad-read-binds-propose`, class `read`, comment `# rule 3: a read template binds a propose tool.`), `bad-propose-binds-write.yaml` (class `propose`, tools `get_file_contents` and `k8s_patch_resource` in a `requireApproval: true` binding, comment `# rule 3b: a propose template binds a write tool, gated or not.`), and `tests/admission-policies/fixtures/agent-capability/bad/agent-capability-delegation/bad-propose-delegates-to-admin.yaml`:
```yaml
# rule 6b: propose template binds the admin k8s-agent template as a subagent.
apiVersion: api.kagent.dev/v1alpha3
kind: AgentTemplate
metadata:
  name: bad-propose-delegates-to-admin
  namespace: kagent
  labels:
    capability.homelab/class: propose
spec:
  modelConfig:
    name: anthropic-claude-haiku-5-5
  systemPrompt: fixture
  tools:
    - mcp:
        server:
          kind: RemoteMCPServer
          name: agent-docs
        requireApproval: false
        tools:
          - create_pull_request
    - subAgent:
        name: operator
        description: fixture subagent
        templateRef:
          name: k8s-agent
```

Run: `python3 -m pytest tests/admission-policies/ -q -k "propose or real_kagent"` (Docker boots a k3s; ~3 min)
Expected: the good fixture allowed; each bad fixture denied by the policy its directory names; real manifests still allowed.

- [ ] **Step 8: Failing audit tests**

Append to `tests/agent-audit/test_agent_audit_risk.py`:
```python
def test_load_gated_tools_reads_the_propose_class():
    cls = ar.load_gated_tools(TAXONOMY)
    assert cls["create_pull_request"] == "propose"


def test_propose_call_is_a_candidate_with_its_own_reason(classes):
    assert ar.is_candidate([_call("create_pull_request", {"title": "fix"})], classes) == ["propose:create_pull_request"]


def test_state_counts_propose_calls(classes):
    state = ar.build_state([_call("create_pull_request", {"title": "fix"})], classes)
    assert state["counts"]["propose"] == 1 and state["counts"]["unknown"] == 0
```
Append to `tests/agent-audit/test_agent_audit_v1.py` (near `GATED`):
```python
def test_propose_tools_are_not_gated_for_the_ungated_finding():
    assert "create_pull_request" not in GATED and "k8s_patch_resource" in GATED
```
Run: `python3 -m pytest tests/agent-audit/ -q -k "propose"`
Expected: the first three FAIL (`KeyError`/`unknown`), the last passes already (write/destructive only) — keep it as the pin.

- [ ] **Step 9: Risk classifier learns the class**

In `scripts/agent-audit-risk.py`: `load_gated_tools` loops `("read", "propose", "write", "destructive")`; in `is_candidate` add after the gated branch:
```python
        elif cls == "propose":
            reasons.append(f"propose:{c['tool']}")
```
In `build_state`: `counts = {"read": 0, "propose": 0, "write": 0, "destructive": 0, "unknown": 0, "approvals": 0}`. Add one sentence to the module docstring: "A `propose` call (a branch or PR opened by an agent) always goes to Jev: an unprompted proposal deserves a look even though nothing in the cluster changed."

Run: `python3 -m pytest tests/agent-audit/ -q`
Expected: all pass. Then `python3 scripts/gen-agent-audit-cronjob.py --repo-root .` and `--check` → exit 0.

- [ ] **Step 10: Whole-suite check and commit**

Run: `python3 scripts/validate-agent-capability.py --repo-root . && python3 -m pytest tests/agent-capability/ -q && python3 -m pytest tests/dispatch/ -q && python3 -m pytest tests/agent-audit/ -q`
Expected: all green (dispatch still passes: no propose route exists yet).

```bash
git checkout -b feat/propose-class
git add base-apps/admission-policies/agent-capability-taxonomy.yaml base-apps/admission-policies/agent-capability.yaml scripts/validate-agent-capability.py scripts/gen-agent-capability-policy.py scripts/agent-audit-risk.py base-apps/postgresql/agent-audit-cronjob.yaml tests/agent-capability tests/agent-audit tests/admission-policies/fixtures
git commit -m "feat(guardrails): capability class propose (branches and pull requests, ungated; the merge is the gate)"
```
Open the PR; wait for green; admin-merge (rebase).

---

### Task 2: Vault provisioning script for the implementer's GitHub App

**Files:**
- Create: `scripts/provision-implementer-vault.sh`
- Test: `tests/implementer/test_provisioning.py` (new suite; `tests/implementer/conftest.py` created here)

**Interfaces:**
- Consumes: nothing.
- Produces: Vault key `k8s-secrets/implementer-github-app` with properties `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY`; Vault policy and role `implementer-mcp` (SA `eso-implementer-mcp`, ns `kagent`, ttl 1h). Task 3's manifests name exactly these.

- [ ] **Step 1: Write the failing test**

Create `tests/implementer/conftest.py`:
```python
# tests/implementer/conftest.py
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
```
Create `tests/implementer/test_provisioning.py`:
```python
# tests/implementer/test_provisioning.py
"""The provisioning script and the manifests agree on every name (a stray property name is
the failure the agent-docs runbook already documents)."""
import subprocess
from conftest import REPO

SCRIPT = REPO / "scripts/provision-implementer-vault.sh"


def test_script_parses_and_refuses_values_on_the_command_line():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    text = SCRIPT.read_text()
    assert "--key-file" in text and "--app-id" in text and "--installation-id" in text
    assert "GITHUB_APP_PRIVATE_KEY=$(cat" in text or 'GITHUB_APP_PRIVATE_KEY="$(<' in text
    assert "VAULT_ADDR:?" in text and "vault token lookup" in text


def test_script_names_match_the_contract():
    text = SCRIPT.read_text()
    for must in ("KEY=implementer-github-app", "bound_service_account_names=eso-implementer-mcp",
                 "bound_service_account_namespaces=kagent", "vault policy write implementer-mcp",
                 "auth/kubernetes/role/implementer-mcp", "GITHUB_APP_ID=", "GITHUB_APP_INSTALLATION_ID="):
        assert must in text, must
```
Run: `python3 -m pytest tests/implementer/ -q`
Expected: FAIL (`FileNotFoundError` reading the script).

- [ ] **Step 2: Write the script**

Create `scripts/provision-implementer-vault.sh` (mode 755):
```bash
#!/usr/bin/env bash
# Implementer agent — Vault provisioning for its GitHub App (idempotent, safe to re-run).
#   k8s-secrets/implementer-github-app   GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID,
#                                        GITHUB_APP_PRIVATE_KEY (PEM) — the env var names the
#                                        GitHub MCP server reads (docs/github-app-auth.md)
#   policy implementer-mcp               read k8s-secrets/data/implementer-github-app
#   role   implementer-mcp               kubernetes auth: SA eso-implementer-mcp in kagent, ttl 1h
#
# The private key never passes on a command line: it is read from a file you downloaded from
# the App's settings page and should delete afterwards. Ids are not secret and are flags.
#   kubectl port-forward -n vault svc/vault 8200:8200 &
#   export VAULT_ADDR=http://127.0.0.1:8200
#   vault login -method=oidc            # Dex; or export VAULT_TOKEN=<token>
#   scripts/provision-implementer-vault.sh --app-id 123456 --installation-id 7891011 --key-file ~/Downloads/arigsela-implementer.pem
# Re-running with only --key-file rotates the key and leaves the ids untouched.
set -euo pipefail
MOUNT=k8s-secrets
KEY=implementer-github-app
app_id=""; installation_id=""; key_file=""
while (($#)); do
  case "$1" in
    --app-id) app_id="$2"; shift 2 ;;
    --installation-id) installation_id="$2"; shift 2 ;;
    --key-file) key_file="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
: "${VAULT_ADDR:?set VAULT_ADDR (kubectl port-forward -n vault svc/vault 8200:8200; http://127.0.0.1:8200)}"
if ! vault token lookup >/dev/null 2>&1; then
  echo "not logged in to Vault: run 'vault login -method=oidc' or export VAULT_TOKEN" >&2
  exit 2
fi
args=()
[[ -n "$app_id" ]] && args+=("GITHUB_APP_ID=$app_id")
[[ -n "$installation_id" ]] && args+=("GITHUB_APP_INSTALLATION_ID=$installation_id")
if [[ -n "$key_file" ]]; then
  [[ -r "$key_file" ]] || { echo "cannot read $key_file" >&2; exit 2; }
  grep -q "BEGIN RSA PRIVATE KEY\|BEGIN PRIVATE KEY" "$key_file" || { echo "$key_file is not a PEM private key" >&2; exit 2; }
  args+=("GITHUB_APP_PRIVATE_KEY=$(cat "$key_file")")
fi
if ((${#args[@]})); then
  if vault kv get -mount="$MOUNT" "$KEY" >/dev/null 2>&1; then
    vault kv patch -mount="$MOUNT" "$KEY" "${args[@]}" >/dev/null
  else
    vault kv put -mount="$MOUNT" "$KEY" "${args[@]}" >/dev/null
  fi
fi
vault policy write implementer-mcp - <<POLICY
path "$MOUNT/data/$KEY" { capabilities = ["read"] }
POLICY
vault write auth/kubernetes/role/implementer-mcp \
  bound_service_account_names=eso-implementer-mcp \
  bound_service_account_namespaces=kagent \
  policies=implementer-mcp ttl=1h >/dev/null
present=$(vault kv get -mount="$MOUNT" -format=json "$KEY" 2>/dev/null | python3 -c 'import json,sys; print(",".join(sorted(json.load(sys.stdin)["data"]["data"])))' 2>/dev/null || echo none)
echo "implementer-mcp: policy and role written; properties set this run: ${#args[@]}; present now: $present"
```
Note: `vault kv patch` with a multi-line value is passed as one argv element, so the PEM survives; `vault` quotes it. (`args+=("GITHUB_APP_PRIVATE_KEY=$(cat "$key_file")")` is the line the test looks for.)

- [ ] **Step 3: Run the tests**

Run: `python3 -m pytest tests/implementer/ -q`
Expected: 2 passed.

- [ ] **Step 4: Commit and open the PR; then the owner step**

```bash
git checkout main && git pull && git checkout -b feat/implementer-vault
git add scripts/provision-implementer-vault.sh tests/implementer
git commit -m "feat(implementer): Vault provisioning for the implementer GitHub App (key from a file, ids as flags)"
```
PR → green → merge. **STOP for the owner:** create GitHub App `arigsela-implementer` (homepage `https://github.com/arigsela/kubernetes`, webhook off; permissions Contents RW, Pull requests RW, Metadata R, Checks R; **no Workflows**), install it on `arigsela/kubernetes`, note the App ID (settings page) and the installation ID (the number in the installation URL `.../settings/installations/<id>`), generate one private key, then with Vault port-forwarded and logged in:
```
scripts/provision-implementer-vault.sh --app-id <id> --installation-id <id> --key-file <downloaded .pem>
```
Delete the .pem. Expected output ends with `present now: GITHUB_APP_ID,GITHUB_APP_INSTALLATION_ID,GITHUB_APP_PRIVATE_KEY`. Task 3 does not merge before this.

---

### Task 3: The implementer MCP server, its secret, and the RemoteMCPServer

**Files:**
- Create: `base-apps/kagent/implementer-mcp.yaml`, `base-apps/kagent/implementer-mcp-remote.yaml`, `base-apps/kagent/implementer-mcp-secret-store.yaml`, `base-apps/kagent/eso-implementer-mcp-serviceaccount.yaml`, `base-apps/kagent/implementer-mcp-external-secret.yaml`
- Test: `tests/implementer/test_mcp_server.py`
- Modify: `.github/workflows/validate.yaml` (new job `implementer-validate` after `eval-gate-validate`), `AGENTS.md:36` (add `implementer` to the no-extra-needs suites)

**Interfaces:**
- Consumes: Task 2's Vault names.
- Produces: `RemoteMCPServer` `implementer` in `kagent` (URL `http://implementer-mcp.kagent:3000/mcp`), tools exactly `IMPLEMENTER_READ_TOOLS` and `IMPLEMENTER_PROPOSE_TOOLS` as defined in `tests/implementer/conftest.py` (Task 4 imports them).

- [ ] **Step 1: Failing manifest tests**

Add to `tests/implementer/conftest.py`:
```python
import yaml

IMPLEMENTER_READ_TOOLS = ["get_file_contents", "search_code", "list_commits", "get_commit",
                          "list_branches", "list_pull_requests", "pull_request_read"]
IMPLEMENTER_PROPOSE_TOOLS = ["create_branch", "create_or_update_file", "push_files",
                             "create_pull_request", "update_pull_request"]
NEVER_LOADED = ["merge_pull_request", "update_pull_request_branch", "pull_request_review_write",
                "add_comment_to_pending_review", "delete_file", "create_repository",
                "delete_repository", "fork_repository", "create_pull_request_with_copilot"]
# Tool names the github-mcp-server v1.14.0 README lists for the repos and pull_requests
# toolsets. A name outside this list fails the server at startup (README, "Tool
# Configuration"), which would leave the agent silently without tools.
KNOWN_SERVER_TOOLS = {
    "add_comment_to_pending_review", "add_reply_to_pull_request_comment", "create_pull_request",
    "list_pull_requests", "merge_pull_request", "pull_request_read", "pull_request_review_write",
    "search_pull_requests", "update_pull_request", "update_pull_request_branch",
    "create_branch", "create_or_update_file", "create_repository", "delete_file",
    "delete_repository", "fork_repository", "get_commit", "get_file_contents",
    "get_latest_release", "get_release_by_tag", "get_tag", "list_branches", "list_commits",
    "list_releases", "list_repository_collaborators", "list_tags", "push_files", "search_code",
    "search_commits", "search_repositories",
}


def docs(rel):
    return [d for d in yaml.safe_load_all((REPO / rel).read_text()) if d]


def one(rel, kind, name=None):
    found = [d for d in docs(rel) if d.get("kind") == kind and (name is None or d["metadata"]["name"] == name)]
    assert len(found) == 1, (rel, kind, name)
    return found[0]


def taxonomy():
    cm = one("base-apps/admission-policies/agent-capability-taxonomy.yaml", "ConfigMap")
    import json
    return {k: json.loads(cm["data"][k]) for k in ("read", "propose", "write", "destructive")}
```
Create `tests/implementer/test_mcp_server.py`:
```python
# tests/implementer/test_mcp_server.py
"""The implementer MCP server loads exactly the tools the taxonomy's propose list plus the
named read tools, under GitHub App auth, and nothing that merges, reviews or deletes."""
from conftest import (IMPLEMENTER_PROPOSE_TOOLS, IMPLEMENTER_READ_TOOLS, KNOWN_SERVER_TOOLS,
                      NEVER_LOADED, REPO, one, taxonomy)

MCP = "base-apps/kagent/implementer-mcp.yaml"


def loaded_tools() -> list[str]:
    args = one(MCP, "MCPServer", "implementer-mcp")["spec"]["deployment"]["args"]
    return args[args.index("--tools") + 1].split(",")


def test_server_is_the_pinned_github_mcp_server_in_stdio_mode():
    spec = one(MCP, "MCPServer", "implementer-mcp")["spec"]
    assert spec["transportType"] == "stdio"
    dep = spec["deployment"]
    assert dep["image"].startswith("ghcr.io/github/github-mcp-server:v1.14.0@sha256:")
    assert dep["cmd"] == "./github-mcp-server" and dep["args"][0] == "stdio"
    assert "--read-only" not in dep["args"] and "--toolsets" not in dep["args"]


def test_server_authenticates_as_the_github_app_from_the_vault_backed_secret():
    dep = one(MCP, "MCPServer", "implementer-mcp")["spec"]["deployment"]
    assert [s["name"] for s in dep["secretRefs"]] == ["implementer-github-app"]
    es = one("base-apps/kagent/implementer-mcp-external-secret.yaml", "ExternalSecret", "implementer-github-app")
    assert es["spec"]["target"]["name"] == "implementer-github-app"
    assert es["spec"]["dataFrom"] == [{"extract": {"key": "implementer-github-app"}}]
    assert es["spec"]["secretStoreRef"] == {"name": "vault-implementer-mcp", "kind": "SecretStore"}
    store = one("base-apps/kagent/implementer-mcp-secret-store.yaml", "SecretStore", "vault-implementer-mcp")
    auth = store["spec"]["provider"]["vault"]["auth"]["kubernetes"]
    assert auth["role"] == "implementer-mcp" and auth["serviceAccountRef"]["name"] == "eso-implementer-mcp"
    sa = one("base-apps/kagent/eso-implementer-mcp-serviceaccount.yaml", "ServiceAccount", "eso-implementer-mcp")
    assert sa["metadata"]["namespace"] == "kagent"


def test_loaded_tools_are_exactly_the_contract():
    assert loaded_tools() == IMPLEMENTER_READ_TOOLS + IMPLEMENTER_PROPOSE_TOOLS


def test_propose_tools_loaded_equal_the_taxonomy_propose_list():
    assert set(IMPLEMENTER_PROPOSE_TOOLS) == set(taxonomy()["propose"])
    assert set(IMPLEMENTER_READ_TOOLS) <= set(taxonomy()["read"])


def test_nothing_that_merges_reviews_or_deletes_is_loaded():
    text = (REPO / MCP).read_text()
    for name in NEVER_LOADED:
        assert name not in text, name


def test_every_loaded_tool_name_is_a_known_server_tool():
    unknown = set(loaded_tools()) - KNOWN_SERVER_TOOLS
    assert not unknown, f"a misspelt tool name fails the server at startup: {sorted(unknown)}"


def test_remote_registers_the_server():
    remote = one("base-apps/kagent/implementer-mcp-remote.yaml", "RemoteMCPServer", "implementer")
    assert remote["spec"]["url"] == "http://implementer-mcp.kagent:3000/mcp"
    assert remote["spec"]["protocol"] == "STREAMABLE_HTTP"
```
Run: `python3 -m pytest tests/implementer/ -q`
Expected: the new tests FAIL (`FileNotFoundError`).

- [ ] **Step 2: Write the manifests**

`base-apps/kagent/implementer-mcp.yaml`:
```yaml
# The implementer's GitHub tool server (§T.116, §V.94): a second instance of the official GitHub
# MCP server, beside the read-only agent-docs-mcp, authenticating as GitHub App
# `arigsela-implementer` (installed on this repo only; Contents + Pull requests read/write,
# Metadata + Checks read, NO Workflows permission, so GitHub itself refuses any change under
# .github/workflows). In stdio mode the server mints and refreshes installation tokens itself
# from GITHUB_APP_ID / GITHUB_APP_INSTALLATION_ID / GITHUB_APP_PRIVATE_KEY (envFrom the
# Vault-backed Secret implementer-github-app; scripts/provision-implementer-vault.sh).
#
# `--tools` is the whole surface: the named read tools and exactly the taxonomy's `propose`
# list. No toolset is enabled, so merge_pull_request, the review tools, delete_file and the
# repository tools never exist here (tests/implementer/test_mcp_server.py). A misspelt name
# fails the server at startup, by design.
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: implementer-mcp
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/name: implementer-mcp
spec:
  transportType: stdio
  deployment:
    image: ghcr.io/github/github-mcp-server:v1.14.0@sha256:7aaeeec9ae4fe9a736d100c1ff0798f3c219b5009e05f5d3945fcacb13cc196b
    cmd: ./github-mcp-server
    args:
      - stdio
      - --tools
      - get_file_contents,search_code,list_commits,get_commit,list_branches,list_pull_requests,pull_request_read,create_branch,create_or_update_file,push_files,create_pull_request,update_pull_request
    secretRefs:
      - name: implementer-github-app
    resources:
      requests:
        cpu: 50m
        memory: 64Mi
      limits:
        cpu: 200m
        memory: 256Mi
    nodeSelector:
      node.kubernetes.io/workload: infrastructure
    tolerations:
      - key: node-role.kubernetes.io/control-plane
        effect: NoSchedule
```
`base-apps/kagent/implementer-mcp-remote.yaml`:
```yaml
# Registers the implementer tool server with kagent so AgentTemplates can bind its tools by
# name (the kmcp MCPServer only deploys it). Same shape as agent-docs-mcp-remote.yaml.
apiVersion: api.kagent.dev/v1alpha3
kind: RemoteMCPServer
metadata:
  name: implementer
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/name: implementer
spec:
  description: GitHub MCP for the implementer agent (arigsela/kubernetes, branches and pull requests under its own App)
  protocol: STREAMABLE_HTTP
  url: http://implementer-mcp.kagent:3000/mcp
  timeout: 30s
  sseReadTimeout: 5m0s
  terminateOnClose: true
```
`base-apps/kagent/implementer-mcp-secret-store.yaml`:
```yaml
# Per-consumer SecretStore (templates/agent-identity/README.md): Vault role implementer-mcp
# reads only k8s-secrets/implementer-github-app. The role must exist BEFORE this merges (a
# SecretStore with no role is Degraded and fails the whole kagent-secrets sync):
# scripts/provision-implementer-vault.sh writes it.
apiVersion: external-secrets.io/v1
kind: SecretStore
metadata:
  name: vault-implementer-mcp
  namespace: kagent
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "implementer-mcp"
          serviceAccountRef:
            name: "eso-implementer-mcp"
```
`base-apps/kagent/eso-implementer-mcp-serviceaccount.yaml`:
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: eso-implementer-mcp
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    arigsela.com/idp-managed: "true"
```
`base-apps/kagent/implementer-mcp-external-secret.yaml`:
```yaml
# Secret implementer-github-app: the three env vars the GitHub MCP server reads for App auth,
# every property of the Vault key, by name (dataFrom.extract: a property added in Vault lands
# here with no manifest change, and a renamed one is noticed by the server failing to start).
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: implementer-github-app
  namespace: kagent
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-implementer-mcp
    kind: SecretStore
  target:
    name: implementer-github-app
    creationPolicy: Owner
  dataFrom:
    - extract:
        key: implementer-github-app
```

- [ ] **Step 3: Validate**

Run:
```
python3 -m pytest tests/implementer/ -q
yamllint -c .yamllint.yaml base-apps/kagent/implementer-mcp*.yaml base-apps/kagent/eso-implementer-mcp-serviceaccount.yaml
kubeconform -summary -strict -ignore-missing-schemas -schema-location default -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' -kubernetes-version 1.33.0 base-apps/kagent/implementer-mcp*.yaml base-apps/kagent/eso-implementer-mcp-serviceaccount.yaml
python3 scripts/validate-agent-identity.py --repo-root . && python3 scripts/validate-agent-capability.py --repo-root .
```
Expected: tests pass; lint and schema clean; both validators exit 0 (an MCPServer and a RemoteMCPServer are known kinds; the SecretStore is scoped).

- [ ] **Step 4: CI job and suite table**

In `.github/workflows/validate.yaml`, after the `eval-gate-validate` job insert:
```yaml
  implementer-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
      - name: Set up Python
        uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install pyyaml==6.0.2 pytest==8.3.3
      - name: Run implementer tests
        run: python -m pytest tests/implementer/ -q
```
In `AGENTS.md` line 36 add `` `implementer` `` after `` `identity` ``.

- [ ] **Step 5: Commit, PR, merge after the owner step, verify live**

```bash
git checkout main && git pull && git checkout -b feat/implementer-mcp
git add base-apps/kagent/implementer-mcp.yaml base-apps/kagent/implementer-mcp-remote.yaml base-apps/kagent/implementer-mcp-secret-store.yaml base-apps/kagent/eso-implementer-mcp-serviceaccount.yaml base-apps/kagent/implementer-mcp-external-secret.yaml tests/implementer .github/workflows/validate.yaml AGENTS.md
git commit -m "feat(implementer): GitHub MCP server under the implementer App, explicit tool list, Vault-backed secret"
```
PR (`review:read`, it touches `.github/`). Merge only after the owner confirms the Vault key exists. Then:
```
kubectl -n kagent get externalsecret implementer-github-app   # SecretSynced
kubectl -n kagent get remotemcpserver implementer -o jsonpath='{.status.discoveredTools[*].name}'
```
Expected: exactly the 12 names (order may differ). If the pod crashloops, `kubectl -n kagent logs deploy/implementer-mcp` names the bad tool or the missing env var.

---

### Task 4: The `implementer` agent, and the promotion pin

**Files:**
- Create: `base-apps/kagent/agent-templates/implementer.yaml`, `base-apps/kagent/agents/implementer.yaml`
- Test: `tests/implementer/test_agent.py`, `tests/promotion/test_promote.py` (one test appended)
- Modify: `base-apps/kagent/docs.md` frontmatter `sources` (add the seven new files), the "Agents in Git" table and the "Tools via MCP servers" table (one row each)

**Interfaces:**
- Consumes: `IMPLEMENTER_READ_TOOLS`, `IMPLEMENTER_PROPOSE_TOOLS` from `tests/implementer/conftest.py`; RemoteMCPServer `implementer` (Task 3).
- Produces: AgentTemplate `implementer` (class `propose`), Agent `implementer` (Harness `kagent-stateless`), reachable as `kagent/implementer` over A2A. Task 5 routes to it.

- [ ] **Step 1: Failing tests**

Create `tests/implementer/test_agent.py`:
```python
# tests/implementer/test_agent.py
"""The implementer template: class propose, the contract tools and nothing else, and a prompt
whose load-bearing lines are pinned (the prompt IS the change contract; §T.116)."""
from conftest import IMPLEMENTER_PROPOSE_TOOLS, IMPLEMENTER_READ_TOOLS, NEVER_LOADED, one

TEMPLATE = "base-apps/kagent/agent-templates/implementer.yaml"
K8S_READ = ["k8s_get_resources", "k8s_get_resource_yaml", "k8s_get_pod_logs", "k8s_describe_resource",
            "k8s_get_events", "k8s_get_cluster_configuration", "k8s_get_available_api_resources",
            "k8s_check_service_connectivity"]


def template():
    return one(TEMPLATE, "AgentTemplate", "implementer")


def bindings():
    return {(b["mcp"]["server"]["name"], b["mcp"].get("requireApproval")): b["mcp"]["tools"]
            for b in template()["spec"]["tools"] if "mcp" in b}


def prompt() -> str:
    return template()["spec"]["systemPrompt"]


def test_class_model_and_harness():
    t = template()
    assert t["metadata"]["labels"]["capability.homelab/class"] == "propose"
    assert t["spec"]["modelConfig"]["name"] == "anthropic-claude-sonnet-5-5"
    agent = one("base-apps/kagent/agents/implementer.yaml", "Agent", "implementer")
    assert agent["spec"] == {"templateRef": {"name": "implementer"}, "harnessRef": {"name": "kagent-stateless"}}


def test_bindings_are_the_contract_and_no_subagents():
    b = bindings()
    assert b[("implementer", False)] == IMPLEMENTER_READ_TOOLS + IMPLEMENTER_PROPOSE_TOOLS
    assert b[("agent-docs", False)] == ["get_file_contents", "search_code"]
    assert b[("kagent-tool-server", False)] == K8S_READ
    assert set(b) == {("implementer", False), ("agent-docs", False), ("kagent-tool-server", False)}
    assert not [x for x in template()["spec"]["tools"] if "subAgent" in x]
    assert not any(n in sum(b.values(), []) for n in NEVER_LOADED)


def test_prompt_pins_the_deny_list_and_the_file_cap():
    p = prompt()
    for path in (".github/", "terraform/", ".claude/", "CLAUDE.md", "AGENTS.md", "templates/", "SPEC.md",
                 "external-secret", "secret-store"):
        assert path in p, path
    assert "at most three files" in p


def test_prompt_never_merges_and_never_opens_a_second_pr():
    p = prompt()
    assert "Never merge" in p and "never close, review or comment" in p
    assert "already exists" in p and "update that branch" in p


def test_prompt_fixes_the_branch_slug():
    p = prompt()
    assert "agent/<slug>" in p and "[a-z0-9-]" in p


def test_prompt_covers_chart_rendered_apps():
    assert "valuesObject" in prompt()


def test_prompt_handles_a_fix_already_in_git():
    assert "already differs from the live pod" in prompt()


def test_prompt_says_ci_is_the_validation_and_quotes_evidence():
    p = prompt()
    assert "validation: CI" in p and "never quote values that look like" in p.lower()
```
Append to `tests/promotion/test_promote.py`:
```python
def test_implementer_app_pr_is_never_auto(tmp_path=None):
    """§V.95: an agent-authored PR has no promotion class, however green and however small."""
    app_pr = pr(user={"login": "arigsela-implementer[bot]"}, labels=[{"name": "review:skip"}])
    d = pm.decide(app_pr, files("docs/platform/promotion.md"), checks("success"), policy())
    assert d.klass == "owner" and any("author" in r for r in d.reasons)
    d = pm.decide(app_pr, files("base-apps/donetick/deployment.yaml"), checks("success"), policy())
    assert d.klass == "owner"
```
Run: `python3 -m pytest tests/implementer/ -q -k agent; python3 -m pytest tests/promotion/ -q -k implementer`
Expected: implementer tests FAIL (no file); the promotion test passes immediately (no class admits that author) — keep it: it is the pin for V95, and it fails the day a class is widened.

- [ ] **Step 2: Write the AgentTemplate**

Create `base-apps/kagent/agent-templates/implementer.yaml`:
```yaml
# implementer — the agent that changes this repository, under its own identity (§T.116, §V.94,
# §V.95). WHAT it is: Sonnet 5.5, class `propose`: it binds the implementer GitHub tools (read +
# the taxonomy's five proposal tools, ungated: the merge is the human gate), the agent-docs read
# tools and k8s-reader's eight read tools, so it can investigate before it proposes. HOW it runs:
# ../agents/implementer.yaml -> ../harnesses/kagent-stateless.yaml (no memory: every task starts
# from the repository and the request). No sub-agents. It never merges, closes, reviews or
# comments on a pull request; the server does not even load those tools.
#
# Two callers: a human (`scripts/ask-hk --agent kagent/implementer "..."`, the session owned by
# their Dex identity) and the dispatcher (base-apps/dispatcher/dispatch-table.yaml, owned by
# system:serviceaccount:dispatcher:dispatcher). The system prompt is the change contract and
# tests/implementer/test_agent.py pins its load-bearing lines.
apiVersion: api.kagent.dev/v1alpha3
kind: AgentTemplate
metadata:
  name: implementer
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/managed-by: kagent
    app.kubernetes.io/name: implementer
    app: kagent
    arigsela.com/idp-managed: "true"
    # propose: read tools plus branches and pull requests on arigsela/kubernetes, nothing on
    # the cluster. Checked at admission (agent-capability) and in CI.
    capability.homelab/class: propose
spec:
  description: Changes the arigsela/kubernetes repository through a pull request under the implementer GitHub App. Investigates with read tools first; a human merges.
  modelConfig:
    name: anthropic-claude-sonnet-5-5
  systemPrompt: |
    You are the implementer for the homelab GitOps repository arigsela/kubernetes. You change
    the repository only through a pull request opened under your own GitHub App identity, and a
    human merges it. You never change the cluster: your Kubernetes tools are read-only.

    ## Before you change a number
    Read the app's `base-apps/<app>/docs.md` and the comments next to the value in the manifest:
    this repository keeps its rationale in the manifests. Most apps are Helm charts: the value
    you need often lives in the Application's `valuesObject` in `base-apps/<app>.yaml`, not in a
    Deployment manifest. Edit where the value is declared; never add a manifest that duplicates
    what a chart renders. Use `get_file_contents` on `main` and `search_code` to find it.

    ## The change contract
    - Branch `agent/<slug>` from `main`, where `<slug>` is `[a-z0-9-]` only: lowercase the
      alert id or the request's key words, replace every other character with `-`, at most 60
      characters. If a branch named `agent/<slug>` or an open pull request from it already
      exists, update that branch with `create_or_update_file` (use the file's current `sha`)
      and report that pull request's link; never open a second pull request for the same slug.
    - One commit per pull request, at most three files. Conventional Commits title with a
      scope, for example `fix(n8n): raise the memory limit to 768Mi (Container near memory limit, 2026-10-14)`.
    - Never touch `.github/`, `terraform/`, `.claude/`, `CLAUDE.md`, `AGENTS.md`, `templates/`,
      `SPEC.md`, any `external-secret*.yaml` or `secret-store.yaml`, or any file that holds a
      credential reference. If the fix would need one of those, open no pull request and say why.
    - The pull request body: the evidence you saw, quoted as lines (never quote values that look
      like tokens, keys or passwords; name the line instead), what changed and why, the line
      `validation: CI` (the repository's checks are the validation), and the alert id or the
      requester's words. Add a comment next to the number you changed naming the alert and the
      date, so the next reader knows why it moved.
    - Never merge a pull request; never close, review or comment on one. Report the link and stop.

    ## Resource alerts (the dispatcher's prompts name the alert)
    Gather: describe the pod, its last state and restart count, its events, the last 100 log
    lines, the manifest in git, and the app's docs. Decide:
    - "limit too low": steady use near the limit, no OOMKilled, no restart growth (for CPU:
      throttled with steady demand). Raise the limit (or request) by half, rounded to a clean
      value (256Mi→384Mi, 512Mi→768Mi, 1Gi→1536Mi, 500m→750m), keeping the request-to-limit ratio.
    - "leak or contention": growth over time, OOMKilled, or node pressure. Open no pull request;
      answer with the evidence and the sentence `no pull request: <reason>`.
    - If the value in git already differs from the live pod's (a fix is rolling out), open no
      pull request and say so.

    ## Answering
    End with the pull request link, or with `no pull request: <reason>`. Keep the answer short;
    the pull request body carries the detail.
  tools:
    # The implementer GitHub server: the only propose-class binding on the platform. Ungated by
    # design (§V.94): the merge is the gate. Order matches tests/implementer/conftest.py.
    - mcp:
        server:
          kind: RemoteMCPServer
          name: implementer
        requireApproval: false
        tools:
          - get_file_contents
          - search_code
          - list_commits
          - get_commit
          - list_branches
          - list_pull_requests
          - pull_request_read
          - create_branch
          - create_or_update_file
          - push_files
          - create_pull_request
          - update_pull_request
    # The read-only GitHub server the knowledge agent uses, for the same repo under the
    # read-only token: cheap reads that do not spend the App's rate limit.
    - mcp:
        server:
          kind: RemoteMCPServer
          name: agent-docs
        requireApproval: false
        tools:
          - get_file_contents
          - search_code
    # Exactly k8s-reader's eight read tools: the evidence for a resource alert.
    - mcp:
        server:
          kind: RemoteMCPServer
          name: kagent-tool-server
        requireApproval: false
        tools:
          - k8s_get_resources
          - k8s_get_resource_yaml
          - k8s_get_pod_logs
          - k8s_describe_resource
          - k8s_get_events
          - k8s_get_cluster_configuration
          - k8s_get_available_api_resources
          - k8s_check_service_connectivity
```
Create `base-apps/kagent/agents/implementer.yaml` (copy `agents/homelab-knowledge.yaml`, replace every `homelab-knowledge` with `implementer`, and `harnessRef.name: kagent-stateless`).

- [ ] **Step 3: Validate**

Run:
```
python3 -m pytest tests/implementer/ -q
python3 scripts/validate-agent-capability.py --repo-root . && python3 scripts/validate-agent-identity.py --repo-root .
python3 -m pytest tests/agent-capability/ -q && python3 -m pytest tests/agent-identity/ -q && python3 -m pytest tests/dispatch/ -q
python3 -m pytest tests/admission-policies/ -q -k real_kagent
yamllint -c .yamllint.yaml base-apps/kagent/agent-templates/implementer.yaml base-apps/kagent/agents/implementer.yaml
```
Expected: all green; the real-manifests admission test admits the new template (class propose, ungated propose tools).

- [ ] **Step 4: Docs rows**

In `base-apps/kagent/docs.md`: add to `sources` the five Task 3 files and the two Task 4 files; add to "Agents in Git": `| \`implementer\` | \`anthropic-claude-sonnet-5-5\` | \`kagent-stateless\` | propose | \`implementer\` (7 read + the 5 propose tools, ungated: the merge is the gate), \`agent-docs\`, and the 8 read tools of \`kagent-tool-server\`. Opens PRs under GitHub App \`arigsela-implementer\`; called by a human (ask-hk) or the dispatcher |`; add to "Tools via MCP servers": `| \`implementer\` (\`implementer-mcp-remote.yaml\`) | \`http://implementer-mcp.kagent:3000/mcp\`, a second GitHub MCP server (\`implementer-mcp.yaml\`) under GitHub App auth (Secret \`implementer-github-app\`, Vault \`implementer-github-app\`), \`--tools\` naming 12 tools and no toolset; merge, review and delete tools are never loaded | the 7 read and 5 propose tools listed in \`tests/implementer/conftest.py\` |`. Change the sentence "a `read` template binds only read tools, and every write/destructive tool must sit in a binding with `requireApproval: true`" to add "a `propose` template may also bind the five proposal tools (branches and pull requests) ungated".
Run: `python3 scripts/validate-agent-docs.py --repo-root . && python3 scripts/gen-okf.py --repo-root . --check && python3 scripts/gen-techdocs.py --repo-root . --check` (regenerate without `--check` if they report drift, and include the outputs).

- [ ] **Step 5: Commit, PR, merge, acceptance (a)**

```bash
git checkout main && git pull && git checkout -b feat/implementer-agent
git add base-apps/kagent/agent-templates/implementer.yaml base-apps/kagent/agents/implementer.yaml base-apps/kagent/docs.md tests/implementer tests/promotion/test_promote.py base-apps/index.md docs/index.md base-apps/kagent/mkdocs.yml
git commit -m "feat(implementer): the implementer AgentTemplate (class propose) and Agent; promotion never admits its PRs"
```
PR → green → merge. The eval gate runs (new agent surface; no corpus → nothing scored). Then:
```
kubectl -n kagent get agent implementer            # Ready
scripts/ask-hk --agent kagent/implementer "In base-apps/kagent/agent-templates/implementer.yaml, add one line to the header comment: '# Acceptance 2026-10-14: first pull request opened by the implementer itself.' Open the pull request."
```
Expected: an answer ending in a PR link; `gh pr view <n> --json author,labels` shows author `app/arigsela-implementer` and a triage label; `gh pr checks` runs; the promotion workflow comments `owner`. The owner merges or closes it.

---

### Task 5: Dispatch the two resource alerts to the implementer, with a PR cap

**Files:**
- Modify: `scripts/dispatch.py` (table validation, `Config`, `Dispatcher.decide`, new `OpenPRs` counter)
- Modify: `base-apps/dispatcher/dispatch-table.yaml` (limits, two rows), `base-apps/dispatcher/deployment.yaml` (env `IMPLEMENTER_LOGIN`; checksums regenerated)
- Regenerate: `base-apps/dispatcher/code-configmap.yaml` (+ Deployment checksums)
- Test: `tests/dispatch/test_dispatch.py`, `tests/dispatch/test_table.py`
- Modify: `base-apps/dispatcher/docs.md` ("What it is not", "The table"), `base-apps/dispatcher/runbook.md` (replay expectation)

**Interfaces:**
- Consumes: AgentTemplate `implementer` (Task 4); taxonomy `propose` (Task 1).
- Produces: table `limits.open_prs_max` (int ≥ 0, default 3 when absent); rule key `proposes: true`; decision `skip-open-prs`; `Config.implementer_login` (env `IMPLEMENTER_LOGIN`, default `arigsela-implementer[bot]`), `Config.github_repo` (env `GITHUB_REPO`, default `arigsela/kubernetes`); `OpenPRs(repo, login, fetch, clock, ttl=300).count() -> int | None`.

- [ ] **Step 1: Failing dispatcher tests**

Append to `tests/dispatch/test_dispatch.py`:
```python
# ---------------------------------------------------------------- open-PR cap (§T.117)

PROPOSE_TABLE = {
    "version": 1,
    "limits": {"investigations_per_hour": 4, "repeat_after_hours": 6, "open_prs_max": 2},
    "rules": [{"id": "mem", "source": "grafana", "match": {"alertname": "Container near memory limit"},
               "route": "agent:implementer", "proposes": True, "prompt": "fix {labels[pod]}", "why": "w"}],
}


def mem_alert(fp="fp-1"):
    return {"status": "firing", "fingerprint": fp,
            "labels": {"alertname": "Container near memory limit", "namespace": "n8n", "pod": "n8n-0"}}


def make_propose(fake, count):
    cfg = dp.Config(table_path="/dev/null", a2a_base="http://a2a:8083", n8n_base="http://n8n:5678/webhook",
                    user="dispatcher", timeout=5.0, ui_url="https://ui/agents/{session}/chat")
    counter = dp.OpenPRs("arigsela/kubernetes", "arigsela-implementer[bot]", fetch=count, clock=lambda: 0.0)
    return dp.Dispatcher(dp.load_table(json.dumps(PROPOSE_TABLE)), cfg, send=fake.send, post=fake.post,
                         log=fake.log, run_async=False, open_prs=counter)


def test_open_prs_max_defaults_to_three_and_proposes_is_validated():
    t = dp.load_table(json.dumps({**PROPOSE_TABLE, "limits": {"investigations_per_hour": 1, "repeat_after_hours": 1}}))
    assert t["limits"]["open_prs_max"] == 3
    with pytest.raises(dp.TableError, match="proposes"):
        bad = json.loads(json.dumps(PROPOSE_TABLE)); bad["rules"][0]["proposes"] = "yes"
        dp.load_table(json.dumps(bad))


def test_below_the_cap_a_proposing_rule_is_investigated():
    fake = Fake(reply=completed_task(text="https://github.com/arigsela/kubernetes/pull/900"))
    d = make_propose(fake, count=lambda: [{"n": 1}])
    assert [x["decision"] for x in d.handle_grafana({"alerts": [mem_alert()]})] == ["investigate"]
    assert len(fake.sent) == 1


def test_at_the_cap_a_proposing_rule_is_skipped_with_a_reason():
    fake = Fake(reply=completed_task())
    d = make_propose(fake, count=lambda: [{"n": 1}, {"n": 2}])
    decisions = d.handle_grafana({"alerts": [mem_alert()]})
    assert [x["decision"] for x in decisions] == ["skip-open-prs"]
    assert decisions[0]["open_prs"] == 2 and not fake.sent


def test_open_prs_count_failure_routes_and_logs():
    def boom():
        raise OSError("rate limited")
    fake = Fake(reply=completed_task())
    d = make_propose(fake, count=boom)
    decisions = d.handle_grafana({"alerts": [mem_alert()]})
    assert [x["decision"] for x in decisions] == ["investigate"]
    assert decisions[0]["open_prs"] is None
    assert any(x.get("event") == "open-prs" and "rate limited" in x.get("error", "") for x in fake.logged)


def test_non_proposing_rule_never_counts_prs():
    calls = []
    fake = Fake(reply=completed_task())
    d = make(fake)
    d.open_prs = dp.OpenPRs("r", "l", fetch=lambda: calls.append(1) or [], clock=lambda: 0.0)
    d.handle_grafana({"alerts": [alert()]})
    assert calls == []


def test_open_prs_counter_caches_for_its_ttl():
    calls = []
    now = [0.0]
    c = dp.OpenPRs("r", "l", fetch=lambda: calls.append(1) or [{"n": 1}], clock=lambda: now[0], ttl=300)
    assert c.count() == 1 and c.count() == 1 and len(calls) == 1
    now[0] = 301.0
    assert c.count() == 1 and len(calls) == 2


def test_open_prs_fetch_filters_by_author_login():
    seen = {}
    def opener(req, timeout):
        seen["url"] = req.full_url
        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps([{"user": {"login": "arigsela-implementer[bot]"}},
                                               {"user": {"login": "arigsela"}}]).encode()
        return R()
    items = dp.fetch_open_prs("arigsela/kubernetes", "arigsela-implementer[bot]", opener=opener)
    assert len(items) == 1 and "pulls?state=open&per_page=100" in seen["url"]


def test_config_reads_implementer_login_and_repo():
    c = dp.Config.from_env({})
    assert c.implementer_login == "arigsela-implementer[bot]" and c.github_repo == "arigsela/kubernetes"
```
Run: `python3 -m pytest tests/dispatch/test_dispatch.py -q -k "open_prs or proposing or implementer_login"`
Expected: FAIL (`AttributeError: OpenPRs`, `TypeError: open_prs`).

- [ ] **Step 2: Implement in `scripts/dispatch.py`**

In `load_table`, after the two existing limit checks:
```python
    limits.setdefault("open_prs_max", 3)
    if not isinstance(limits["open_prs_max"], int) or limits["open_prs_max"] < 0:
        raise TableError("limits.open_prs_max must be a non-negative integer")
    table["limits"] = limits
```
In the rule loop, after the prompt checks:
```python
        if "proposes" in rule and not isinstance(rule["proposes"], bool):
            raise TableError(f"rule {rid}: proposes must be true or false")
        if rule.get("proposes") and route == "none":
            raise TableError(f"rule {rid}: proposes needs an agent route")
```
After the `Budget` class:
```python
# ------------------------------------------------------------- the open-PR cap

def fetch_open_prs(repo: str, login: str, opener=urllib.request.urlopen, timeout: float = 10.0) -> list[dict]:
    """Open PRs on the public repo authored by `login`, unauthenticated (60 requests/hour is
    plenty at one call per five minutes)."""
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=100",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "dispatcher/1"})
    with opener(req, timeout=timeout) as resp:
        items = json.loads(resp.read() or b"[]")
    return [p for p in items if (p.get("user") or {}).get("login") == login]


class OpenPRs:
    """How many pull requests the implementer currently has open (§T.117, limits.open_prs_max).
    Cached for `ttl` seconds; a failed count returns None and the caller routes anyway: a
    read-only investigation never fails closed on a GitHub outage."""

    def __init__(self, repo: str, login: str, *, fetch=None, clock=time.time, ttl: float = 300.0):
        self.fetch = fetch or (lambda: fetch_open_prs(repo, login))
        self.clock, self.ttl = clock, ttl
        self._at, self._value = -1e18, 0
        self.last_error: str | None = None
        self._lock = threading.Lock()

    def count(self) -> int | None:
        with self._lock:
            now = self.clock()
            if now - self._at < self.ttl:
                return self._value
            try:
                self._value, self._at, self.last_error = len(self.fetch()), now, None
                return self._value
            except (OSError, ValueError) as err:
                self.last_error = str(err)[:200]
                return None
```
In `Config` add fields `implementer_login: str = "arigsela-implementer[bot]"` and `github_repo: str = "arigsela/kubernetes"`, and in `from_env`: `implementer_login=env.get("IMPLEMENTER_LOGIN", "arigsela-implementer[bot]")`, `github_repo=env.get("GITHUB_REPO", "arigsela/kubernetes")`.
In `Dispatcher.__init__` add parameter `open_prs: OpenPRs | None = None` and `self.open_prs = open_prs or OpenPRs(config.github_repo, config.implementer_login)`.
In `decide`, before the budget check:
```python
        if rule.get("proposes"):
            n = self.open_prs.count()
            base["open_prs"] = n
            if n is None:
                self.log({"event": "open-prs", "error": self.open_prs.last_error, "rule": rule["id"]})
            elif n >= self.table["limits"]["open_prs_max"]:
                return {**base, "decision": "skip-open-prs"}
```
Update the module docstring's decision list to include `skip-open-prs`.

Run: `python3 -m pytest tests/dispatch/test_dispatch.py -q`
Expected: all pass.

- [ ] **Step 3: Failing table tests (V87 amended)**

In `tests/dispatch/test_table.py` replace `read_tools()` and `test_agent_routes_are_read_only_templates` with:
```python
def tools_of(cls: str) -> set[str]:
    cm = yaml.safe_load(TAXONOMY.read_text())
    return set(json.loads(cm["data"][cls]))


def template_class(name: str) -> str:
    doc = yaml.safe_load((TEMPLATES / f"{name}.yaml").read_text())
    return doc["metadata"]["labels"]["capability.homelab/class"]


def test_agent_routes_are_read_or_propose_templates_with_no_gate():
    """§V.87 (amended 2026-10-14): read or propose tools only, never a requireApproval binding."""
    allowed = tools_of("read") | tools_of("propose")
    for row in load_table()["rules"]:
        if not row["route"].startswith("agent:"):
            continue
        name = row["route"].split(":", 1)[1]
        assert (TEMPLATES / f"{name}.yaml").exists(), f"{row['id']}: no AgentTemplate {name}"
        tools, gated = template_tools(name)
        assert tools, f"{name} binds no tools; nothing to investigate with"
        assert tools <= allowed, f"{name} binds write/destructive tools: {sorted(tools - allowed)}"
        assert not gated, f"{name} has a requireApproval binding; the dispatcher never answers approvals"


def test_proposes_flag_matches_the_template_class():
    """A row that can end in a pull request says so, and only those rows are capped."""
    for row in load_table()["rules"]:
        if not row["route"].startswith("agent:"):
            assert not row.get("proposes"), row["id"]
            continue
        name = row["route"].split(":", 1)[1]
        assert bool(row.get("proposes")) == (template_class(name) == "propose"), row["id"]


def test_table_caps_open_prs():
    assert load_table()["limits"]["open_prs_max"] >= 1


def test_resource_alerts_route_to_the_implementer():
    rows = {r["match"]["alertname"]: r for r in load_table()["rules"] if r["source"] == "grafana"}
    for title in ("Container near memory limit", "Pod CPU starved"):
        row = rows[title]
        assert row["route"] == "agent:implementer" and row["proposes"] is True
        p = prompt_of(row)
        assert "limit too low" in p and "no pull request" in p and "{labels[pod]}" in p
```
Run: `python3 -m pytest tests/dispatch/test_table.py -q`
Expected: the last three FAIL (no `open_prs_max`, rows route to k8s-reader).

- [ ] **Step 4: The table rows**

In `base-apps/dispatcher/dispatch-table.yaml`: header comment: change "may only name templates whose every tool is `read`" to "may only name templates whose every tool is `read` or `propose` in the capability taxonomy with no requireApproval binding (V87); a row whose agent may open a pull request carries `\"proposes\": true` and is skipped while the implementer has `open_prs_max` pull requests open". Limits: `{"investigations_per_hour": 4, "repeat_after_hours": 6, "open_prs_max": 3}`. Replace the two rows:
```json
        {
          "id": "pod-cpu-starved",
          "source": "grafana",
          "match": {"alertname": "Pod CPU starved"},
          "route": "agent:implementer",
          "proposes": true,
          "prompt": [
            "Grafana alert '{alertname}' is firing for pod {labels[namespace]}/{labels[pod]}: {annotations[summary]}",
            "Investigate with your read-only tools: describe the pod, read its recent events and the last 100 log lines,",
            "find where its CPU request and limit are declared in git, and decide between 'limit too low'",
            "(throttled with steady demand, no restarts) and 'contention' (node pressure, other pods).",
            "Only for 'limit too low': open one pull request raising the CPU limit (and request, same ratio) by half,",
            "rounded, with a comment naming this alert and today's date; branch agent/pod-cpu-starved-{labels[namespace]}-{labels[pod]}.",
            "Otherwise answer with the evidence and 'no pull request: <reason>'. Runbook hint: {annotations[runbook]}",
            "Never quote values that look like tokens, keys or passwords, even from logs or YAML; name the line instead."
          ],
          "why": "The investigation already said 'raise the limit' most of the time; raising it is one number in one file, which is exactly what a pull request a human merges is for (ADP path 8, the first unprompted write)."
        },
        {
          "id": "container-near-memory-limit",
          "source": "grafana",
          "match": {"alertname": "Container near memory limit"},
          "route": "agent:implementer",
          "proposes": true,
          "prompt": [
            "Grafana alert '{alertname}' is firing for {labels[namespace]}/{labels[pod]} container {labels[container]}: {annotations[summary]}",
            "Investigate with your read-only tools: describe the pod, check for OOMKilled in its last state and events,",
            "read the last 100 log lines, find where the container's memory limit is declared in git, and decide between",
            "'limit too low' (steady use near the limit, no OOMKilled, no restart growth) and 'leak' (growth over time or OOMKilled).",
            "Only for 'limit too low': open one pull request raising the memory limit (and request, same ratio) by half,",
            "rounded, with a comment naming this alert and today's date; branch agent/container-near-memory-limit-{labels[namespace]}-{labels[pod]}.",
            "Otherwise answer with the evidence and 'no pull request: <reason>'. Runbook hint: {annotations[runbook]}",
            "Never quote values that look like tokens, keys or passwords, even from logs or YAML; name the line instead."
          ],
          "why": "Memory pressure has two causes; only the 'limit too low' one is a number in a manifest, and that number becomes a pull request. A leak stays a finding."
        },
```
In `base-apps/dispatcher/deployment.yaml` add to the container env (next to `DISPATCH_USER`): `- name: IMPLEMENTER_LOGIN` / `value: "arigsela-implementer[bot]"` with a comment "the implementer App's login as GitHub reports PR authors; the open-PR cap counts its PRs (§T.117)".
Run: `python3 scripts/gen-dispatcher-configmap.py --repo-root . && python3 -m pytest tests/dispatch/ -q`
Expected: ConfigMap and both checksums rewritten; all dispatch tests pass (`test_manifests` checks the checksums; `test_gen_configmap` the generator).

- [ ] **Step 5: Docs**

`base-apps/dispatcher/docs.md`: in "What it is not", change the "Not an operator" bullet to "Not an operator. It may only route to agents whose every tool is `read` or `propose` in the capability taxonomy with no `requireApproval` binding (§V.87, amended 2026-10-14: a `propose` agent can open a pull request, never touch the cluster); `tests/dispatch/test_table.py` enforces it. It never answers an approval or a question (§V.88)." In "The table": add `open_prs_max` to the limits list and the sentence "A row with `proposes: true` routes to the implementer and is skipped (`skip-open-prs`) while that agent has `open_prs_max` pull requests open, counted unauthenticated from the public repo every five minutes; a failed count is logged (`event: open-prs`) and the alert is routed anyway." In "Observability" add `skip-open-prs` to the decision list. `runbook.md`: in the replay section add "For the two resource alerts the finding ends with a pull request link (or `no pull request: <reason>`); the PR is authored by `app/arigsela-implementer` and waits for you."

- [ ] **Step 6: Commit, PR, merge, acceptance (b)**

```bash
git checkout main && git pull && git checkout -b feat/remediate-on-signal
git add scripts/dispatch.py base-apps/dispatcher tests/dispatch
git commit -m "feat(dispatcher): the two resource alerts route to the implementer (propose), capped by open_prs_max (ADP path 8)"
```
PR → green → merge; Argo rolls the dispatcher (checksum change). Then replay with a real pod (pick one near its limit from the Cluster Overview dashboard, or any pod: the agent will answer `no pull request` with evidence, which is also a pass for the routing):
```
kubectl run -n dispatcher curl --rm -i --restart=Never --image=curlimages/curl:8.11.1 -- \
  curl -s -X POST http://dispatcher.dispatcher.svc.cluster.local:8080/signal/grafana \
  -H 'Content-Type: application/json' \
  -d '{"alerts":[{"status":"firing","fingerprint":"manual-mem-1","labels":{"alertname":"Container near memory limit","namespace":"<ns>","pod":"<pod>","container":"<container>"},"annotations":{"summary":"manual replay","runbook":"none"}}]}'
```
Expected: `{"decisions":["investigate"]}`; Loki/`kubectl -n dispatcher logs deploy/dispatcher` shows `event: dispatch … rule: container-near-memory-limit … open_prs: <n>` then `event: finding`; Slack carries the answer with either a PR link (author `app/arigsela-implementer`) or `no pull request: <reason>`. Record both the session id and the PR number for SPEC.

---

### Task 6: Ledger, brief and docs

**Files:**
- Modify: `SPEC.md` (V87 amended, V94, V95, T116, T117, owner row), `CLAUDE.md` (promotion paragraph: one sentence), `docs/presentations/whole-picture-presentation-brief.md` (as-of note; screen 09 rows 3 and 8; screen 10 level-3 line; screen 13 items 4 and 5), `base-apps/admission-policies/agent-capability-taxonomy.yaml` (nothing further), `base-apps/kagent/runbook.md` (one symptom), memory file.

**Interfaces:** consumes the PR numbers, session ids and acceptance outcomes of Tasks 4 and 5.

- [ ] **Step 1: SPEC rows**

Append after V93:
```
V94: taxonomy class `propose` = tools that change ONLY a branch | pull request on arigsela/kubernetes (⊥ cluster, ⊥ account, ⊥ merge): bindable w/o `requireApproval` (the merge is the gate); `read` templates ⊥ bind it; `propose` templates bind read ∪ propose; `MUST_APPROVE` stays {write, destructive}. implementer MCP server loads exactly taxonomy `propose` ∪ named read tools, ⊥ merge/review/delete tools. CI: tests/agent-capability, tests/implementer, admission fixtures.
V95: ⊥ PR authored by an in-cluster agent merges w/o a human: promotion classes admit only Renovate's app & the owner (`app/arigsela-implementer` → `owner`, tests/promotion); implementer binds ⊥ merge/review/comment tool; main ruleset requires a PR. GitHub App `arigsela-implementer`: Contents+PRs RW, Metadata+Checks R, ⊥ Workflows ∴ `.github/workflows` refused by GitHub itself.
```
Edit V87 to: `V87: dispatch table routes only → AgentTemplate whose ∀ bound tool ∈ taxonomy read ∪ propose (amended 2026-10-14) & ⊥ `requireApproval: true` binding; a row whose template is class propose carries `proposes: true` & is skipped at `limits.open_prs_max` open PRs (count failure ⇒ route + log, ⊥ fail closed); ∀ Grafana rule title has a row. CI: tests/dispatch/test_table.py.`
Append tasks (fill the PR numbers and ids from the acceptance runs):
```
T116|x|**DONE 2026-10-14:** implement inside the platform (ADP path 3, brief item 4): class `propose` (#…), Vault provisioning (#…), implementer MCP server under GitHub App `arigsela-implementer` (#…), AgentTemplate/Agent `implementer` (#…); acceptance: ask-hk → PR #… authored by `app/arigsela-implementer`, triage `review:…`, promotion `owner`. Substrate sandboxes have ⊥ egress at alpha11 ∴ the GitHub API is the workbench; CI is the validation. spec `docs/superpowers/specs/2026-10-14-implementer-and-remediate-design.md`|V94,V95
T117|x|**DONE 2026-10-14:** remediate on signal (ADP path 8, brief item 5): rows `pod-cpu-starved` + `container-near-memory-limit` → `agent:implementer` w/ `proposes: true`, `open_prs_max: 3` (#…); acceptance: replay `Container near memory limit` for <ns>/<pod> → session <id> → <PR #… | no pull request: reason>. leak/contention stays a finding; gated operator call = follow-up|V87,V94,V95
```

- [ ] **Step 2: CLAUDE.md and the kagent runbook**

In `CLAUDE.md`'s Promotion bullet, after "Nothing else merges without the owner." add: "That includes the in-cluster `implementer` agent's PRs (author `app/arigsela-implementer`): they are triaged and checked like any other and wait for you."
In `base-apps/kagent/runbook.md` add after the agent-docs 401 symptom:
```
### Symptom: the implementer says it cannot open a pull request (GitHub 401/404 from `implementer-mcp`)
The App token is minted from the Secret `implementer-github-app` (Vault `implementer-github-app`).
- **Check:** `kubectl -n kagent logs deploy/implementer-mcp --tail=50`: `401` = App ID or private key wrong; `404` = installation ID wrong or the App is not installed on the repo; a startup error naming a tool = a misspelt `--tools` entry.
- **Fix (owner):** re-run `scripts/provision-implementer-vault.sh` with the right `--app-id`, `--installation-id` or a new `--key-file`, then `kubectl -n kagent annotate externalsecret implementer-github-app force-sync=$(date +%s) --overwrite` and `kubectl -n kagent rollout restart deploy/implementer-mcp`.
- **A PR it opened cannot change `.github/workflows`**: by design (no Workflows permission); the agent reports it and opens nothing.
```

- [ ] **Step 3: The brief**

In `docs/presentations/whole-picture-presentation-brief.md`:
- "As of" paragraph: append "On 2026-10-14 items 4 and 5 landed: an in-cluster implementer opens pull requests under its own GitHub App (path 3 BUILT), and the two resource alerts become pull requests when the evidence says the limit is too low (path 8 BUILT); the footer on screen 09 reads 'Eight built, none partial'."
- Screen 09 row 3: "A coding agent at the owner's desk writes most commits under the contract; in the cluster, the `implementer` agent edits a manifest on a branch and opens the pull request under its own GitHub App, through the same triage, checks and policy. The runtime's sandboxes still cannot reach GitHub, so the API is the workbench and CI is the sandbox." State BUILT. Row 8: "Two operator agents with every mutating tool behind approval; and since 2026-10-14 the dispatcher's resource alerts end in a pull request raising the limit when the evidence says it is too low (a leak stays a finding). The fix is proposed unprompted and merged by a human." State BUILT. Footer: "Eight built, none partial".
- Screen 10 level 3 line: append "; an alert now becomes a pull request with no human in between, and a human merges it."
- Screen 13 rows 4 and 5: state `BUILT 2026-10-14`, "why" text: 4: "Done without the sandbox: Substrate sandboxes have no egress, so the agent writes through the GitHub API under a fourth capability class, `propose` (branches and pull requests only, ungated; the merge is the gate)."; 5: "Done for the two resource alerts; the leak case and the gated operator call are the next rows."
Run: `python3 scripts/gen-techdocs.py --repo-root . --check` (docs/ is not generated; nothing to regenerate) and `python3 -m pytest tests/techdocs/ -q`.

- [ ] **Step 4: Commit, PR (docs-only self-merges), memory**

```bash
git checkout main && git pull && git checkout -b docs/implementer-and-remediate-done
git add SPEC.md CLAUDE.md base-apps/kagent/runbook.md docs/presentations/whole-picture-presentation-brief.md
git commit -m "docs: implementer and remediate-on-signal recorded (V87 amended, V94, V95, T116, T117); brief rescored"
```
Note: this PR touches `CLAUDE.md` and `SPEC.md` (`SPEC.md` is `*.md` but `CLAUDE.md` is denied for docs-only) so it is `owner`-merged. Then write memory `implementer-agent.md` (how to call it, the propose class, the open-PR cap, the owner's App) and link it in `MEMORY.md`.
