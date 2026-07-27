"""Every PVC manifest must opt out of Argo pruning (SPEC.md §V.19).

`local-path` is the only StorageClass in this cluster and it reclaims `Delete`, so a
PersistentVolume is destroyed along with its claim. Every Argo Application runs
`prune: true`. That combination means a manifest rename, a bad Helm values change, or a
chart bump that stops rendering a claim silently deletes the data behind it.

This was not hypothetical: an audit on 2026-07-27 found 10 PVCs live in prune scope,
including postgresql, ollama, n8n and grafana. The fix was a `Prune=false` sync-option on
each manifest. This test exists so the next PVC added to the repo cannot quietly
reintroduce the exposure.

Deliberate deletion is still possible — remove the annotation in a commit, which is
reviewable, rather than having it happen as a side effect of an unrelated change.
"""
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
BASE_APPS = REPO / "base-apps"
SYNC_OPTIONS = "argocd.argoproj.io/sync-options"

# Claims created by a StatefulSet volumeClaimTemplate are made by the StatefulSet
# controller, not applied by Argo, so they are never in prune scope. vault-data-vault-0
# is the case that matters here.
def _pvc_docs():
    for path in sorted(BASE_APPS.rglob("*.yaml")):
        try:
            docs = list(yaml.safe_load_all(path.read_text()))
        except yaml.YAMLError:
            continue  # malformed YAML is yamllint's job, not this test's
        for doc in docs:
            if isinstance(doc, dict) and doc.get("kind") == "PersistentVolumeClaim":
                yield path, doc


def test_pvc_manifests_exist():
    """Guard against the glob silently matching nothing and the suite passing vacuously."""
    found = list(_pvc_docs())
    assert len(found) >= 9, f"expected the known PVC manifests, found {len(found)}"


@pytest.mark.parametrize(
    "path,doc",
    [pytest.param(p, d, id=f"{d['metadata'].get('namespace','?')}/{d['metadata']['name']}")
     for p, d in _pvc_docs()],
)
def test_pvc_opts_out_of_prune(path, doc):
    """§V.19: no PVC may sit in Argo's prune scope."""
    annotations = doc["metadata"].get("annotations") or {}
    opts = annotations.get(SYNC_OPTIONS, "")
    assert "Prune=false" in opts, (
        f"{path.relative_to(REPO)} declares a PVC without '{SYNC_OPTIONS}: Prune=false'.\n"
        f"local-path reclaims Delete, so an Argo prune of this claim destroys the volume "
        f"behind it. Add the annotation, or if deletion really is intended, say so in the "
        f"commit message."
    )


def test_atlantis_app_has_prune_disabled():
    """atlantis renders its PVC from a Helm chart whose values expose no annotations
    (6.1.0: enabled/dataStorage/storageClassName/accessModes only), so the protection has
    to live on the Application instead."""
    app = yaml.safe_load((BASE_APPS / "atlantis.yaml").read_text())
    automated = app["spec"]["syncPolicy"]["automated"]
    assert automated.get("prune") is False, (
        "base-apps/atlantis.yaml must keep prune: false — the chart renders atlantis-data "
        "as a standalone PVC on local-path and offers no way to annotate it."
    )
