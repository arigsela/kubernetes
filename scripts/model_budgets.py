#!/usr/bin/env python3
"""The model budget table (base-apps/model-gateway/budgets.yaml) and the collector's prices.

Shared by scripts/gen-model-budgets.py, scripts/provision-model-gateway-vault.sh (through
`--groups`) and tests/model-gateway. Spec: docs/superpowers/specs/2026-10-23-model-gateway-budgets-design.md.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
BUDGETS = REPO / "base-apps/model-gateway/budgets.yaml"
COLLECTOR = REPO / "base-apps/tracing/collector.yaml"
GATEWAY_URL = "http://model-gateway.model-gateway.svc.cluster.local:8080"

_GROUP = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
_GROUP_KEYS = {"agents", "models", "daily_usd", "gateway"}


def load(path: Path = BUDGETS) -> dict:
    """The parsed table; ValueError when it breaks a rule the rest of the repo relies on."""
    table = yaml.safe_load(Path(path).read_text())
    if table.get("version") != 1:
        raise ValueError("budgets.yaml: version must be 1")
    if table.get("window") != "24h":
        raise ValueError("budgets.yaml: window must be 24h (a day from 00:00 UTC)")
    warn = table.get("warn_at")
    if not isinstance(warn, (int, float)) or not 0 < warn < 1:
        raise ValueError("budgets.yaml: warn_at must be between 0 and 1")
    groups = table.get("groups") or {}
    if not groups:
        raise ValueError("budgets.yaml: no groups")
    for name, g in groups.items():
        if not _GROUP.match(name):
            raise ValueError(f"budgets.yaml: bad group name {name!r}")
        missing = _GROUP_KEYS - set(g)
        if missing:
            raise ValueError(f"budgets.yaml: {name} lacks {sorted(missing)}")
        if not isinstance(g["daily_usd"], (int, float)) or g["daily_usd"] <= 0:
            raise ValueError(f"budgets.yaml: {name}.daily_usd must be > 0")
        if not isinstance(g["gateway"], bool):
            raise ValueError(f"budgets.yaml: {name}.gateway must be true or false")
        if not g["models"]:
            raise ValueError(f"budgets.yaml: {name} has no models")
        if not g["agents"] and not g.get("chart_default"):
            raise ValueError(f"budgets.yaml: {name} has no agents")
    return table


def group_of(path: Path = BUDGETS) -> dict[str, str]:
    """agent → group."""
    return {a: name for name, g in load(path)["groups"].items() for a in g["agents"]}


def env_name(group: str) -> str:
    """The environment variable that carries a group's virtual key into the gateway."""
    return "MODEL_GATEWAY_KEY_" + group.upper().replace("-", "_")


# One statement of transform/genai-cost: four $/MTok multipliers, the model, an optional tier.
_PRICE = re.compile(
    r'\* (?P<input>[\d.]+) \+ cache\["cache_write"\] \* (?P<cache_write>[\d.]+) '
    r'\+ cache\["cache_read"\] \* (?P<cache_read>[\d.]+) \+ cache\["output"\] \* (?P<output>[\d.]+)\)'
    r' / 1000000\.0\) where attributes\["gen_ai\.request\.model"\] == "(?P<model>[^"]+)"'
    r'(?: and cache\["input"\] > (?P<over>[\d.]+))?')


def collector_prices(path: Path = COLLECTOR) -> dict[str, list[dict]]:
    """model → price tiers from the collector's cost estimate (§V.105), base tier first."""
    out: dict[str, list[dict]] = {}
    for m in _PRICE.finditer(Path(path).read_text()):
        tier = {"over": int(float(m["over"])) if m["over"] else 0,
                **{k: float(m[k]) for k in ("input", "cache_write", "cache_read", "output")}}
        out.setdefault(m["model"], []).append(tier)
    for tiers in out.values():
        tiers.sort(key=lambda t: t["over"])
    if not out:
        raise ValueError("no prices found in the collector's transform/genai-cost")
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["--groups"]:
        print("\n".join(load()["groups"]))
    else:
        print("usage: model_budgets.py --groups", file=sys.stderr)
        raise SystemExit(2)
