"""The Agent KPIs dashboard (§T.144) and the two files it is built from.

scripts/agent-capability-map.yaml says which agents serve each of the playbook's eight capability
areas; scripts/agent-kpis.py prints the hourly lines most tiles read. These tests keep the three in
step: a map that names an agent that does not exist, a tile that reads a field the job never prints,
or a stream the CronJob does not write would each show a confident number that means nothing.
"""
import base64
import importlib.util
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load("gen_agent_dashboards", REPO / "scripts" / "gen-agent-dashboards.py")
kpis = _load("agent_kpis", REPO / "scripts" / "agent-kpis.py")
cron = _load("gen_agent_audit_cronjob", REPO / "scripts" / "gen-agent-audit-cronjob.py")
MAP = yaml.safe_load((REPO / gen.CAPABILITY_MAP).read_text())


def _dashboard():
    out = REPO / "base-apps" / "logging" / "grafana-dashboards-agents.yaml"
    cm = next(d for d in yaml.safe_load_all(out.read_text()) if d and d["metadata"]["name"] == "grafana-dashboard-agent-kpis")
    return json.loads(cm["data"]["agent-kpis.json"])


def _targets():
    for p in _dashboard()["panels"]:
        for t in p.get("targets", []):
            yield p, t


def _template_class(agent):
    doc = yaml.safe_load((REPO / "base-apps/kagent/agent-templates" / f"{agent}.yaml").read_text())
    return doc["metadata"]["labels"]["capability.homelab/class"]


# --- the capability map -----------------------------------------------------------------------------

def test_the_map_has_the_playbooks_eight_areas_once_each():
    assert sorted(a["area"] for a in MAP["areas"]) == sorted(gen.AREAS) and len(gen.AREAS) == 8


def test_every_agent_in_the_map_exists_and_its_class_matches_its_mode():
    """recommend = class propose, act = class admin (every write gated, checked at admission)."""
    for area in MAP["areas"]:
        for mode in area["modes"]:
            assert mode["agents"], area["area"]
            for agent in mode["agents"]:
                assert (REPO / "base-apps/kagent/agents" / f"{agent}.yaml").exists(), agent
                assert _template_class(agent) == gen.MODE_CLASS[mode["mode"]], (area["area"], agent)


def test_a_closed_area_says_why_and_an_open_one_says_how():
    for area in MAP["areas"]:
        if area["modes"]:
            assert area.get("how") and area.get("ask"), area["area"]
        else:
            assert len(area.get("why", "").split()) >= 8, area["area"]


def test_safely_open_means_golden_tasks_for_every_agent_in_the_area():
    corpus = {p.stem for p in (REPO / gen.EVAL_CORPUS).glob("*.yaml") if not p.name.startswith("_")}
    for a in gen.capability_areas():
        assert a["safe"] == (bool(a["agents"]) and set(a["agents"]) <= corpus), a["area"]


def test_the_first_tile_shows_the_computed_counts():
    areas = gen.capability_areas()
    tile = next(p for p in _dashboard()["panels"] if p["title"].startswith("1 · "))
    exprs = {t["refId"]: t["expr"] for t in tile["targets"]}
    assert exprs == {"A": f"vector({sum(a['safe'] for a in areas)})", "B": f"vector({sum(a['open'] for a in areas)})"}
    assert f"(of {len(areas)})" in tile["title"]


# --- the job's lines, as the dashboard reads them ---------------------------------------------------

def _kpi_lines():
    fixture = json.loads((REPO / "tests/agent-audit/fixtures/kagent-v1/events.json").read_text())
    rows = [(datetime.fromisoformat(at), sid, base64.b64decode(sd) if sd else None, name, ns, base64.b64decode(data))
            for at, sid, sd, name, ns, data in fixture["rows"]]

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self):
            return self

        def execute(self, sql, params):
            pass

        def fetchall(self):
            return rows

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    pr = {"number": 1, "user": {"login": "arigsela-implementer[bot]"}, "created_at": "2026-10-11T18:00:00Z",
          "closed_at": "2026-10-11T18:30:00Z", "merged_at": "2026-10-11T18:30:00Z", "updated_at": "2026-10-11T18:30:00Z"}
    out = io.StringIO()
    kpis.main(["--taxonomy", str(REPO / "base-apps/admission-policies/agent-capability-taxonomy.yaml")],
              now=datetime(2026, 10, 11, 19, 5, tzinfo=timezone.utc), connect=Conn,
              opener=lambda req, timeout: Resp(json.dumps([pr]).encode()), out=out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_every_field_the_dashboard_reads_is_one_the_job_prints():
    lines = _kpi_lines()
    printed = {k: {str(v) for line in lines for kk, v in line.items() if kk == k} for line in lines for k in line}
    for p, t in _targets():
        q = t.get("expr", "")
        if gen.KPI.split(" | ")[0] not in q:
            continue
        for field in re.findall(r"unwrap (\w+)", q):
            assert field in printed, (p["title"], field)
        for field, value in re.findall(r'\| (\w+)!?="([^"]*)"', q):
            if field in ("check", "kind", "db", "caller", "state", "author", "outcome", "agent"):
                assert field in printed, (p["title"], field)
        for kind in re.findall(r'kind="(\w+)"', q):
            assert kind in printed["kind"], (p["title"], kind)


def test_the_dashboard_reads_the_stream_the_cronjob_writes():
    jobs = [d for d in cron.build("s", "t", "a", "r", "k") if d["metadata"]["name"].startswith("agent-kpis")]
    labels = {j["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"]["app"] for j in jobs}
    namespaces = {j["metadata"]["namespace"] for j in jobs}
    assert gen.KPI.startswith('{namespace="%s", app="%s"}' % (namespaces.pop(), labels.pop()))
    assert f'check="{kpis.CHECK}"' in gen.KPI


def test_scenario_kpis_leave_the_eval_gates_tests_out():
    """The eval gate asks every agent adversarial questions and rejects its own gated calls: neither
    is a scenario."""
    for p, t in _targets():
        q = t.get("expr", "")
        if re.search(r'kind="(task|approval)"', q) and p["title"][:1].isdigit():
            # either every caller but the eval gate, or one named caller that is not it
            assert gen.PROD in q or re.search(r'caller="(?!eval")\w', q), p["title"]


def test_ratios_show_no_data_rather_than_zero():
    """A rate with nothing behind it is unknown, not 0 %."""
    for p, t in _targets():
        q = t.get("expr", "")
        if " / " in q and t["datasource"]["type"] == "loki":
            assert not q.rstrip().endswith("or vector(0)"), p["title"]
