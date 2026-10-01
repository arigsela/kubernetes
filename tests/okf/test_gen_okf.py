import importlib.util
import re
import subprocess
from datetime import date, datetime, timedelta
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
status: stable
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
    _write(root / "index.md", '---\nokf_version: "0.2"\ntype: "Bundle"\n---\nroot\n')
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


def test_check_flags_stale_okf_version(tmp_path):
    root = _make_repo(tmp_path)
    gen_okf.write(root)
    (root / "index.md").write_text('---\nokf_version: "0.1"\n---\nroot\n')
    assert any("okf_version" in p for p in gen_okf.check(root))


def test_export_writes_bundle_without_manifests(tmp_path):
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


# --- OKF v0.2 export ---------------------------------------------------------

AGENT_TRAILER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def _git(root: Path, *args: str):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _commit(root: Path, message: str, *, author="Ari Sela <arigsela@example.com>"):
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=x", "-c", "user.email=x@example.com",
         "commit", "-q", f"--author={author}", "-m", message)


def _git_repo(tmp_path: Path, message: str) -> Path:
    root = _make_repo(tmp_path)
    _write(root / "base-apps" / "demo" / "deployments.yaml", "kind: Deployment\n")
    _git(root, "init", "-q")
    _commit(root, message)
    return root


def _frontmatter(path: Path) -> dict:
    return gen_okf._read_frontmatter(path)


def _has_offset(value) -> bool:
    """OKF v0.2 §5: an ISO 8601 datetime with an explicit offset. git writes UTC
    as `Z` and other zones as `-04:00`, so accept both rather than one shape."""
    try:
        return datetime.fromisoformat(str(value)).tzinfo is not None
    except ValueError:
        return False


def _export(root: Path, tmp_path: Path) -> Path:
    dest = tmp_path / "out"
    gen_okf.export(root, dest)
    return dest


def test_export_records_agent_writer_and_verifier(tmp_path):
    root = _git_repo(tmp_path, f"docs: demo\n\n{AGENT_TRAILER}\n")
    fm = _frontmatter(_export(root, tmp_path) / "base-apps" / "demo" / "docs.md")
    assert "timestamp" not in fm
    assert fm["generated"]["by"] == "claude-code/claude-opus-5.5"
    assert fm["verified"] == {"by": "claude-code/claude-opus-5.5", "at": "2026-07-08T00:00:00Z"}


def test_export_records_human_writer_without_agent_trailer(tmp_path):
    root = _git_repo(tmp_path, "docs: demo\n")
    fm = _frontmatter(_export(root, tmp_path) / "base-apps" / "demo" / "docs.md")
    assert fm["generated"]["by"] == "human:arigsela"
    assert fm["verified"]["by"] == "human:arigsela"


def test_export_reviewed_by_trailer_marks_human_verification(tmp_path):
    root = _git_repo(tmp_path, "docs: demo\n")
    docs = root / "base-apps" / "demo" / "docs.md"
    docs.write_text(docs.read_text().replace("last_reviewed: 2026-07-08", "last_reviewed: 2026-09-01"))
    _commit(root, f"docs: review demo\n\n{AGENT_TRAILER}\nReviewed-by: Ari Sela <arigsela@example.com>\n")
    fm = _frontmatter(_export(root, tmp_path) / "base-apps" / "demo" / "docs.md")
    assert fm["generated"]["by"] == "claude-code/claude-opus-5.5"
    assert fm["verified"] == {"by": "human:arigsela", "at": "2026-09-01T00:00:00Z"}


def test_export_verifier_comes_from_the_commit_that_bumped_last_reviewed(tmp_path):
    root = _git_repo(tmp_path, "docs: demo\n")  # human bumps last_reviewed
    docs = root / "base-apps" / "demo" / "docs.md"
    docs.write_text(docs.read_text().replace("body", "edited body"))
    _commit(root, f"docs: agent edit\n\n{AGENT_TRAILER}\n")  # agent edits, no bump
    fm = _frontmatter(_export(root, tmp_path) / "base-apps" / "demo" / "docs.md")
    assert fm["generated"]["by"] == "claude-code/claude-opus-5.5"
    assert fm["verified"]["by"] == "human:arigsela"


def test_export_stale_after_is_last_reviewed_plus_review_window(tmp_path):
    root = _git_repo(tmp_path, "docs: demo\n")
    fm = _frontmatter(_export(root, tmp_path) / "base-apps" / "demo" / "docs.md")
    want = date(2026, 7, 8) + timedelta(days=gen_okf.STALE_AFTER_DAYS)
    assert fm["stale_after"] == f"{want.isoformat()}T00:00:00Z"
    assert fm["status"] == "stable"


def test_export_sources_become_okf_mappings(tmp_path):
    root = _git_repo(tmp_path, "docs: demo\n")
    docs = root / "base-apps" / "demo" / "docs.md"
    docs.write_text(docs.read_text().replace(
        "  - base-apps/demo/deployments.yaml",
        "  - base-apps/demo/deployments.yaml\n"
        "  - id: upstream\n    resource: https://example.com/docs\n    title: Upstream docs"))
    _commit(root, "docs: cite upstream\n")
    fm = _frontmatter(_export(root, tmp_path) / "base-apps" / "demo" / "docs.md")
    local, upstream = fm["sources"]
    assert local["id"] == "base-apps-demo-deployments-yaml"
    assert local["resource"] == "base-apps/demo/deployments.yaml"  # no origin remote
    assert _has_offset(local["last_modified"])
    assert upstream == {"id": "upstream", "resource": "https://example.com/docs",
                        "title": "Upstream docs"}


def test_export_links_sources_to_origin_at_head(tmp_path):
    root = _git_repo(tmp_path, "docs: demo\n")
    _git(root, "remote", "add", "origin", "git@github.com:arigsela/kubernetes.git")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()
    fm = _frontmatter(_export(root, tmp_path) / "base-apps" / "demo" / "docs.md")
    assert fm["sources"][0]["resource"] == (
        f"https://github.com/arigsela/kubernetes/blob/{head}/base-apps/demo/deployments.yaml")


def test_export_without_git_still_writes_lifecycle(tmp_path):
    fm = _frontmatter(_export(_make_repo(tmp_path), tmp_path) / "base-apps" / "demo" / "docs.md")
    assert "generated" not in fm and "verified" not in fm
    assert fm["stale_after"].endswith("T00:00:00Z")


def test_export_moves_hand_written_index_to_overview(tmp_path):
    root = _make_repo(tmp_path)
    _write(root / "terraform" / "index.md",
           '---\ntype: "Directory Index"\ntitle: "Terraform"\n'
           'description: "The Terraform tree."\n---\n# terraform Index\n')
    dest = _export(root, tmp_path)
    overview = _frontmatter(dest / "terraform" / "overview.md")
    assert overview["type"] == "Directory Overview"
    assert "* [Terraform](overview.md) - The Terraform tree." in (dest / "terraform" / "index.md").read_text()


def test_export_is_okf_v02_conformant(tmp_path):
    """Every exported file meets OKF v0.2 §8 (index) or §4/§11 (concept)."""
    root = _git_repo(tmp_path, "docs: demo\n")
    _write(root / "terraform" / "index.md",
           '---\ntype: "Directory Index"\ntitle: "Terraform"\ndescription: "Tf."\n---\nbody\n')
    _commit(root, "docs: terraform\n")
    dest = _export(root, tmp_path)
    files = sorted(dest.rglob("*.md"))
    assert files
    for path in files:
        text = path.read_text()
        if path.name == "index.md":
            if path.parent == dest:
                assert _frontmatter(path) == {"okf_version": gen_okf.OKF_VERSION}
            else:
                assert not text.startswith("---"), f"{path}: index.md must carry no frontmatter"
            assert re.search(r"^\* \[.+\]\(.+\) - .+$", text, re.M), f"{path}: not a §8 listing"
        else:
            fm = _frontmatter(path)
            assert fm.get("type"), f"{path}: concept needs a type"
            assert "timestamp" not in fm, f"{path}: timestamp is superseded by generated.at"
            assert fm.get("status", "stable") in {"draft", "stable", "deprecated"}
            stamps = [fm.get("stale_after"), (fm.get("generated") or {}).get("at"),
                      (fm.get("verified") or {}).get("at")]
            stamps += [e.get("last_modified") for e in fm.get("sources", [])]
            for stamp in filter(None, stamps):
                assert _has_offset(stamp), f"{path}: {stamp!r} lacks an explicit offset"
            for entry in fm.get("sources", []):
                assert isinstance(entry, dict) and entry.get("resource"), f"{path}: bad source {entry!r}"


def test_export_base_apps_index_lists_guides_and_runbooks(tmp_path):
    index = (_export(_make_repo(tmp_path), tmp_path) / "base-apps" / "index.md").read_text()
    assert "* [Demo](demo/docs.md) - A demo app." in index
    assert "(demo/runbook.md)" in index
    assert "plain" not in index  # stubs have no concept document to list
