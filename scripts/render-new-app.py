#!/usr/bin/env python3
"""Render the new-app scaffolder skeleton locally (proxy for Backstage's
fetch:template) so the output can be run through the repo's CI validators.

Backstage uses Nunjucks with variable delimiters `${{ }}`; Jinja2 configured the
same way renders the common subset we use (variables, {% if %}, {% for %}) and
templated file/dir NAMES. This is a faithful pre-deploy proxy; the authoritative
check remains Backstage's template-editor dry-run.
"""
import argparse
import json
import sys
from pathlib import Path

from jinja2 import Environment, StrictUndefined

_ENV = Environment(
    variable_start_string="${{",
    variable_end_string="}}",
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)
# Nunjucks-faithful `dump` filter (== JSON.stringify, no spaces). Backstage
# templates render via Nunjucks, not Jinja2; Jinja2's default str() of a list
# (`['a', 'b']`) is not valid YAML, while Nunjucks' `dump` filter produces
# `["a","b"]`, which is. Match that exactly so `${{ values.tags | dump }}`
# renders identically in this harness and in real Backstage.
_ENV.filters["dump"] = lambda x: json.dumps(x, separators=(",", ":"), ensure_ascii=False)


def _render_str(text, values):
    return _ENV.from_string(text).render(values=values)


def render(skeleton_dirs, values, out_dir):
    """Render each skeleton dir into out_dir. Templates both file/dir names and
    file contents. Returns the list of written file paths."""
    written = []
    out_dir = Path(out_dir)
    for skel in skeleton_dirs:
        skel = Path(skel)
        for src in sorted(skel.rglob("*")):
            if src.is_dir():
                continue
            rel = src.relative_to(skel)
            # template each path segment (handles ${{ values.name }} in names)
            rel_rendered = Path(*[_render_str(part, values) for part in rel.parts])
            dst = out_dir / rel_rendered
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(_render_str(src.read_text(), values))
            written.append(dst)
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--template", required=True, help="templates/new-app dir")
    ap.add_argument("--values", required=True, help="JSON of parameter values")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ingress", action="store_true")
    ap.add_argument("--secrets", action="store_true")
    ap.add_argument("--config", action="store_true")
    args = ap.parse_args()
    tmpl = Path(args.template)
    dirs = [tmpl / "skeleton"]
    if args.ingress:
        dirs.append(tmpl / "skeleton-ingress")
    if args.secrets:
        dirs.append(tmpl / "skeleton-secrets")
    if args.config:
        dirs.append(tmpl / "skeleton-config")
    n = render(dirs, json.loads(args.values), Path(args.out))
    print(f"rendered {len(n)} files to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
