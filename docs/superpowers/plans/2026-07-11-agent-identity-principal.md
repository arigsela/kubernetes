# Agent Identity — "Agent Principal" Pattern (increment 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a reusable, GitOps-native "agent-principal" pattern for kagent agents (identity = credentials it can obtain + its model + its capability surface), enforced by a Python validator, and proven end-to-end on the `homelab-knowledge` + `agent-docs-mcp` slice.

**Architecture:** A convention + validator approach, no new CRD. A contract doc in `templates/agent-identity/` defines three invariants; `scripts/validate-agent-identity.py` asserts them by reading the manifests that already exist (`Agent`, `ModelConfig`, credential `ExternalSecret`/`SecretStore`). Real least-privilege teeth land on the ExternalSecret→SecretStore→Vault boundary (a dedicated ESO ServiceAccount + scoped Vault role/path for the pilot credential), because declarative kagent agents cannot carry their own pod ServiceAccount. Model identity is made structural by pulling `homelab-knowledge`'s out-of-band `ModelConfig` into git.

**Tech Stack:** Python 3.12 stdlib + PyYAML for the validator, pytest for its tests, GitHub Actions for CI, kagent CRDs (`Agent` v1alpha2, `ModelConfig` v1alpha2, `MCPServer`/`RemoteMCPServer`), External Secrets Operator (`external-secrets.io/v1beta1` `SecretStore`/`ExternalSecret`), HashiCorp Vault (KV v2 at mount `k8s-secrets`, Kubernetes auth).

## Global Constraints

- **Branch:** work happens on `docs/agent-identity-principal` (already checked out). Do not commit to `main`. Leave the unrelated in-flight working-tree changes (`.gitignore`, `.judge/rubric.md`, `AGENTS.md`) untouched — `git add` only the files each task names.
- **No workload behavior change except one credential's source path.** The only behavior-relevant change is where the `agent-docs-mcp` GitHub token is read from in Vault. No workload image, ingress, RBAC, or NetworkPolicy change. Adopting the `ModelConfig` into git must be a no-op (git content matches the live object).
- **Pilot bar vs. backlog.** The validator hard-fails only on the pilot credential (`agent-docs-github-mcp-token`) and the pilot agent (`homelab-knowledge`); every other unscoped consumer or unresolved reference is a **warning**, not an error, so the gate is green while the backlog stays visible.
- **Vault writes are operator-run.** The Vault path/policy/role changes are executed by the operator holding the root token (never by an implementer). Ordering is **add-before-remove**: create the new Vault path + policy + role FIRST, then apply the git `ExternalSecret`/`SecretStore` change, verify, and narrow the old broad policy LAST.
- **Do not merge the PR until Vault is prepped.** This is a GitOps repo (Argo CD auto-syncs `main`). Merging the credential-scoping change before the operator has created the new Vault path/role would break the `agent-docs-mcp` token sync. See "Deployment & cutover" at the end.
- **Exact kagent namespace:** all new runtime resources live in namespace `kagent`.
- **Commit style:** Conventional Commits, imperative subject (e.g. `feat(agent-identity): ...`, `docs(agent-identity): ...`). One PR for the whole increment.
- **Kyverno enforcement is out of scope** (deferred follow-on C).

---

## File Structure

**Created:**
- `templates/agent-identity/README.md` — the agent-principal contract: the three invariants + an onboarding checklist.
- `templates/agent-identity/serviceaccount.yaml` — copyable ESO ServiceAccount template for a scoped credential.
- `templates/agent-identity/secretstore.yaml` — copyable scoped `SecretStore` template.
- `scripts/validate-agent-identity.py` — the validator (pure functions + CLI).
- `scripts/agent-identity-scope.txt` — the pilot classification (which credential/agent are held to the hard bar).
- `tests/agent-identity/test_validate_agent_identity.py` — pytest coverage for the validator.
- `base-apps/kagent/model-configs/anthropic-claude-sonnet-4-6.yaml` — `homelab-knowledge`'s ModelConfig, adopted into git.
- `base-apps/kagent/eso-agent-docs-mcp-serviceaccount.yaml` — the dedicated ESO ServiceAccount `eso-agent-docs-mcp`.
- `base-apps/kagent/agent-docs-mcp-secret-store.yaml` — the scoped `SecretStore` `vault-agent-docs-mcp`.

**Modified:**
- `base-apps/kagent/agent-docs-mcp-external-secret.yaml` — point at the scoped store + per-consumer Vault key/path.
- `.github/workflows/validate.yaml` — add an `agent-identity-validate` job.
- `base-apps/kagent/docs.md` — add the new manifests to `sources:` and one sentence on the identity scoping (hygiene; keeps the agent-docs contract honest).

---

## Task 1: Contract templates + scope file

**Files:**
- Create: `templates/agent-identity/README.md`
- Create: `templates/agent-identity/serviceaccount.yaml`
- Create: `templates/agent-identity/secretstore.yaml`
- Create: `scripts/agent-identity-scope.txt`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `scripts/agent-identity-scope.txt` in the exact format Task 2's `load_scope()` parses — lines `pilot-credential: <ExternalSecret name>` and `pilot-agent: <Agent name>`, `#` comments and blanks ignored.

- [ ] **Step 1: Create the contract README**

Create `templates/agent-identity/README.md`:

```markdown
# Agent-Identity Contract ("Agent Principal")

A kagent agent's identity is its **agent-principal**:

> agent-principal = (the credentials it can obtain) + (its model) + (its capability surface)

Because the kagent `Agent` CRD's `spec.declarative.deployment` has no
`serviceAccountName`, declarative agents cannot carry a distinct pod
ServiceAccount. Identity is therefore expressed at the boundaries we control:
the credential boundary (ExternalSecret → SecretStore → Vault role), the model
boundary (ModelConfig), and the capability boundary (`Agent.spec.declarative.tools`).

## The three invariants

1. **Scoped credentials.** Every `ExternalSecret` that materializes a real
   credential in the `kagent` namespace resolves through a *dedicated,
   path-scoped* `SecretStore` — its own ESO ServiceAccount + a Vault role that
   reads only that secret's Vault path — not the shared broad `vault-backend`
   store, and not the monolithic `kagent` Vault key.
2. **In-git model identity.** Every agent's `modelConfig` (and
   `memory.modelConfig`) references a `ModelConfig` that exists as a manifest in
   git. (Chart-generated configs such as `default-model-config` are exempt.)
3. **Declared capability surface.** Every `Agent.spec.declarative.tools`
   `McpServer` ref names an MCP server that exists in git and lists non-empty
   `toolNames` (no implicit bind-all).

The validator (`scripts/validate-agent-identity.py`) enforces these. Enforcement
is staged: the pilot credential and pilot agent named in
`scripts/agent-identity-scope.txt` are hard failures; every other unscoped
consumer or unresolved reference is a warning (visible backlog).

## Onboard a new agent identity-correctly

1. **Credential:** for each real credential the agent (or its MCP server) needs,
   give it its own Vault path (`k8s-secrets/<consumer>`), a Vault policy reading
   only that path, a Kubernetes-auth role bound to a dedicated ESO
   ServiceAccount, and a `SecretStore` using that SA/role (copy
   `serviceaccount.yaml` + `secretstore.yaml`). Point the `ExternalSecret` at
   that store and key. Never read the monolithic `kagent` key for a new
   consumer.
2. **Model:** reference a `ModelConfig` that lives in
   `base-apps/kagent/model-configs/` (or the chart's `default-model-config`).
   Never depend on an out-of-band, hand-applied ModelConfig.
3. **Capability:** list explicit `toolNames` for every `McpServer` tool ref.

## Follow-ons (not yet enforced)

- Kyverno admission policy enforcing invariants 1 & 2 at deploy time.
- Scoping the remaining credentials (Backstage token, DB creds, MCP basic-auth,
  Plex/qBit) and onboarding the other declarative agents.
- Dedicated per-agent Anthropic keys / budget caps; egress control.
```

- [ ] **Step 2: Create the ESO ServiceAccount template**

Create `templates/agent-identity/serviceaccount.yaml`:

```yaml
# Dedicated ESO ServiceAccount for one scoped credential. Copy to
# base-apps/kagent/<consumer>-serviceaccount.yaml and rename. The Vault
# Kubernetes-auth role for this credential is bound to THIS ServiceAccount, so
# only its SecretStore can assume the role.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: eso-REPLACE_ME          # e.g. eso-agent-docs-mcp
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    arigsela.com/idp-managed: "true"
```

- [ ] **Step 3: Create the scoped SecretStore template**

Create `templates/agent-identity/secretstore.yaml`:

```yaml
# Path-scoped SecretStore for one credential. Copy to
# base-apps/kagent/<consumer>-secret-store.yaml. The Vault role reads only this
# consumer's path; the serviceAccountRef is the dedicated ESO ServiceAccount.
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-REPLACE_ME        # e.g. vault-agent-docs-mcp
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
          role: "REPLACE_ME"    # dedicated Vault role, reads only this path
          serviceAccountRef:
            name: "eso-REPLACE_ME"
```

- [ ] **Step 4: Create the scope file**

Create `scripts/agent-identity-scope.txt`:

```text
# agent-identity validator scope. Lines are "key: value"; # and blanks ignored.
# pilot-credential: an ExternalSecret name that MUST be scoped (hard fail).
# pilot-agent: an Agent name held to the model + capability invariants (hard fail).
# Everything else is warn-only (visible backlog) this increment.
pilot-credential: agent-docs-github-mcp-token
pilot-agent: homelab-knowledge
```

- [ ] **Step 5: Commit**

```bash
git add templates/agent-identity scripts/agent-identity-scope.txt
git commit -m "docs(agent-identity): add agent-principal contract, templates, and scope file"
```

---

## Task 2: The validator (TDD)

**Files:**
- Create: `scripts/validate-agent-identity.py`
- Create: `tests/agent-identity/test_validate_agent_identity.py`
- Test: `tests/agent-identity/test_validate_agent_identity.py`

**Interfaces:**
- Consumes: `scripts/agent-identity-scope.txt` (Task 1).
- Produces a module (imported by path — the filename has hyphens) exposing:
  - `load_scope(repo_root: Path) -> tuple[set[str], set[str]]` — `(pilot_secrets, pilot_agents)`.
  - `collect_by_kind(repo_root: Path) -> dict[str, list[tuple[Path, dict]]]` — all YAML docs under `base-apps/kagent/`, grouped by `kind`.
  - `collect_agents(repo_root: Path) -> list[tuple[Path, dict]]` — `kind: Agent` docs under `base-apps/kagent/agents/` plus `base-apps/kagent/build-orchestrator.yaml`.
  - `check_credential_scoping(repo_root: Path, pilot_secrets: set[str]) -> tuple[list[str], list[str]]` — `(errors, warnings)`.
  - `check_model_config_in_git(repo_root: Path, pilot_agents: set[str]) -> tuple[list[str], list[str]]`.
  - `check_capability_surface(repo_root: Path, pilot_agents: set[str]) -> tuple[list[str], list[str]]`.
  - `main(argv=None) -> int` — exit 0 on pass, 1 on any error; warnings print but never fail.

- [ ] **Step 1: Write the failing tests**

Create `tests/agent-identity/test_validate_agent_identity.py`:

```python
from pathlib import Path
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "validate_agent_identity",
    Path(__file__).resolve().parents[2] / "scripts" / "validate-agent-identity.py",
)
vai = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vai)


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _scope(root: Path):
    _write(root / "scripts" / "agent-identity-scope.txt",
           "pilot-credential: agent-docs-github-mcp-token\n"
           "pilot-agent: homelab-knowledge\n")


def _external_secret(name: str, store: str, key: str) -> str:
    return (
        "apiVersion: external-secrets.io/v1beta1\n"
        "kind: ExternalSecret\n"
        f"metadata:\n  name: {name}\n  namespace: kagent\n"
        "spec:\n"
        f"  secretStoreRef:\n    name: {store}\n    kind: SecretStore\n"
        "  data:\n"
        "    - secretKey: TOKEN\n"
        f"      remoteRef:\n        key: {key}\n        property: p\n"
    )


def _agent(name: str, model: str, mcp_ref: str, tool_names: list[str]) -> str:
    tn = "".join(f"        - {t}\n" for t in tool_names)
    tools = (
        "    tools:\n"
        "    - type: McpServer\n"
        "      mcpServer:\n"
        "        apiGroup: kagent.dev\n"
        "        kind: RemoteMCPServer\n"
        f"        name: {mcp_ref}\n"
        + ("        toolNames:\n" + tn if tool_names else "")
    )
    return (
        "apiVersion: kagent.dev/v1alpha2\n"
        "kind: Agent\n"
        f"metadata:\n  name: {name}\n  namespace: kagent\n"
        "spec:\n  type: Declarative\n  declarative:\n"
        f"    modelConfig: {model}\n"
        "    memory:\n      modelConfig: embedding-model-config\n"
        + tools
    )


def _remote_mcp(name: str) -> str:
    return (
        "apiVersion: kagent.dev/v1alpha2\n"
        "kind: RemoteMCPServer\n"
        f"metadata:\n  name: {name}\n  namespace: kagent\n"
        "spec:\n  url: http://example\n"
    )


def _model_config(name: str) -> str:
    return (
        "apiVersion: kagent.dev/v1alpha2\n"
        "kind: ModelConfig\n"
        f"metadata:\n  name: {name}\n  namespace: kagent\n"
        "spec:\n  provider: Anthropic\n  model: x\n"
    )


def _good_repo(tmp_path: Path) -> Path:
    root = tmp_path
    kd = root / "base-apps" / "kagent"
    _scope(root)
    _write(kd / "agent-docs-mcp-external-secret.yaml",
           _external_secret("agent-docs-github-mcp-token", "vault-agent-docs-mcp",
                            "kagent-agent-docs-mcp"))
    _write(kd / "agent-docs-mcp-remote.yaml", _remote_mcp("agent-docs"))
    _write(kd / "model-configs" / "anthropic-claude-sonnet-4-6.yaml",
           _model_config("anthropic-claude-sonnet-4-6"))
    _write(kd / "embedding-model-config.yaml", _model_config("embedding-model-config"))
    _write(kd / "agents" / "homelab-knowledge.yaml",
           _agent("homelab-knowledge", "anthropic-claude-sonnet-4-6",
                  "agent-docs", ["get_file_contents"]))
    return root


def test_load_scope(tmp_path):
    _scope(tmp_path)
    secrets, agents = vai.load_scope(tmp_path)
    assert secrets == {"agent-docs-github-mcp-token"}
    assert agents == {"homelab-knowledge"}


def test_good_repo_passes_all(tmp_path):
    root = _good_repo(tmp_path)
    secrets, agents = vai.load_scope(root)
    assert vai.check_credential_scoping(root, secrets) == ([], [])
    assert vai.check_model_config_in_git(root, agents) == ([], [])
    assert vai.check_capability_surface(root, agents) == ([], [])
    assert vai.main(["--repo-root", str(root)]) == 0


def test_pilot_unscoped_credential_is_error(tmp_path):
    root = _good_repo(tmp_path)
    # Regress the pilot ExternalSecret back to the broad store + monolithic key.
    _write(root / "base-apps" / "kagent" / "agent-docs-mcp-external-secret.yaml",
           _external_secret("agent-docs-github-mcp-token", "vault-backend", "kagent"))
    secrets, _ = vai.load_scope(root)
    errors, warnings = vai.check_credential_scoping(root, secrets)
    assert any("agent-docs-github-mcp-token" in e for e in errors)
    assert vai.main(["--repo-root", str(root)]) == 1


def test_nonpilot_unscoped_credential_is_warning(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "db-external-secret.yaml",
           _external_secret("kagent-db-credentials", "vault-backend", "kagent"))
    secrets, _ = vai.load_scope(root)
    errors, warnings = vai.check_credential_scoping(root, secrets)
    assert errors == []
    assert any("kagent-db-credentials" in w for w in warnings)


def test_pilot_missing_modelconfig_is_error(tmp_path):
    root = _good_repo(tmp_path)
    (root / "base-apps" / "kagent" / "model-configs"
     / "anthropic-claude-sonnet-4-6.yaml").unlink()
    _, agents = vai.load_scope(root)
    errors, _ = vai.check_model_config_in_git(root, agents)
    assert any("anthropic-claude-sonnet-4-6" in e for e in errors)


def test_chart_generated_modelconfig_is_exempt(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "skill-suggester.yaml",
           _agent("skill-suggester", "default-model-config", "agent-docs",
                  ["get_file_contents"]))
    _, agents = vai.load_scope(root)
    errors, warnings = vai.check_model_config_in_git(root, agents)
    # skill-suggester is not a pilot agent and default-model-config is exempt:
    assert errors == []
    assert not any("default-model-config" in w for w in warnings)


def test_pilot_empty_toolnames_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "homelab-knowledge.yaml",
           _agent("homelab-knowledge", "anthropic-claude-sonnet-4-6", "agent-docs", []))
    _, agents = vai.load_scope(root)
    errors, _ = vai.check_capability_surface(root, agents)
    assert any("toolNames" in e for e in errors)


def test_pilot_unknown_mcp_ref_is_error(tmp_path):
    root = _good_repo(tmp_path)
    _write(root / "base-apps" / "kagent" / "agents" / "homelab-knowledge.yaml",
           _agent("homelab-knowledge", "anthropic-claude-sonnet-4-6",
                  "does-not-exist", ["t"]))
    _, agents = vai.load_scope(root)
    errors, _ = vai.check_capability_surface(root, agents)
    assert any("does-not-exist" in e for e in errors)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pip install pyyaml==6.0.2 pytest==8.3.3 && python -m pytest tests/agent-identity/ -v`
Expected: FAIL — `scripts/validate-agent-identity.py` does not exist yet (import error at collection).

- [ ] **Step 3: Write the validator**

Create `scripts/validate-agent-identity.py`:

```python
#!/usr/bin/env python3
"""Validate the agent-identity ("agent principal") contract for kagent agents.

An agent-principal = (credentials it can obtain) + (its model) + (its capability
surface). Three invariants (see templates/agent-identity/README.md):
  1. Scoped credentials: pilot credential ExternalSecrets resolve through a
     dedicated scoped SecretStore + per-consumer Vault key (not 'vault-backend'
     / the monolithic 'kagent' key). Non-pilot unscoped credentials warn.
  2. In-git model identity: modelConfig/memory.modelConfig referenced by
     in-scope agents resolve to a ModelConfig manifest in git (chart-generated
     configs exempt). Pilot agent = hard fail; others warn.
  3. Capability surface: each Agent McpServer tool ref resolves to an in-git MCP
     server and lists non-empty toolNames. Pilot agent = hard fail; others warn.

Exit 1 on any hard error; warnings print but do not fail.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

BROAD_SECRETSTORE = "vault-backend"
MONOLITHIC_VAULT_KEY = "kagent"
CHART_GENERATED_MODELCONFIGS = {"default-model-config"}


def _load_docs(path: Path) -> list[dict]:
    try:
        return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
    except yaml.YAMLError:
        return []


def collect_by_kind(repo_root: Path) -> dict[str, list[tuple[Path, dict]]]:
    out: dict[str, list[tuple[Path, dict]]] = {}
    kagent_dir = repo_root / "base-apps" / "kagent"
    if not kagent_dir.is_dir():
        return out
    for path in sorted(kagent_dir.rglob("*.yaml")):
        for doc in _load_docs(path):
            kind = doc.get("kind")
            if kind:
                out.setdefault(kind, []).append((path, doc))
    return out


def collect_agents(repo_root: Path) -> list[tuple[Path, dict]]:
    kagent_dir = repo_root / "base-apps" / "kagent"
    paths = sorted((kagent_dir / "agents").glob("*.yaml"))
    extra = kagent_dir / "build-orchestrator.yaml"
    if extra.is_file():
        paths.append(extra)
    agents: list[tuple[Path, dict]] = []
    for path in paths:
        for doc in _load_docs(path):
            if doc.get("kind") == "Agent":
                agents.append((path, doc))
    return agents


def load_scope(repo_root: Path) -> tuple[set[str], set[str]]:
    path = repo_root / "scripts" / "agent-identity-scope.txt"
    pilot_secrets: set[str] = set()
    pilot_agents: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key, val = key.strip(), val.strip()
        if key == "pilot-credential":
            pilot_secrets.add(val)
        elif key == "pilot-agent":
            pilot_agents.add(val)
    return pilot_secrets, pilot_agents


def check_credential_scoping(repo_root: Path, pilot_secrets: set[str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for _path, doc in collect_by_kind(repo_root).get("ExternalSecret", []):
        name = (doc.get("metadata") or {}).get("name")
        spec = doc.get("spec") or {}
        store = (spec.get("secretStoreRef") or {}).get("name")
        keys = {
            (ref.get("remoteRef") or {}).get("key")
            for ref in (spec.get("data") or [])
        }
        scoped = store != BROAD_SECRETSTORE and MONOLITHIC_VAULT_KEY not in keys
        if scoped:
            continue
        detail = (f"{name}: credential is not scoped (store={store!r}, "
                  f"keys={sorted(k for k in keys if k)})")
        if name in pilot_secrets:
            errors.append(detail + " — pilot credential must use a dedicated "
                          "SecretStore and a per-consumer Vault key")
        else:
            warnings.append(detail + " — backlog for a later identity increment")
    return errors, warnings


def _agent_model_refs(doc: dict) -> list[str]:
    decl = (doc.get("spec") or {}).get("declarative") or {}
    refs = []
    if decl.get("modelConfig"):
        refs.append(decl["modelConfig"])
    mem = decl.get("memory") or {}
    if mem.get("modelConfig"):
        refs.append(mem["modelConfig"])
    return refs


def check_model_config_in_git(repo_root: Path, pilot_agents: set[str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    in_git = {
        (doc.get("metadata") or {}).get("name")
        for _path, doc in collect_by_kind(repo_root).get("ModelConfig", [])
    }
    for _path, doc in collect_agents(repo_root):
        name = (doc.get("metadata") or {}).get("name")
        for ref in _agent_model_refs(doc):
            if ref in in_git or ref in CHART_GENERATED_MODELCONFIGS:
                continue
            detail = f"{name}: modelConfig {ref!r} is not a ModelConfig manifest in git"
            if name in pilot_agents:
                errors.append(detail)
            else:
                warnings.append(detail + " — backlog")
    return errors, warnings


def check_capability_surface(repo_root: Path, pilot_agents: set[str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    by_kind = collect_by_kind(repo_root)
    mcp_names = {
        (doc.get("metadata") or {}).get("name")
        for kind in ("RemoteMCPServer", "MCPServer")
        for _path, doc in by_kind.get(kind, [])
    }
    for _path, doc in collect_agents(repo_root):
        name = (doc.get("metadata") or {}).get("name")
        decl = (doc.get("spec") or {}).get("declarative") or {}
        for tool in decl.get("tools") or []:
            if tool.get("type") != "McpServer":
                continue
            mcp = tool.get("mcpServer") or {}
            ref = mcp.get("name")
            tool_names = mcp.get("toolNames") or []
            problems = []
            if ref not in mcp_names:
                problems.append(f"references unknown MCP server {ref!r}")
            if not tool_names:
                problems.append(f"MCP ref {ref!r} lists no toolNames (binds nothing)")
            for problem in problems:
                detail = f"{name}: {problem}"
                if name in pilot_agents:
                    errors.append(detail)
                else:
                    warnings.append(detail + " — backlog")
    return errors, warnings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate the agent-identity contract.")
    parser.add_argument("--repo-root", default=".", type=Path)
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    pilot_secrets, pilot_agents = load_scope(repo_root)
    errors: list[str] = []
    warnings: list[str] = []
    for check, arg in (
        (check_credential_scoping, pilot_secrets),
        (check_model_config_in_git, pilot_agents),
        (check_capability_surface, pilot_agents),
    ):
        e, w = check(repo_root, arg)
        errors.extend(e)
        warnings.extend(w)

    for w in warnings:
        print(f"WARN: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if errors:
        print(f"\nagent-identity validation FAILED with {len(errors)} error(s).")
        return 1
    print(f"\nagent-identity validation passed ({len(warnings)} warning(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/agent-identity/ -v`
Expected: PASS (all tests green).

- [ ] **Step 5: Run the validator against the real repo (document the expected pilot failures)**

Run: `python scripts/validate-agent-identity.py --repo-root .; echo "exit=$?"`
Expected: `exit=1` with exactly two pilot ERRORs — the `agent-docs-github-mcp-token` credential is still unscoped (fixed in Task 4) and `homelab-knowledge`'s `anthropic-claude-sonnet-4-6` ModelConfig is not yet in git (fixed in Task 3). Non-pilot unscoped credentials appear as WARN. This is the expected starting state; it proves the gate bites. Do not "fix" it here.

- [ ] **Step 6: Commit**

```bash
git add scripts/validate-agent-identity.py tests/agent-identity/
git commit -m "feat(agent-identity): add agent-principal validator with pytest coverage"
```

---

## Task 3: Adopt homelab-knowledge's ModelConfig into git (model identity)

**Files:**
- Create: `base-apps/kagent/model-configs/anthropic-claude-sonnet-4-6.yaml`

**Interfaces:**
- Consumes: the validator (Task 2). After this task, `check_model_config_in_git(root, {"homelab-knowledge"})` returns `([], [])`.
- Produces: an in-git `ModelConfig` named `anthropic-claude-sonnet-4-6` in namespace `kagent`.

**Context:** `homelab-knowledge` references `modelConfig: anthropic-claude-sonnet-4-6`, which exists **live but not in git** (hand-applied). Adopting it must be a **no-op** — the git content must match the live object so Argo CD sync does not mutate it. `apiKeySecretRef` stays `kagent-anthropic` (the existing shared key; "structural only", no billing change).

- [ ] **Step 1: Capture the live object as the source of truth (authoritative)**

If you have cluster access, capture the live ModelConfig and strip runtime fields — this is the authoritative content:

```bash
kubectl -n kagent get modelconfig anthropic-claude-sonnet-4-6 -o yaml \
  | yq 'del(.metadata.resourceVersion, .metadata.uid, .metadata.generation,
            .metadata.creationTimestamp, .metadata.managedFields,
            .metadata.annotations."kubectl.kubernetes.io/last-applied-configuration",
            .status)'
```

If you do **not** have cluster access, do not guess the schema: mark this task **BLOCKED** and ask the operator to paste the stripped live YAML. The expected shape (for cross-check only) mirrors `base-apps/kagent/embedding-model-config.yaml` and the Anthropic provider block in `base-apps/kagent.yaml`:

```yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: anthropic-claude-sonnet-4-6
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    arigsela.com/idp-managed: "true"
spec:
  provider: Anthropic
  model: claude-sonnet-4-6          # CONFIRM exact string against live before commit
  apiKeySecretRef: kagent-anthropic
  apiKeySecretKey: ANTHROPIC_API_KEY
```

- [ ] **Step 2: Write the manifest**

Write the captured/confirmed content to `base-apps/kagent/model-configs/anthropic-claude-sonnet-4-6.yaml`. The `spec` fields (`provider`, `model`, `apiKeySecretRef`, `apiKeySecretKey`, and any nesting) MUST match live exactly. `apiKeySecretRef` MUST be `kagent-anthropic`.

- [ ] **Step 3: Lint the manifest**

Run: `yamllint -c .yamllint.yaml base-apps/kagent/model-configs/anthropic-claude-sonnet-4-6.yaml`
Expected: no errors.

- [ ] **Step 4: Schema-validate the manifest**

Run: `kubeconform -summary -strict -ignore-missing-schemas base-apps/kagent/model-configs/anthropic-claude-sonnet-4-6.yaml`
Expected: valid (the `ModelConfig` CRD has no bundled schema, so it is reported skipped/ignored, not failed).

- [ ] **Step 5: Confirm invariant 2 now passes for the pilot**

Run: `python scripts/validate-agent-identity.py --repo-root . 2>&1 | grep -c "anthropic-claude-sonnet-4-6.*not a ModelConfig" || echo 0`
Expected: `0` — no "not a ModelConfig manifest in git" error for the pilot. (The credential-scoping ERROR from Task 2 Step 5 remains until Task 4.)

- [ ] **Step 6: Commit**

```bash
git add base-apps/kagent/model-configs/anthropic-claude-sonnet-4-6.yaml
git commit -m "feat(agent-identity): adopt homelab-knowledge ModelConfig into GitOps"
```

---

## Task 4: Credential-scope the agent-docs MCP slice

**Files:**
- Create: `base-apps/kagent/eso-agent-docs-mcp-serviceaccount.yaml`
- Create: `base-apps/kagent/agent-docs-mcp-secret-store.yaml`
- Modify: `base-apps/kagent/agent-docs-mcp-external-secret.yaml`
- Modify: `base-apps/kagent/docs.md` (add sources + one sentence)

**Interfaces:**
- Consumes: the validator (Task 2), the templates (Task 1).
- Produces: the pilot credential resolving through `(eso-agent-docs-mcp SA + kagent-agent-docs-mcp Vault role + kagent-agent-docs-mcp path + vault-agent-docs-mcp SecretStore)`. After this task **and** the operator Vault steps, `check_credential_scoping(root, {"agent-docs-github-mcp-token"})` returns `([], [...warnings])`.

**Current state (grounded):** `base-apps/kagent/agent-docs-mcp-external-secret.yaml` reads `key: kagent, property: agent-docs-github-token` via `secretStoreRef.name: vault-backend`, producing Secret `agent-docs-github-mcp-token` (env `GITHUB_PERSONAL_ACCESS_TOKEN`), consumed by the `MCPServer agent-docs-mcp` via `secretRefs`. The Secret **target name stays `agent-docs-github-mcp-token`** so the MCPServer's `secretRefs` keep resolving.

- [ ] **Step 1: Create the dedicated ESO ServiceAccount**

Create `base-apps/kagent/eso-agent-docs-mcp-serviceaccount.yaml`:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: eso-agent-docs-mcp
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    arigsela.com/idp-managed: "true"
```

- [ ] **Step 2: Create the scoped SecretStore**

Create `base-apps/kagent/agent-docs-mcp-secret-store.yaml`:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-agent-docs-mcp
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
          role: "kagent-agent-docs-mcp"
          serviceAccountRef:
            name: "eso-agent-docs-mcp"
```

- [ ] **Step 3: Repoint the ExternalSecret at the scoped store + per-consumer key**

Edit `base-apps/kagent/agent-docs-mcp-external-secret.yaml` to exactly:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: agent-docs-github-mcp-token
  namespace: kagent
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-agent-docs-mcp
    kind: SecretStore
  target:
    name: agent-docs-github-mcp-token
    creationPolicy: Owner
  data:
    - secretKey: GITHUB_PERSONAL_ACCESS_TOKEN
      remoteRef:
        key: kagent-agent-docs-mcp
        property: github-token
```

- [ ] **Step 4: Update kagent docs.md (hygiene)**

In `base-apps/kagent/docs.md`, add the two new files to the `sources:` list:

```
  - base-apps/kagent/eso-agent-docs-mcp-serviceaccount.yaml
  - base-apps/kagent/agent-docs-mcp-secret-store.yaml
  - base-apps/kagent/model-configs/anthropic-claude-sonnet-4-6.yaml
```

And add one sentence to the "Secrets & database" section:

```
The `agent-docs-mcp` GitHub token is credential-scoped per the agent-identity contract (`templates/agent-identity/README.md`): it resolves through a dedicated `SecretStore` (`vault-agent-docs-mcp`) whose ESO ServiceAccount (`eso-agent-docs-mcp`) assumes a Vault role scoped to only the `kagent-agent-docs-mcp` path, not the broad `kagent` role.
```

- [ ] **Step 5: Lint and schema-validate the new/changed manifests**

Run:
```bash
yamllint -c .yamllint.yaml \
  base-apps/kagent/eso-agent-docs-mcp-serviceaccount.yaml \
  base-apps/kagent/agent-docs-mcp-secret-store.yaml \
  base-apps/kagent/agent-docs-mcp-external-secret.yaml
kubeconform -summary -strict -ignore-missing-schemas \
  base-apps/kagent/eso-agent-docs-mcp-serviceaccount.yaml \
  base-apps/kagent/agent-docs-mcp-secret-store.yaml \
  base-apps/kagent/agent-docs-mcp-external-secret.yaml
```
Expected: yamllint clean; kubeconform reports the `ServiceAccount` valid and the External Secrets CRDs skipped/ignored (no failure).

- [ ] **Step 6: Confirm the pilot credential invariant now passes (manifest side)**

Run: `python scripts/validate-agent-identity.py --repo-root . 2>&1 | grep "agent-docs-github-mcp-token" | grep -c ERROR || echo 0`
Expected: `0` — the pilot credential no longer errors (it uses `vault-agent-docs-mcp` + key `kagent-agent-docs-mcp`).

- [ ] **Step 7: Commit**

```bash
git add base-apps/kagent/eso-agent-docs-mcp-serviceaccount.yaml \
        base-apps/kagent/agent-docs-mcp-secret-store.yaml \
        base-apps/kagent/agent-docs-mcp-external-secret.yaml \
        base-apps/kagent/docs.md
git commit -m "feat(agent-identity): credential-scope the agent-docs MCP token"
```

> **OPERATOR ACTIONS (run by you, root token — not the implementer, and BEFORE the PR merges).** These prepare Vault so the repointed ExternalSecret resolves the moment Argo CD syncs. Order is add-before-remove:
> 1. Copy the token into its own path: read the current value from `k8s-secrets/kagent` property `agent-docs-github-token`, then
>    `vault kv put k8s-secrets/kagent-agent-docs-mcp github-token=<that value>`.
> 2. Policy (reads only the new path):
>    `vault policy write kagent-agent-docs-mcp - <<'EOF'`
>    `path "k8s-secrets/data/kagent-agent-docs-mcp" { capabilities = ["read"] }`
>    `EOF`
> 3. Kubernetes-auth role bound to the dedicated SA:
>    `vault write auth/kubernetes/role/kagent-agent-docs-mcp bound_service_account_names=eso-agent-docs-mcp bound_service_account_namespaces=kagent policies=kagent-agent-docs-mcp ttl=1h`
> 4. **After** the PR merges, Argo CD syncs, and you have verified the token still materializes (below), **narrow** the broad `kagent` Vault policy so it no longer grants `k8s-secrets/data/kagent-agent-docs-mcp` (and optionally delete the now-unused `agent-docs-github-token` property from `k8s-secrets/kagent`). This last step is what turns the change into a real privilege reduction.

---

## Task 5: Wire the validator into CI

**Files:**
- Modify: `.github/workflows/validate.yaml`

**Interfaces:**
- Consumes: `scripts/validate-agent-identity.py` + `tests/agent-identity/` (Task 2), and the pilot fixes (Tasks 3–4) that make `--repo-root .` exit 0.
- Produces: an `agent-identity-validate` CI job that blocks merge on any hard error (warnings do not fail).

- [ ] **Step 1: Add the job**

Append this job to `.github/workflows/validate.yaml`, mirroring the existing `agent-docs-validate` job (sibling; repo-global, so no `changed-files` dependency):

```yaml
  agent-identity-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install pyyaml==6.0.2 pytest==8.3.3
      - name: Run agent-identity validator tests
        run: python -m pytest tests/agent-identity/ -q
      - name: Validate agent-identity contract
        run: python scripts/validate-agent-identity.py --repo-root .
```

- [ ] **Step 2: Lint the workflow file**

Run: `yamllint -c .yamllint.yaml .github/workflows/validate.yaml`
Expected: no errors.

- [ ] **Step 3: Dry-run the exact CI commands locally**

Run:
```bash
python -m pytest tests/agent-identity/ -q
python scripts/validate-agent-identity.py --repo-root .; echo "exit=$?"
```
Expected: pytest all green; validator prints WARNs for the non-pilot unscoped consumers (DB creds, Backstage token, MCP basic-auth, Plex) and `exit=0` (zero hard errors, because Tasks 3–4 fixed both pilot invariants).

- [ ] **Step 4: Prove the gate fails on a broken pilot contract**

Run:
```bash
cp base-apps/kagent/agent-docs-mcp-external-secret.yaml /tmp/es-backup.yaml
python - <<'PY'
import pathlib
p = pathlib.Path("base-apps/kagent/agent-docs-mcp-external-secret.yaml")
p.write_text(p.read_text().replace("vault-agent-docs-mcp", "vault-backend")
                          .replace("kagent-agent-docs-mcp", "kagent"))
PY
python scripts/validate-agent-identity.py --repo-root .; echo "exit=$?"
cp /tmp/es-backup.yaml base-apps/kagent/agent-docs-mcp-external-secret.yaml
```
Expected: an `ERROR` naming `agent-docs-github-mcp-token` and `exit=1`; after restore, `python scripts/validate-agent-identity.py --repo-root .` exits 0 and `git status` shows the file unmodified.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/validate.yaml
git commit -m "ci(agent-identity): validate the agent-identity contract on PRs"
```

---

## Final verification

- [ ] `python -m pytest tests/agent-identity/ -v` → all pass.
- [ ] `python scripts/validate-agent-identity.py --repo-root .` → exit 0, warnings only for the non-pilot unscoped consumers.
- [ ] `yamllint -c .yamllint.yaml base-apps/kagent/**/*.yaml` on the changed files → clean.
- [ ] `git log --oneline` shows one commit per task, all on `docs/agent-identity-principal` (none on `main`).
- [ ] The only modified existing manifest is `base-apps/kagent/agent-docs-mcp-external-secret.yaml` (plus additive new files and `docs.md`); no other existing manifest changed.
- [ ] The `ModelConfig` git content matches the live object (no-op adoption confirmed).

## Deployment & cutover (operator, after review)

This is a GitOps repo — merging to `main` auto-syncs. Sequence:

1. Merge only after the operator has completed **Task 4 OPERATOR ACTIONS steps 1–3** (new Vault path + policy + role). Merging earlier would break the `agent-docs-mcp` token sync.
2. Merge → Argo CD syncs the new `ServiceAccount`, `SecretStore`, repointed `ExternalSecret`, and the adopted `ModelConfig`.
3. **Verify:** confirm `ExternalSecret agent-docs-github-mcp-token` is `Ready` and the Secret still holds the token, then ask `homelab-knowledge` a repo question (e.g. "What is cert-manager and how does it issue certs here?") — it must still read the repo. Confirm the `ModelConfig` adoption caused no drift.
4. **Only after verification:** run **Task 4 OPERATOR ACTIONS step 4** (narrow the broad `kagent` policy; optionally delete the old property). This completes the privilege reduction.
5. Rollback if needed: revert the PR; leave `k8s-secrets/kagent-agent-docs-mcp` in place until re-verified.

## Success criteria (from the spec)

- `templates/agent-identity/README.md` documents the pattern + three invariants. ✅ Task 1.
- `scripts/validate-agent-identity.py` exists with pytest coverage + a CI job, and passes (pilot satisfied; others warn). ✅ Tasks 2, 5.
- The `agent-docs-mcp` token resolves through the dedicated `(eso-agent-docs-mcp SA + kagent-agent-docs-mcp role + kagent-agent-docs-mcp path + vault-agent-docs-mcp SecretStore)`, and the broad role no longer grants that path. ✅ Task 4 + operator actions.
- `homelab-knowledge`'s `ModelConfig` is a git-tracked manifest. ✅ Task 3.
- End-to-end: `homelab-knowledge` still answers a repo question after cutover. ✅ Deployment step 3.
- `yamllint` + `kubeconform` pass; no unrelated manifest modified. ✅ Final verification.
