# tests/beacon/test_homelab_seed.py
"""beacon §T.17: the homelab tenant monitors every host on the shared Gateway."""
import json
import re
from urllib.parse import urlsplit

from conftest import REPO, load, one

AUTHN = REPO / "ansible/roles/k3s_node/files/authn/authn-config.yaml"


def seed():
    cm = one("base-apps/beacon/configmaps.yaml", "ConfigMap", "beacon-homelab-seed")
    return json.loads(cm["data"]["homelab.json"])


def gateway_hosts():
    gw = one("base-apps/istio-ingress/gateway.yaml", "Gateway", "main")
    return {l["hostname"] for l in gw["spec"]["listeners"]
            if l.get("hostname", "").endswith(".arigsela.com") and not l["hostname"].startswith("*")}


def host_of(m):
    return urlsplit(m["target"]).hostname if m["kind"] == "http" else m["target"].rsplit(":", 1)[0]


def test_every_gateway_host_is_monitored_and_nothing_else():
    monitored = {host_of(m) for m in seed()["monitors"]}
    missing, extra = gateway_hosts() - monitored, monitored - gateway_hosts()
    assert not missing, (f"Gateway hosts without a homelab monitor: {sorted(missing)}. Add one to "
                         "beacon-homelab-seed in base-apps/beacon/configmaps.yaml (beacon §T.17).")
    assert not extra, f"homelab monitors for hosts the Gateway no longer serves: {sorted(extra)}"


def test_monitors_are_well_formed():
    s = seed()
    assert s["slug"] == "homelab" and s["tier"] == "free"
    names = [m["name"] for m in s["monitors"]]
    assert len(names) == len(set(names))
    for m in s["monitors"]:
        assert m["kind"] in ("http", "tcp") and m["interval_seconds"] >= 60
        if m["kind"] == "http":
            assert m["target"].startswith("https://")
        else:
            assert re.fullmatch(r"[a-z0-9.-]+:\d+", m["target"])


def test_owner_is_the_subject_the_api_server_pins():
    pinned = re.search(r'claims\.sub == "([^"]+)"', AUTHN.read_text()).group(1)
    assert seed()["owners"] == [pinned]


def test_seed_job_runs_after_the_workloads_with_the_app_role():
    job = next(d for d in load("base-apps/beacon/jobs.yaml") if d["metadata"]["name"] == "beacon-seed-homelab")
    assert job["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PostSync"
    c = job["spec"]["template"]["spec"]["containers"][0]
    assert c["args"] == ["seed-tenant"]
    env = {e["name"]: e for e in c["env"]}
    assert env["DATABASE_URL"]["valueFrom"]["secretKeyRef"]["name"] == "beacon-db-app"
    assert env["BEACON_SEED_FILE"]["value"] == "/etc/beacon/seed/homelab.json"
