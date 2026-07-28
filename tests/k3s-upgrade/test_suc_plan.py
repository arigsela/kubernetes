"""Guards on the system-upgrade-controller rig (SPEC.md §T.13-§T.16).

Two of these properties are safety-critical and easy to undo by accident.

**No drain.** `local-path` is the only StorageClass and its PersistentVolumes carry
hard node affinity, so a drained pod with a volume cannot reschedule anywhere — it
sits Pending until the node returns. The Plan CRD documents that omitting `drain`
performs no drain, so the guard is that the key stays absent. Adding `drain: {}`
would look harmless and would strand Vault, Postgres, and every other stateful pod.

**Never the control plane.** §V.17 keeps SUC away from `k3s-control-01`: this is a
single-server cluster, and SUC runs upgrades as a privileged Job scheduled by the very
API server it would restart. That is enforced twice — by not labelling the node
(§T.15) and by the Plan's own nodeSelector — because the label is cluster state that
no test can see, while the selector lives in git where this test can.
"""
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SUC_DIR = REPO / "base-apps" / "system-upgrade-controller"
APP = REPO / "base-apps" / "system-upgrade-controller.yaml"


def _docs(path: Path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _plans():
    return [d for f in SUC_DIR.glob("*.yaml") for d in _docs(f) if d.get("kind") == "Plan"]


def test_rig_exists():
    """Guard against the globs silently matching nothing and the suite passing vacuously."""
    assert APP.exists(), "system-upgrade-controller Application missing"
    assert _plans(), "no Plan manifests found"


def test_no_plan_targets_the_control_plane():
    """§V.17: SUC scope is agents only."""
    for plan in _plans():
        exprs = plan["spec"].get("nodeSelector", {}).get("matchExpressions", [])
        guarded = any(
            e.get("key") == "node-role.kubernetes.io/control-plane"
            and e.get("operator") == "DoesNotExist"
            for e in exprs
        )
        assert guarded, (
            f"Plan {plan['metadata']['name']} does not exclude the control plane. "
            f"SUC on a single-server cluster can strand it (§V.17)."
        )


def test_no_plan_server_manifest_exists():
    """§I declares plan-server.yaml must never exist."""
    assert not (SUC_DIR / "plan-server.yaml").exists(), (
        "plan-server.yaml would point SUC at k3s-control-01 (§V.17)."
    )


@pytest.mark.parametrize("plan", _plans(), ids=lambda p: p["metadata"]["name"])
def test_plan_does_not_drain(plan):
    """Draining strands every pod with a node-pinned local-path volume."""
    assert "drain" not in plan["spec"], (
        f"Plan {plan['metadata']['name']} sets `drain`. local-path PVs have hard node "
        f"affinity, so drained stateful pods cannot reschedule and sit Pending until "
        f"the node returns. Omit the key entirely — the CRD treats absent as no-drain."
    )


@pytest.mark.parametrize("plan", _plans(), ids=lambda p: p["metadata"]["name"])
def test_plan_pins_a_version_not_a_channel(plan):
    """§V.3: one deliberate minor at a time.

    A `channel` resolves to whatever upstream calls latest via HTTP redirect, so a Plan
    using one silently retargets when a release is cut.
    """
    spec = plan["spec"]
    assert spec.get("version"), f"Plan {plan['metadata']['name']} must pin `version`"
    assert "channel" not in spec, (
        f"Plan {plan['metadata']['name']} uses `channel`, which follows upstream "
        f"releases automatically and would skip the deliberate one-minor walk."
    )


@pytest.mark.parametrize("plan", _plans(), ids=lambda p: p["metadata"]["name"])
def test_plan_upgrades_one_node_at_a_time(plan):
    """There are only two workers; concurrency 2 would take both out together."""
    assert plan["spec"].get("concurrency") == 1, (
        f"Plan {plan['metadata']['name']} must set concurrency: 1"
    )


def test_sync_waves_order_crd_before_controller():
    """The Plan CRD must exist before the controller, and both before any Plan, or the
    first sync fails on an unknown kind."""
    waves = {}
    for f in SUC_DIR.glob("*.yaml"):
        for d in _docs(f):
            w = int(d.get("metadata", {}).get("annotations", {})
                    .get("argocd.argoproj.io/sync-wave", 0))
            waves.setdefault(d["kind"], w)
    assert waves.get("CustomResourceDefinition", 0) < waves.get("Deployment", 0), (
        f"CRD must sync before the controller: {waves}"
    )
    assert waves.get("Deployment", 0) < waves.get("Plan", 0), (
        f"controller must sync before any Plan: {waves}"
    )
