from datetime import date
from pathlib import Path

# The script filename has hyphens; import it by path via importlib.
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "validate_agent_docs",
    Path(__file__).resolve().parents[2] / "scripts" / "validate-agent-docs.py",
)
validate_agent_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_agent_docs)


GOOD_FM = """---
type: "Kubernetes App Guide"
title: "Demo"
description: "A demo app used by the validator tests."
app: demo
catalog_entity: demo
kind: docs
namespace: demo-ns
last_reviewed: 2026-07-08
status: stable
tags: [x]
sources:
  - base-apps/demo/deployments.yaml
---
body
"""


def test_parse_frontmatter_returns_dict():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    assert fm["app"] == "demo"
    assert fm["kind"] == "docs"


def test_validate_frontmatter_accepts_good():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    assert validate_agent_docs.validate_frontmatter(fm) == []


def test_validate_frontmatter_rejects_bad_kind():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    fm["kind"] = "notes"
    errors = validate_agent_docs.validate_frontmatter(fm)
    assert any("kind" in e for e in errors)


def test_validate_frontmatter_rejects_bad_date():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    fm["last_reviewed"] = "yesterday"
    errors = validate_agent_docs.validate_frontmatter(fm)
    assert any("last_reviewed" in e for e in errors)


def test_validate_frontmatter_rejects_type_kind_mismatch():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    fm["kind"] = "runbook"  # type still says Guide
    errors = validate_agent_docs.validate_frontmatter(fm)
    assert any("type must be" in e for e in errors)


def test_validate_frontmatter_rejects_blank_description():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    fm["description"] = "   "
    errors = validate_agent_docs.validate_frontmatter(fm)
    assert any("description must be a non-empty string" in e for e in errors)


def test_validate_frontmatter_rejects_pipe_in_description():
    # A pipe would corrupt the generated base-apps/index.md table.
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    fm["description"] = "does a | thing"
    errors = validate_agent_docs.validate_frontmatter(fm)
    assert any("'|'" in e for e in errors)


def test_validate_frontmatter_rejects_missing_title():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    del fm["title"]
    errors = validate_agent_docs.validate_frontmatter(fm)
    assert any("title" in e for e in errors)


def test_missing_frontmatter_raises():
    import pytest
    with pytest.raises(ValueError):
        validate_agent_docs.parse_frontmatter("no frontmatter here")


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _make_repo(tmp_path: Path) -> Path:
    # Minimal fake repo: one in-scope app 'demo' with a valid contract.
    root = tmp_path
    app = root / "base-apps" / "demo"
    _write(app / "deployments.yaml", "kind: Deployment\n")
    _write(app / "catalog-info.yaml",
           "apiVersion: backstage.io/v1alpha1\nkind: Component\n"
           "metadata:\n  name: demo\n  namespace: demo-ns\n"
           "  annotations:\n    agent-docs/path: docs.md\nspec:\n  owner: platform\n")
    _write(app / "docs.md", GOOD_FM)
    # `type` must track `kind`, so swap both.
    runbook = GOOD_FM.replace("kind: docs", "kind: runbook").replace(
        "Kubernetes App Guide", "Kubernetes App Runbook")
    _write(app / "runbook.md", runbook)
    # Argo CD Application for 'demo' that excludes catalog-info.yaml (in-band guard).
    _write(root / "base-apps" / "demo.yaml",
           "apiVersion: argoproj.io/v1alpha1\nkind: Application\n"
           "metadata:\n  name: demo\nspec:\n  source:\n"
           "    path: base-apps/demo\n"
           "    directory:\n      exclude: catalog-info.yaml\n")
    _write(root / "base-apps" / "index.md",
           "| app | description | namespace | docs | runbook | catalog |\n"
           "|---|---|---|---|---|---|\n"
           "| demo | x | demo-ns | docs.md | runbook.md | catalog-info.yaml |\n")
    _write(root / "scripts" / "agent-docs-scope.txt", "demo\n")
    return root


def test_check_app_contract_passes_on_good_repo(tmp_path):
    root = _make_repo(tmp_path)
    assert validate_agent_docs.check_app_contract(root, "demo") == []


def test_check_app_contract_flags_missing_runbook(tmp_path):
    root = _make_repo(tmp_path)
    (root / "base-apps" / "demo" / "runbook.md").unlink()
    errors = validate_agent_docs.check_app_contract(root, "demo")
    assert any("runbook.md" in e for e in errors)


def test_check_app_contract_flags_dangling_source(tmp_path):
    root = _make_repo(tmp_path)
    (root / "base-apps" / "demo" / "deployments.yaml").unlink()
    errors = validate_agent_docs.check_app_contract(root, "demo")
    assert any("deployments.yaml" in e for e in errors)


def test_check_app_contract_flags_catalog_name_mismatch(tmp_path):
    root = _make_repo(tmp_path)
    ci = root / "base-apps" / "demo" / "catalog-info.yaml"
    ci.write_text(ci.read_text().replace("name: demo", "name: wrong"))
    errors = validate_agent_docs.check_app_contract(root, "demo")
    assert any("catalog_entity" in e or "name" in e for e in errors)


def test_check_app_contract_flags_bad_catalog_apiversion(tmp_path):
    root = _make_repo(tmp_path)
    ci = root / "base-apps" / "demo" / "catalog-info.yaml"
    ci.write_text(ci.read_text().replace("backstage.io/v1alpha1", "example.com/v1"))
    errors = validate_agent_docs.check_app_contract(root, "demo")
    assert any("apiVersion" in e for e in errors)


def test_check_app_contract_flags_bad_catalog_kind(tmp_path):
    root = _make_repo(tmp_path)
    ci = root / "base-apps" / "demo" / "catalog-info.yaml"
    ci.write_text(ci.read_text().replace("kind: Component", "kind: Widget"))
    errors = validate_agent_docs.check_app_contract(root, "demo")
    assert any("kind must be one of" in e for e in errors)


def test_check_app_contract_flags_missing_agent_docs_annotation(tmp_path):
    root = _make_repo(tmp_path)
    ci = root / "base-apps" / "demo" / "catalog-info.yaml"
    ci.write_text(ci.read_text().replace("    agent-docs/path: docs.md\n", ""))
    errors = validate_agent_docs.check_app_contract(root, "demo")
    assert any("agent-docs/path" in e for e in errors)


def test_check_index_coverage_flags_missing_row(tmp_path):
    root = _make_repo(tmp_path)
    # add an app dir with no index row
    _write(root / "base-apps" / "orphan" / "x.yaml", "kind: X\n")
    errors = validate_agent_docs.check_index_coverage(root)
    assert any("orphan" in e for e in errors)


def test_check_staleness_warns_when_old(tmp_path):
    root = _make_repo(tmp_path)
    warnings = validate_agent_docs.check_staleness(root, ["demo"], date(2030, 1, 1), 180)
    assert any("demo" in w for w in warnings)


def test_directory_exclude_passes_on_good_repo(tmp_path):
    root = _make_repo(tmp_path)
    assert validate_agent_docs.check_app_directory_exclude(root, "demo") == []


def test_directory_exclude_flags_missing_exclude(tmp_path):
    root = _make_repo(tmp_path)
    # Application exists but drops the directory.exclude guard.
    _write(root / "base-apps" / "demo.yaml",
           "apiVersion: argoproj.io/v1alpha1\nkind: Application\n"
           "metadata:\n  name: demo\nspec:\n  source:\n    path: base-apps/demo\n")
    errors = validate_agent_docs.check_app_directory_exclude(root, "demo")
    assert any("directory.exclude" in e for e in errors)


def test_directory_exclude_flags_no_application(tmp_path):
    root = _make_repo(tmp_path)
    (root / "base-apps" / "demo.yaml").unlink()
    errors = validate_agent_docs.check_app_directory_exclude(root, "demo")
    assert any("no Argo CD Application" in e for e in errors)


def test_directory_exclude_matches_by_source_path_not_filename(tmp_path):
    # App synced by a differently-named manifest (like cert-manager-config.yaml).
    root = _make_repo(tmp_path)
    (root / "base-apps" / "demo.yaml").unlink()
    _write(root / "base-apps" / "demo-config.yaml",
           "apiVersion: argoproj.io/v1alpha1\nkind: Application\n"
           "metadata:\n  name: demo-config\nspec:\n  source:\n"
           "    path: base-apps/demo\n"
           "    directory:\n      exclude: catalog-info.yaml\n")
    assert validate_agent_docs.check_app_directory_exclude(root, "demo") == []


def test_directory_exclude_ok_when_no_catalog(tmp_path):
    root = _make_repo(tmp_path)
    (root / "base-apps" / "demo" / "catalog-info.yaml").unlink()
    # No catalog-info.yaml -> nothing to guard, even with no Application.
    assert validate_agent_docs.check_app_directory_exclude(root, "demo") == []


# --- OKF v0.2: lifecycle values, mapping-form sources, per-claim footnotes ---

def test_validate_frontmatter_accepts_okf_status_values():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    for status in ("stable", "draft", "deprecated"):
        fm["status"] = status
        assert validate_agent_docs.validate_frontmatter(fm) == []


def test_validate_frontmatter_rejects_pre_okf_status_values():
    # current/wip were this repo's own values before OKF v0.2 reserved `status`.
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    for status in ("current", "wip"):
        fm["status"] = status
        assert any("status" in e for e in validate_agent_docs.validate_frontmatter(fm))


def test_validate_frontmatter_accepts_mapping_source():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    fm["sources"] = ["base-apps/demo/deployments.yaml",
                     {"id": "deploy", "resource": "base-apps/demo/deployments.yaml",
                      "title": "Demo Deployment"}]
    assert validate_agent_docs.validate_frontmatter(fm) == []


def test_validate_frontmatter_rejects_mapping_source_without_id():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    fm["sources"] = [{"resource": "base-apps/demo/deployments.yaml"}]
    assert any("sources entry" in e for e in validate_agent_docs.validate_frontmatter(fm))


def test_validate_frontmatter_rejects_duplicate_source_ids():
    fm = validate_agent_docs.parse_frontmatter(GOOD_FM)
    fm["sources"] = [{"id": "a", "resource": "x.yaml"}, {"id": "a", "resource": "y.yaml"}]
    assert any("duplicate sources id" in e for e in validate_agent_docs.validate_frontmatter(fm))


def test_validate_footnotes_accepts_cited_source():
    fm = {"sources": [{"id": "deploy", "resource": "base-apps/demo/deployments.yaml"}]}
    body = "Runs two replicas.[^deploy]\n\n[^deploy]: Demo Deployment\n"
    assert validate_agent_docs.validate_footnotes(fm, body) == []


def test_validate_footnotes_rejects_label_without_source():
    fm = {"sources": ["base-apps/demo/deployments.yaml"]}
    body = "Runs two replicas.[^deploy]\n\n[^deploy]: Demo Deployment\n"
    errors = validate_agent_docs.validate_footnotes(fm, body)
    assert any("[^deploy]" in e and "sources id" in e for e in errors)


def test_validate_footnotes_rejects_missing_definition():
    fm = {"sources": [{"id": "deploy", "resource": "base-apps/demo/deployments.yaml"}]}
    errors = validate_agent_docs.validate_footnotes(fm, "Runs two replicas.[^deploy]\n")
    assert any("no definition" in e for e in errors)


def test_validate_footnotes_ignores_code_fences():
    fm = {"sources": ["base-apps/demo/deployments.yaml"]}
    body = "```\nliteral [^not-a-cite]\n```\n"
    assert validate_agent_docs.validate_footnotes(fm, body) == []


def test_check_app_contract_flags_dangling_mapping_source(tmp_path):
    root = _make_repo(tmp_path)
    docs = root / "base-apps" / "demo" / "docs.md"
    docs.write_text(docs.read_text().replace(
        "  - base-apps/demo/deployments.yaml",
        "  - id: gone\n    resource: base-apps/demo/gone.yaml"))
    errors = validate_agent_docs.check_app_contract(root, "demo")
    assert any("gone.yaml" in e for e in errors)


def test_check_app_contract_accepts_url_source(tmp_path):
    root = _make_repo(tmp_path)
    docs = root / "base-apps" / "demo" / "docs.md"
    docs.write_text(docs.read_text().replace(
        "  - base-apps/demo/deployments.yaml",
        "  - base-apps/demo/deployments.yaml\n"
        "  - id: upstream\n    resource: https://example.com/docs"))
    assert validate_agent_docs.check_app_contract(root, "demo") == []


def test_validate_footnotes_ignores_inline_code():
    fm = {"sources": ["base-apps/demo/deployments.yaml"]}
    assert validate_agent_docs.validate_footnotes(fm, "Cite with `[^id]`.\n") == []
