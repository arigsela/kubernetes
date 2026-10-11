"""Continuous enforcement of SPEC.md §V.10 — removed-API scan clean vs target minors.

§T.4 ran this once and recorded the result in docs/plans/k3s-1.36-api-scan.md. That is a
snapshot; this is the guard. Manifests land in base-apps/ continuously, and the walk to
1.36 spans weeks, so a one-off scan would go stale long before the last hop.

Scope note: pluto only knows *built-in* Kubernetes API deprecations. CRD version removals
are invisible to it, and that is where this repo's real exposure sits — 59 manifests on
external-secrets.io/v1beta1, which ESO removes in 0.17.0. See the report for the full
picture; do not read a green run here as "safe to hop".
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from conftest import requires_docker

REPO = Path(__file__).resolve().parents[2]
# Pinned by digest: the floating `:v5` tag sat on 5.23.5 long after newer releases shipped.
PLUTO = ("us-docker.pkg.dev/fairwinds-ops/oss/pluto:v5.24.4"
         "@sha256:0b058f9f27f05fef92a3a4325a5c02b1c250095fba360ed87c7734ea11f1d188")
TARGETS = ["v1.34.0", "v1.35.0", "v1.36.0", "v1.37.0"]
# pluto v5.24.4 knows nothing removed at or after 1.37, so 1.37 removals are declared here
# (SPEC §R.40). Without this file the v1.37.0 target would pass vacuously.
ADDITIONAL = Path(__file__).resolve().parent / "pluto-additional-versions.yaml"


def pluto_scan(path, target):
    return subprocess.run(
        ["docker", "run", "--rm", "-v", f"{path}:/scan:ro", "-v", f"{ADDITIONAL}:/additional.yaml:ro",
         PLUTO, "detect-files", "-d", "/scan", "-f", "/additional.yaml",
         "--target-versions", f"k8s={target}"],
        capture_output=True, text=True, timeout=300,
    )


@pytest.mark.slow
@requires_docker
@pytest.mark.parametrize("target", TARGETS)
# charts/ was scanned too until its only chart (smoke-test-workload, unused since the
# XSmokeTestApp composition was deleted) was removed. Add it back if a chart returns.
@pytest.mark.parametrize("directory", ["base-apps"])
def test_api_scan_clean(target, directory):
    """§V.10: no manifest may use an API removed in any minor on the path to 1.37."""
    result = pluto_scan(REPO / directory, target)
    combined = result.stdout + result.stderr
    assert "no resources found with known deprecated apiVersions" in combined, (
        f"§V.10 violation scanning {directory}/ against k8s={target}:\n{combined}"
    )


@pytest.mark.slow
@requires_docker
def test_the_1_37_target_catches_a_planted_removal():
    """The 1.37 target must not be vacuous: a scheduling.k8s.io/v1alpha2 object (dropped in 1.37,
    kubernetes#138572) has to be reported. The planted dir lives under $HOME because colima only
    shares $HOME with its VM, and it is world-readable because pluto runs as non-root: in both
    failure modes pluto sees nothing and reports the scan clean."""
    planted = Path(tempfile.mkdtemp(dir=Path.home()))
    try:
        # mkdtemp is 0700 and pluto runs as a non-root user: an unreadable dir also scans "clean".
        planted.chmod(0o755)
        manifest = planted / "workload.yaml"
        manifest.write_text(
            "apiVersion: scheduling.k8s.io/v1alpha2\nkind: Workload\nmetadata:\n  name: planted\n")
        manifest.chmod(0o644)
        result = pluto_scan(planted, "v1.37.0")
    finally:
        shutil.rmtree(planted)
    combined = result.stdout + result.stderr
    assert "planted" in combined and "scheduling.k8s.io/v1alpha2" in combined, combined


def test_additional_versions_file_declares_the_1_37_scheduling_removal():
    import yaml
    entries = yaml.safe_load(ADDITIONAL.read_text())["deprecated-versions"]
    removed = {(e["version"], e["kind"]) for e in entries if e["removed-in"] == "v1.37.0"}
    assert removed == {("scheduling.k8s.io/v1alpha2", "Workload"), ("scheduling.k8s.io/v1alpha2", "PodGroup")}


def test_scan_report_records_the_crd_blind_spot():
    """The report must keep stating pluto's coverage limit.

    A clean built-in scan reads as "cleared to hop" unless the CRD gap is spelled out
    right next to it — and the ESO v1beta1 removal is the finding that actually gates
    the walk.
    """
    report = REPO / "docs" / "plans" / "k3s-1.36-api-scan.md"
    assert report.exists(), "§T.4 report missing"
    text = report.read_text()
    assert "external-secrets.io/v1beta1" in text
    assert "0.17.0" in text, "report must name the release that removes v1beta1"
    assert "pluto" in text.lower()
