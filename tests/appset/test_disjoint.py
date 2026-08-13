"""master-app and the managed-apps ApplicationSet must never own the same name.

master-app applies every top-level base-apps/*.yaml. If an Application name
also appears in appsets/managed-apps/, two controllers write the same object
and fight over it. This test is the only thing standing between a future
copy-paste and that outcome.
"""
import yaml


def _top_level_application_names(repo_root):
    names = set()
    for path in sorted((repo_root / "base-apps").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict) and doc.get("kind") == "Application":
            names.add(doc["metadata"]["name"])
    return names


def test_appset_names_disjoint_from_app_of_apps(configs, repo_root):
    generated = {cfg["name"] for cfg in configs.values()}
    hand_written = _top_level_application_names(repo_root)
    overlap = generated & hand_written
    assert not overlap, f"owned by both master-app and managed-apps: {sorted(overlap)}"


def test_applicationset_manifest_exists_and_is_wellformed(repo_root):
    path = repo_root / "base-apps" / "managed-apps.yaml"
    doc = yaml.safe_load(path.read_text())
    assert doc["kind"] == "ApplicationSet"
    assert doc["metadata"]["name"] == "managed-apps"
    assert doc["metadata"]["namespace"] == "argo-cd"
    assert doc["spec"]["goTemplate"] is True
    assert doc["spec"]["goTemplateOptions"] == ["missingkey=error"]
    assert doc["spec"]["syncPolicy"]["preserveResourcesOnDeletion"] is True
    files = doc["spec"]["generators"][0]["git"]["files"]
    assert files == [{"path": "appsets/managed-apps/*.yaml"}]
