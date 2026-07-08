#!/usr/bin/env python3
"""Validate the agent-docs contract across the repo.

Checks (hard failures, exit 1): contract-file presence for in-scope apps,
frontmatter validity, link/source resolution, catalog_entity name match,
and base-apps index coverage. Staleness is a warning unless --staleness-fails.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

REQUIRED_KEYS = ["app", "catalog_entity", "kind", "namespace", "last_reviewed", "status", "tags", "sources"]
KIND_VALUES = {"docs", "runbook"}
STATUS_VALUES = {"current", "wip", "deprecated"}


def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        raise ValueError("missing YAML frontmatter (must start with '---')")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("malformed frontmatter: no closing '---'")
    data = yaml.safe_load(parts[1])
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    return data


def validate_frontmatter(fm: dict) -> list[str]:
    errors = []
    for key in REQUIRED_KEYS:
        if key not in fm:
            errors.append(f"missing required key: {key}")
    if fm.get("kind") not in KIND_VALUES:
        errors.append(f"kind must be one of {sorted(KIND_VALUES)}, got {fm.get('kind')!r}")
    if fm.get("status") not in STATUS_VALUES:
        errors.append(f"status must be one of {sorted(STATUS_VALUES)}, got {fm.get('status')!r}")
    lr = fm.get("last_reviewed")
    if not _is_iso_date(lr):
        errors.append(f"last_reviewed must be an ISO date YYYY-MM-DD, got {lr!r}")
    if not isinstance(fm.get("tags"), list):
        errors.append("tags must be a list")
    if not isinstance(fm.get("sources"), list) or not fm.get("sources"):
        errors.append("sources must be a non-empty list")
    return errors


def _is_iso_date(value) -> bool:
    if isinstance(value, date):
        return True
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


CONTRACT_FILES = ["catalog-info.yaml", "docs.md", "runbook.md"]


def _read_frontmatter_file(path: Path) -> tuple[dict | None, list[str]]:
    try:
        fm = parse_frontmatter(path.read_text())
    except ValueError as exc:
        return None, [f"{path}: {exc}"]
    return fm, [f"{path}: {e}" for e in validate_frontmatter(fm)]


def check_app_contract(repo_root: Path, app: str) -> list[str]:
    errors: list[str] = []
    app_dir = repo_root / "base-apps" / app
    for fname in CONTRACT_FILES:
        if not (app_dir / fname).is_file():
            errors.append(f"{app}: missing contract file {fname}")
    if errors:
        return errors  # can't check further without the files

    catalog = yaml.safe_load((app_dir / "catalog-info.yaml").read_text()) or {}
    catalog_name = (catalog.get("metadata") or {}).get("name")

    for md in ("docs.md", "runbook.md"):
        fm, fm_errors = _read_frontmatter_file(app_dir / md)
        errors.extend(fm_errors)
        if fm is None:
            continue
        if fm.get("catalog_entity") != catalog_name:
            errors.append(
                f"{app}/{md}: catalog_entity {fm.get('catalog_entity')!r} "
                f"does not match catalog-info.yaml metadata.name {catalog_name!r}")
        for src in fm.get("sources", []) or []:
            # .exists() (not .is_file()) so a directory source such as
            # terraform/modules/argocd is accepted.
            if not (repo_root / src).exists():
                errors.append(f"{app}/{md}: sources path does not exist: {src}")
    return errors


def check_index_coverage(repo_root: Path) -> list[str]:
    index = repo_root / "base-apps" / "_INDEX.md"
    if not index.is_file():
        return ["base-apps/_INDEX.md is missing"]
    index_text = index.read_text()
    errors = []
    for child in sorted((repo_root / "base-apps").iterdir()):
        if not child.is_dir():
            continue
        if f"| {child.name} " not in index_text and f"|{child.name}|" not in index_text:
            errors.append(f"base-apps/_INDEX.md has no row for app '{child.name}'")
    return errors


def check_staleness(repo_root: Path, apps: list[str], today: date, max_age_days: int = 180) -> list[str]:
    warnings = []
    for app in apps:
        for md in ("docs.md", "runbook.md"):
            path = repo_root / "base-apps" / app / md
            if not path.is_file():
                continue
            try:
                fm = parse_frontmatter(path.read_text())
                reviewed = _as_date(fm["last_reviewed"])
            except (ValueError, KeyError):
                continue
            age = (today - reviewed).days
            if age > max_age_days:
                warnings.append(f"{app}/{md}: last_reviewed {reviewed} is {age} days old (>{max_age_days})")
    return warnings


def _load_scope(repo_root: Path) -> list[str]:
    scope = repo_root / "scripts" / "agent-docs-scope.txt"
    return [ln.strip() for ln in scope.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate the agent-docs contract.")
    parser.add_argument("--repo-root", default=".", type=Path)
    parser.add_argument("--staleness-fails", action="store_true")
    parser.add_argument("--today", default=None, help="override today (YYYY-MM-DD) for testing")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    today = _as_date(args.today) if args.today else date.today()
    apps = _load_scope(repo_root)

    errors: list[str] = []
    for app in apps:
        errors.extend(check_app_contract(repo_root, app))
    errors.extend(check_index_coverage(repo_root))

    warnings = check_staleness(repo_root, apps, today)

    for w in warnings:
        print(f"WARN: {w}")
    for e in errors:
        print(f"ERROR: {e}")

    if args.staleness_fails:
        errors.extend(warnings)
    if errors:
        print(f"\nagent-docs validation FAILED with {len(errors)} error(s).")
        return 1
    print(f"\nagent-docs validation passed ({len(apps)} apps in scope, {len(warnings)} warning(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
