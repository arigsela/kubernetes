import importlib.util
from pathlib import Path

import pytest

# The script filename has hyphens; import it by path via importlib.
_spec = importlib.util.spec_from_file_location(
    "gen_okf",
    Path(__file__).resolve().parents[2] / "scripts" / "gen-okf.py",
)
gen_okf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_okf)


DOCS_FM = """---
type: "Kubernetes App Guide"
title: "Demo"
description: "A demo app."
app: demo
catalog_entity: demo
kind: docs
namespace: demo-ns
last_reviewed: 2026-07-08
status: current
tags: [x]
sources:
  - base-apps/demo/deployments.yaml
---
body
"""


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _make_repo(tmp_path: Path) -> Path:
    """Fake repo: one documented app 'demo', one undocumented stub 'plain'."""
    root = tmp_path
    _write(root / "base-apps" / "demo" / "docs.md", DOCS_FM)
    _write(root / "base-apps" / "demo" / "runbook.md",
           DOCS_FM.replace("kind: docs", "kind: runbook"))
    _write(root / "base-apps" / "demo" / "catalog-info.yaml", "kind: Component\n")
    _write(root / "base-apps" / "plain" / "deployment.yaml", "kind: Deployment\n")
    _write(root / "scripts" / "okf-stubs.yaml",
           'plain:\n  description: "An undocumented stub."\n  namespace: plain-ns\n')
    _write(root / "index.md", '---\nokf_version: "0.1"\ntype: "Bundle"\n---\nroot\n')
    return root


def test_render_index_uses_frontmatter_description(tmp_path):
    index = gen_okf.render_index(_make_repo(tmp_path))
    assert "| demo | A demo app. | demo-ns |" in index
    assert "[docs.md](demo/docs.md)" in index
    assert "[runbook.md](demo/runbook.md)" in index


def test_render_index_falls_back_to_stub_descriptions(tmp_path):
    # An app with no docs.md must not silently lose its curated one-liner.
    index = gen_okf.render_index(_make_repo(tmp_path))
    assert "| plain | An undocumented stub. | plain-ns |" in index


def test_render_index_marks_generated(tmp_path):
    assert gen_okf.GENERATED_MARKER in gen_okf.render_index(_make_repo(tmp_path))


def test_check_flags_missing_index(tmp_path):
    root = _make_repo(tmp_path)
    problems = gen_okf.check(root)
    assert any("missing" in p for p in problems)


def test_write_then_check_is_clean(tmp_path):
    root = _make_repo(tmp_path)
    gen_okf.write(root)
    assert gen_okf.check(root) == []


def test_check_flags_drift(tmp_path):
    root = _make_repo(tmp_path)
    gen_okf.write(root)
    docs = root / "base-apps" / "demo" / "docs.md"
    docs.write_text(docs.read_text().replace("A demo app.", "Changed."))
    assert any("out of sync" in p for p in gen_okf.check(root))


def test_check_flags_bundle_root_without_okf_version(tmp_path):
    root = _make_repo(tmp_path)
    gen_okf.write(root)
    (root / "index.md").write_text('---\ntype: "Bundle"\n---\nroot\n')
    assert any("okf_version" in p for p in gen_okf.check(root))


def test_with_timestamp_appends_as_last_frontmatter_key():
    out = gen_okf._with_timestamp(DOCS_FM, "2026-07-20T12:00:00Z")
    frontmatter = out.split("---")[1]
    assert frontmatter.rstrip().endswith("timestamp: 2026-07-20T12:00:00Z")
    assert out.endswith("body\n")


def test_export_writes_bundle_with_timestamps(tmp_path):
    root = _make_repo(tmp_path)
    dest = tmp_path / "out"
    count = gen_okf.export(root, dest)
    assert count >= 4
    assert (dest / "index.md").is_file()
    assert (dest / "base-apps" / "index.md").is_file()
    assert (dest / "base-apps" / "demo" / "docs.md").is_file()
    # No manifests leak into a knowledge bundle.
    assert not (dest / "base-apps" / "demo" / "catalog-info.yaml").exists()
    assert not (dest / "base-apps" / "plain").exists()


def test_render_index_rejects_doc_without_frontmatter(tmp_path):
    root = _make_repo(tmp_path)
    (root / "base-apps" / "demo" / "docs.md").write_text("no frontmatter\n")
    with pytest.raises(ValueError):
        gen_okf.render_index(root)
