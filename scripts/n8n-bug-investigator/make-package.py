#!/usr/bin/env python3
"""Zip the n8n bug investigator for someone else's n8n (§T.152, the handover package).

    scripts/n8n-bug-investigator/make-package.py --out ~/Desktop/n8n-bug-investigator.zip

The zip holds one folder, n8n-bug-investigator/: the files in package/ (README.md, ADAPTERS.md,
config.example.toml, prompt.md, build.py, docker-compose.yml) and engine/ (builder.py, lib/,
nodes/). engine/lib/settings.js is package/settings.js, with neutral defaults, not this homelab's.
The same sources give the same bytes (fixed timestamps and order), so a rebuilt zip diffs clean.
"""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = "n8n-bug-investigator"
TOP = ["README.md", "ADAPTERS.md", "config.example.toml", "prompt.md", "build.py", "docker-compose.yml"]
STAMP = (2026, 1, 1, 0, 0, 0)


def entries() -> list[tuple[str, Path]]:
    """(path in the zip, source file), in zip order."""
    out = [(f"{ROOT}/{name}", HERE / "package" / name) for name in TOP]
    out.append((f"{ROOT}/engine/builder.py", HERE / "builder.py"))
    for p in sorted((HERE / "lib").glob("*.js")):
        src = HERE / "package" / "settings.js" if p.name == "settings.js" else p
        out.append((f"{ROOT}/engine/lib/{p.name}", src))
    out += [(f"{ROOT}/engine/nodes/{p.name}", p) for p in sorted((HERE / "nodes").glob("*.js"))]
    return out


def write(dest: Path) -> list[str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for name, src in entries():
            info = zipfile.ZipInfo(name, STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if name.endswith(".py") else 0o644) << 16
            z.writestr(info, src.read_bytes())
            names.append(name)
    return names


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True, help="the zip to write")
    args = ap.parse_args(argv)
    names = write(args.out)
    print(f"wrote {args.out} ({len(names)} files, {args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
