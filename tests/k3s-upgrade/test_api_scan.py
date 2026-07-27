"""Continuous enforcement of SPEC.md §V.10 — removed-API scan clean vs target minors.

§T.4 ran this once and recorded the result in docs/plans/k3s-1.36-api-scan.md. That is a
snapshot; this is the guard. Manifests land in base-apps/ continuously, and the walk to
1.36 spans weeks, so a one-off scan would go stale long before the last hop.

Scope note: pluto only knows *built-in* Kubernetes API deprecations. CRD version removals
are invisible to it, and that is where this repo's real exposure sits — 59 manifests on
external-secrets.io/v1beta1, which ESO removes in 0.17.0. See the report for the full
picture; do not read a green run here as "safe to hop".
"""
import subprocess
from pathlib import Path

import pytest

from conftest import requires_docker

REPO = Path(__file__).resolve().parents[2]
PLUTO = "us-docker.pkg.dev/fairwinds-ops/oss/pluto:v5"
TARGETS = ["v1.34.0", "v1.35.0", "v1.36.0"]


@pytest.mark.slow
@requires_docker
@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("directory", ["base-apps", "charts"])
def test_api_scan_clean(target, directory):
    """§V.10: no manifest may use an API removed in any minor on the path to 1.36."""
    result = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{REPO}:/repo:ro", PLUTO,
         "detect-files", "-d", f"/repo/{directory}",
         "--target-versions", f"k8s={target}"],
        capture_output=True, text=True, timeout=300,
    )
    combined = result.stdout + result.stderr
    assert "no resources found with known deprecated apiVersions" in combined, (
        f"§V.10 violation scanning {directory}/ against k8s={target}:\n{combined}"
    )


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
