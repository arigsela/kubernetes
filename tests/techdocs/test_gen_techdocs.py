import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen-techdocs.py"
_spec = importlib.util.spec_from_file_location("gen_techdocs", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

_CATALOG = """\
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: sample
  namespace: sample
  annotations:
    agent-docs/path: docs.md
    backstage.io/kubernetes-namespace: sample
spec:
  type: service
  owner: group:default/platform
"""


def _app(root: Path, name: str = "sample") -> Path:
    d = root / "base-apps" / name
    d.mkdir(parents=True)
    (d / "docs.md").write_text("# Overview\nbody\n")
    (d / "runbook.md").write_text("# Runbook\nsteps\n")
    (d / "catalog-info.yaml").write_text(_CATALOG)
    return d


def test_annotation_inserted_with_matching_indent():
    out = mod._annotated_catalog_info(_CATALOG)
    assert "backstage.io/techdocs-ref: dir:." in out
    # inserted directly after the agent-docs/path line, same 4-space indent
    lines = out.splitlines()
    i = next(n for n, l in enumerate(lines) if l.strip().startswith("agent-docs/path:"))
    assert lines[i + 1] == "    backstage.io/techdocs-ref: dir:."


def test_annotation_idempotent():
    once = mod._annotated_catalog_info(_CATALOG)
    twice = mod._annotated_catalog_info(once)
    assert once == twice


def test_annotation_missing_anchor_returns_none():
    assert mod._annotated_catalog_info("kind: Component\nmetadata:\n  name: x\n") is None


def test_write_then_check_is_clean(tmp_path):
    _app(tmp_path)
    mod.write(str(tmp_path))
    assert mod.check(str(tmp_path)) == []
    # generated files exist with expected content
    d = tmp_path / "base-apps" / "sample"
    assert (d / "mkdocs.yml").read_text().startswith("site_name: sample")
    assert (d / "docs" / "index.md").read_text() == (d / "docs.md").read_text()
    assert (d / "docs" / "runbook.md").read_text() == (d / "runbook.md").read_text()
    assert "backstage.io/techdocs-ref: dir:." in (d / "catalog-info.yaml").read_text()


def test_check_reports_missing_catalog_info_without_crashing(tmp_path):
    d = tmp_path / "base-apps" / "sample"
    d.mkdir(parents=True)
    (d / "docs.md").write_text("# Overview\nbody\n")
    (d / "runbook.md").write_text("# Runbook\nsteps\n")
    # no catalog-info.yaml on purpose
    problems = mod.check(str(tmp_path))  # must not raise FileNotFoundError
    assert any("catalog-info.yaml" in p for p in problems)


def test_check_detects_drift(tmp_path):
    _app(tmp_path)
    mod.write(str(tmp_path))
    # edit the source docs.md without regenerating -> docs/index.md drifts
    (tmp_path / "base-apps" / "sample" / "docs.md").write_text("# Overview\nCHANGED\n")
    problems = mod.check(str(tmp_path))
    assert any("docs/index.md" in p for p in problems)
