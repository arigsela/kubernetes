#!/usr/bin/env python3
"""Generate (or --check) per-app Backstage TechDocs scaffolding.

For every base-apps/<app>/ that has both docs.md and runbook.md (the agent-docs
contract), this produces the files TechDocs' MkDocs builder needs:

  base-apps/<app>/mkdocs.yml        -- site config (docs_dir: docs, techdocs-core)
  base-apps/<app>/docs/index.md     -- copy of docs.md
  base-apps/<app>/docs/runbook.md   -- copy of runbook.md

and ensures base-apps/<app>/catalog-info.yaml carries the annotation
  backstage.io/techdocs-ref: dir:.

WHY COPIES (not docs_dir: '.' or symlinks):
  - MkDocs 1.6 rejects docs_dir being the config file's own directory, so the
    markdown must live in a child subdir (docs/).
  - The root docs.md/runbook.md must stay put for the agent-docs contract
    (validate-agent-docs.py, the homelab-knowledge agent, the agent-docs/path
    annotation). Moving them would break that framework.
  - Symlinks build locally but Backstage's GitHub TechDocs fetch extracts the
    source tree with tar, which unreliably preserves symlinks.
  So docs.md/runbook.md stay canonical at the app root and are copied into docs/.
  This script keeps the copies in sync; the CI --check gate fails on drift.

Usage:
  python3 scripts/gen-techdocs.py --repo-root .            # write/update
  python3 scripts/gen-techdocs.py --repo-root . --check    # verify, exit 1 on drift
"""
import argparse
import sys
from pathlib import Path

MKDOCS_TEMPLATE = """\
site_name: {app}
docs_dir: docs
nav:
  - Overview: index.md
  - Runbook: runbook.md
plugins:
  - techdocs-core
"""

TECHDOCS_ANNOTATION = "backstage.io/techdocs-ref: dir:."
AGENT_DOCS_KEY = "agent-docs/path:"


def _apps(repo_root):
    """base-apps/<app> dirs that have both docs.md and runbook.md, sorted."""
    base = Path(repo_root) / "base-apps"
    out = []
    for d in sorted(base.iterdir()):
        if d.is_dir() and (d / "docs.md").is_file() and (d / "runbook.md").is_file():
            out.append(d)
    return out


def _annotated_catalog_info(text):
    """Return catalog-info.yaml text with the techdocs-ref annotation ensured.

    Idempotent: if any 'techdocs-ref' is already present, returns text unchanged.
    Otherwise inserts the annotation immediately after the agent-docs/path line,
    matching its indentation. Returns None if there is no agent-docs/path line
    to anchor to (caller treats that as an error).
    """
    if "techdocs-ref" in text:
        return text
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(AGENT_DOCS_KEY):
            indent = line[: len(line) - len(stripped)]
            newline = "\n" if line.endswith("\n") else ""
            lines.insert(i + 1, f"{indent}{TECHDOCS_ANNOTATION}{newline}")
            return "".join(lines)
    return None


def _desired(app_dir):
    """Return {relative_path: desired_content} for one app."""
    app = app_dir.name
    files = {
        "mkdocs.yml": MKDOCS_TEMPLATE.format(app=app),
        "docs/index.md": (app_dir / "docs.md").read_text(),
        "docs/runbook.md": (app_dir / "runbook.md").read_text(),
    }
    catalog = app_dir / "catalog-info.yaml"
    annotated = _annotated_catalog_info(catalog.read_text())
    files["catalog-info.yaml"] = annotated  # may be None -> reported as error
    return files


def check(repo_root):
    """Return a list of drift/error messages (empty == in sync)."""
    problems = []
    for app_dir in _apps(repo_root):
        for rel, want in _desired(app_dir).items():
            path = app_dir / rel
            if want is None:
                problems.append(f"{path}: no '{AGENT_DOCS_KEY}' annotation to anchor techdocs-ref")
                continue
            if not path.is_file():
                problems.append(f"{path}: missing (run gen-techdocs.py to create)")
            elif path.read_text() != want:
                problems.append(f"{path}: out of sync (run gen-techdocs.py)")
    return problems


def write(repo_root):
    """Generate/update all files. Returns count of files written."""
    written = 0
    for app_dir in _apps(repo_root):
        for rel, want in _desired(app_dir).items():
            if want is None:
                raise SystemExit(
                    f"ERROR: {app_dir/rel}: no '{AGENT_DOCS_KEY}' annotation to anchor techdocs-ref"
                )
            path = app_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.is_file() or path.read_text() != want:
                path.write_text(want)
                written += 1
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--check", action="store_true", help="verify only; exit 1 on drift")
    args = ap.parse_args()

    if args.check:
        problems = check(args.repo_root)
        if problems:
            print("TechDocs scaffolding out of sync:")
            for p in problems:
                print(f"  {p}")
            print(f"\n{len(problems)} problem(s). Run: python3 scripts/gen-techdocs.py --repo-root .")
            return 1
        print(f"TechDocs scaffolding in sync ({len(_apps(args.repo_root))} apps).")
        return 0

    n = write(args.repo_root)
    print(f"Generated/updated {n} file(s) across {len(_apps(args.repo_root))} apps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
