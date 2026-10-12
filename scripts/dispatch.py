#!/usr/bin/env python3
"""Signal dispatcher — routes a Grafana alert to a read-only agent investigation, by table.

    POST /signal/grafana      Grafana contact-point webhook payload (its second integration)
    GET  /healthz             process is up
    GET  /readyz              the table loaded

Humans get every alert regardless (Grafana → n8n → Slack on its own integration, V86);
this service only ADDS investigations. For each alert: match the table
(/etc/dispatch/dispatch.json, see base-apps/dispatcher/dispatch-table.yaml), check the
budget, and for an `agent:<name>` route open a NEW A2A session as X-User-Id `dispatcher`
with the rendered prompt, then post the finding to n8n's agent-findings webhook (→ Slack).

Never forwards the alert to n8n, never answers an approval or a question (V88): if the
agent pauses, the finding says so and points at the kagent UI. Logs one JSON line per
decision and per finding, never the answer text.

Environment (all have defaults for the cluster):
    DISPATCH_TABLE          /etc/dispatch/dispatch.json
    KAGENT_A2A_BASE         http://kagent-controller.kagent.svc.cluster.local:8083
    N8N_WEBHOOK_BASE        http://n8n.n8n.svc.cluster.local:5678/webhook
    DISPATCH_USER           dispatcher
    INVESTIGATION_TIMEOUT   300   (seconds the A2A send may block)
    KAGENT_UI_SESSION_URL   https://kagent.arigsela.com/agents/{session}/chat
    PORT                    8080
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kagent_a2a as a2a  # noqa: E402

ANSWER_LIMIT = 3000       # chars of the agent's answer forwarded to Slack
SOURCES = {"grafana"}


class TableError(ValueError):
    """The dispatch table is malformed; refuse to serve rather than route wrongly."""


# ------------------------------------------------------------------ the table

def load_table(text: str) -> dict:
    table = json.loads(text)
    if table.get("version") != 1:
        raise TableError("version must be 1")
    limits = table.get("limits") or {}
    for key in ("investigations_per_hour", "repeat_after_hours"):
        if not isinstance(limits.get(key), int) or limits[key] < 0:
            raise TableError(f"limits.{key} must be a non-negative integer")
    seen = set()
    for rule in table.get("rules") or []:
        rid = rule.get("id") or "?"
        for key in ("id", "source", "match", "route", "why"):
            if key not in rule:
                raise TableError(f"rule {rid}: missing {key}")
        if rule["id"] in seen:
            raise TableError(f"duplicate rule id {rule['id']}")
        seen.add(rule["id"])
        if rule["source"] not in SOURCES:
            raise TableError(f"rule {rid}: unknown source {rule['source']!r}")
        if not isinstance(rule["match"], dict) or not rule["match"]:
            raise TableError(f"rule {rid}: match must be a non-empty object")
        route = rule["route"]
        if route != "none" and not (isinstance(route, str) and route.startswith("agent:")):
            raise TableError(f"rule {rid}: route must be 'none' or 'agent:<name>', got {route!r}")
        prompt = rule.get("prompt")
        if isinstance(prompt, list):          # a list of strings keeps the JSON lines short
            if not all(isinstance(p, str) for p in prompt):
                raise TableError(f"rule {rid}: prompt list must contain only strings")
            rule["prompt"] = prompt = " ".join(p.strip() for p in prompt)
        if route.startswith("agent:") and not str(prompt or "").strip():
            raise TableError(f"rule {rid}: an agent route needs a prompt")
        if not str(rule["why"]).strip():
            raise TableError(f"rule {rid}: why is required")
    table.setdefault("rules", [])
    return table


def match_rule(table: dict, source: str, labels: dict) -> dict | None:
    """First rule of this source whose match keys all equal the alert's labels."""
    for rule in table["rules"]:
        if rule["source"] != source:
            continue
        if all(labels.get(k) == v for k, v in rule["match"].items()):
            return rule
    return None


class _Safe(dict):
    """A mapping that renders a missing key as '?' instead of raising."""

    def __missing__(self, key):
        return "?"


def render_prompt(rule: dict, alert: dict) -> str:
    labels = _Safe(alert.get("labels") or {})
    return rule["prompt"].format_map(_Safe(
        alertname=labels.get("alertname", "?"),
        status=alert.get("status") or "?",
        starts_at=alert.get("startsAt") or "?",
        labels=labels,
        annotations=_Safe(alert.get("annotations") or {}),
    ))


# ------------------------------------------------------------------ the budget

class Budget:
    """Bounds investigations: at most `per_hour` sends, and one per fingerprint per
    `repeat_after_hours`. In memory: a restart forgets, which errs on the side of looking."""

    def __init__(self, per_hour: int, repeat_after_hours: int, clock=time.time):
        self.per_hour = per_hour
        self.repeat_s = repeat_after_hours * 3600
        self.clock = clock
        self._starts: list[float] = []
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def admit(self, fingerprint: str) -> str | None:
        now = self.clock()
        with self._lock:
            self._starts = [t for t in self._starts if now - t < 3600]
            last = self._seen.get(fingerprint)
            if last is not None and now - last < self.repeat_s:
                return "repeat"
            if len(self._starts) >= self.per_hour:
                return "budget"
            self._starts.append(now)
            self._seen[fingerprint] = now
            return None
