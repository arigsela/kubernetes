#!/usr/bin/env python3
"""Validate that every Backstage catalog entity reference resolves to an entity
defined in git.

Scans catalog/*.yaml and base-apps/*/catalog-info.yaml, builds the set of
defined entities, then checks every relation reference (owner, system, domain,
subcomponentOf, parent, dependsOn, dependencyOf, providesApis, consumesApis,
memberOf, children) against that set. Exits non-zero if any reference is
unresolved.

Scope: git-defined entities only. References to live-ingested entities (kagent
Agents, Crossplane claims) are not in git; add them to _ALLOWLIST if referenced.
"""
import argparse
import sys
from pathlib import Path

import yaml

# scalar spec field -> default kind when a ref omits its "<kind>:" prefix
_SCALAR_FIELDS = {
    "owner": "group",
    "system": "system",
    "domain": "domain",
    "subcomponentOf": "component",
    "parent": "group",
}
# list spec field -> default kind
_LIST_FIELDS = {
    "dependsOn": "component",
    "dependencyOf": "component",
    "providesApis": "api",
    "consumesApis": "api",
    "memberOf": "group",
    "children": "group",
}

_ALLOWLIST = set()  # (kind, namespace, name) tuples for live-ingested refs


def _parse_ref(ref, default_kind, source_ns):
    """[<kind>:][<namespace>/]<name> -> (kind, namespace, name), all lowercased."""
    kind = default_kind
    namespace = source_ns
    rest = ref
    if ":" in rest:
        kind, rest = rest.split(":", 1)
    if "/" in rest:
        namespace, rest = rest.split("/", 1)
    return (kind.lower(), namespace.lower(), rest.lower())


def _iter_entities(repo_root):
    """Yield (path, entity_dict) for every catalog entity defined in git."""
    root = Path(repo_root)
    paths = sorted((root / "catalog").glob("*.yaml"))
    paths += sorted((root / "base-apps").glob("*/catalog-info.yaml"))
    for path in paths:
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict) and doc.get("kind") and isinstance(doc.get("metadata"), dict):
                yield path, doc


def scan(repo_root):
    """Return (defined: set, unresolved: list of (path, field, ref_str))."""
    entities = list(_iter_entities(repo_root))
    defined = set()
    for _path, e in entities:
        name = e["metadata"].get("name")
        if not name:
            continue
        ns = (e["metadata"].get("namespace") or "default").lower()
        defined.add((e["kind"].lower(), ns, name.lower()))

    unresolved = []
    for path, e in entities:
        source_ns = (e["metadata"].get("namespace") or "default").lower()
        spec = e.get("spec") or {}
        for field, default_kind in _SCALAR_FIELDS.items():
            ref = spec.get(field)
            if isinstance(ref, str):
                key = _parse_ref(ref, default_kind, source_ns)
                if key not in defined and key not in _ALLOWLIST:
                    unresolved.append((str(path), field, ref))
        for field, default_kind in _LIST_FIELDS.items():
            for ref in spec.get(field) or []:
                if isinstance(ref, str):
                    key = _parse_ref(ref, default_kind, source_ns)
                    if key not in defined and key not in _ALLOWLIST:
                        unresolved.append((str(path), field, ref))
    return defined, unresolved


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args()
    _defined, unresolved = scan(args.repo_root)
    if unresolved:
        print("Unresolved catalog entity references:")
        for path, field, ref in unresolved:
            print(f"  {path}: spec.{field} -> {ref!r}")
        print(f"\n{len(unresolved)} unresolved reference(s).")
        return 1
    print("All catalog entity references resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
