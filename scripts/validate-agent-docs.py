#!/usr/bin/env python3
"""Validate the agent-docs contract across the repo.

Checks (hard failures, exit 1): contract-file presence for in-scope apps,
frontmatter validity, link/source resolution, catalog_entity name match,
base-apps index coverage, and that each in-scope app's Argo CD Application
excludes its catalog-info.yaml via spec.source.directory.exclude (co-located
catalog-info.yaml files would otherwise fail Argo CD sync). Staleness is a
warning unless --staleness-fails.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

import yaml

REQUIRED_KEYS = [
    # OKF v0.1 interop fields (see templates/agent-docs/README.md).
    "type", "title", "description",
    # This repo's own agent-docs contract.
    "app", "catalog_entity", "kind", "namespace", "last_reviewed", "status", "tags", "sources",
]
KIND_VALUES = {"docs", "runbook"}
STATUS_VALUES = {"current", "wip", "deprecated"}
# `kind` stays the single source of truth; `type` is its OKF-facing label and
# must agree, so external consumers and the validator cannot disagree.
TYPE_FOR_KIND = {"docs": "Kubernetes App Guide", "runbook": "Kubernetes App Runbook"}


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
    kind = fm.get("kind")
    if kind not in KIND_VALUES:
        errors.append(f"kind must be one of {sorted(KIND_VALUES)}, got {kind!r}")
    elif fm.get("type") != TYPE_FOR_KIND[kind]:
        errors.append(
            f"type must be {TYPE_FOR_KIND[kind]!r} for kind {kind!r}, got {fm.get('type')!r}")
    for key in ("title", "description"):
        value = fm.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key} must be a non-empty string")
    description = fm.get("description")
    if isinstance(description, str):
        # description is rendered into a markdown table cell in base-apps/index.md,
        # so a newline or an unescaped pipe would corrupt the generated index.
        if "\n" in description.strip():
            errors.append("description must be a single line")
        if "|" in description:
            errors.append("description must not contain '|' (breaks the generated index table)")
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
CATALOG_KINDS = {"Component", "Resource"}


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
    metadata = catalog.get("metadata") or {}
    catalog_name = metadata.get("name")

    if catalog.get("apiVersion") != "backstage.io/v1alpha1":
        errors.append(
            f"{app}: catalog-info.yaml apiVersion must be 'backstage.io/v1alpha1', "
            f"got {catalog.get('apiVersion')!r}")
    if catalog.get("kind") not in CATALOG_KINDS:
        errors.append(
            f"{app}: catalog-info.yaml kind must be one of {sorted(CATALOG_KINDS)}, "
            f"got {catalog.get('kind')!r}")
    if (metadata.get("annotations") or {}).get("agent-docs/path") != "docs.md":
        errors.append(
            f"{app}: catalog-info.yaml must set annotation 'agent-docs/path: docs.md'")

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
    index = repo_root / "base-apps" / "index.md"
    if not index.is_file():
        return ["base-apps/index.md is missing"]
    index_text = index.read_text()
    errors = []
    for child in sorted((repo_root / "base-apps").iterdir()):
        if not child.is_dir():
            continue
        if f"| {child.name} " not in index_text and f"|{child.name}|" not in index_text:
            errors.append(f"base-apps/index.md has no row for app '{child.name}'")
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


def _exclude_covers_catalog(exclude) -> bool:
    """Whether an Argo CD Application's spec.source.directory.exclude covers
    catalog-info.yaml (accepts a string glob or a list of globs)."""
    if isinstance(exclude, str):
        return "catalog-info.yaml" in exclude
    if isinstance(exclude, list):
        return any("catalog-info.yaml" in str(e) for e in exclude)
    return False


def _app_manifest_excludes_catalog(repo_root: Path, app: str) -> tuple[bool, bool]:
    """Find the Argo CD Application whose source.path is base-apps/<app> and
    report (found, excludes_catalog_info). Matching by source.path (not file
    name) handles apps synced by a differently-named manifest, e.g.
    cert-manager-config.yaml sources base-apps/cert-manager."""
    base_apps = repo_root / "base-apps"
    if not base_apps.is_dir():
        return (False, False)
    for yml in sorted(base_apps.glob("*.yaml")):
        try:
            docs = list(yaml.safe_load_all(yml.read_text()))
        except yaml.YAMLError:
            continue
        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") != "Application":
                continue
            source = (doc.get("spec") or {}).get("source") or {}
            if source.get("path") != f"base-apps/{app}":
                continue
            exclude = (source.get("directory") or {}).get("exclude")
            return (True, _exclude_covers_catalog(exclude))
    return (False, False)


def check_app_directory_exclude(repo_root: Path, app: str) -> list[str]:
    """A co-located catalog-info.yaml is a Backstage entity, not a Kubernetes
    manifest, sitting inside an Argo CD-synced directory. Its Argo CD
    Application MUST set `spec.source.directory.exclude` to cover
    catalog-info.yaml — otherwise Argo CD would try to apply it and fail sync.
    This in-band guard is the framework's load-bearing safety mechanism (a
    global resource.exclusions is not relied upon; see argocd.tf note)."""
    if not (repo_root / "base-apps" / app / "catalog-info.yaml").is_file():
        return []
    found, excludes = _app_manifest_excludes_catalog(repo_root, app)
    if not found:
        return [
            f"{app}: no Argo CD Application (a base-apps/*.yaml with "
            f"spec.source.path 'base-apps/{app}') found to exclude its "
            f"catalog-info.yaml from sync."
        ]
    if not excludes:
        return [
            f"{app}: the Argo CD Application for base-apps/{app} must set "
            f"spec.source.directory.exclude to cover 'catalog-info.yaml' so "
            f"Argo CD does not try to apply the Backstage entity."
        ]
    return []


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
        errors.extend(check_app_directory_exclude(repo_root, app))
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
