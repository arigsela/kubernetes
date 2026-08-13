"""master-app and the managed-apps ApplicationSet must never own the same name.

master-app applies every top-level base-apps/*.yaml. If an Application name
also appears in appsets/managed-apps/, two controllers write the same object
and fight over it. This test is the only thing standing between a future
copy-paste and that outcome.
"""
import yaml

from conftest import DEST_SERVER, REPO_URL, TARGET_REVISION


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


def test_applicationset_template_matches_expand(repo_root):
    """`expand()` in conftest.py is a hand-written Python restatement of this
    manifest's `template` and `templatePatch` -- nothing ties the two
    together automatically. Without this test, an edit to the live template
    (a swapped revision, a renamed placeholder, a deleted templatePatch)
    would leave every other test green while the manifest silently drifts
    from what expand() -- and therefore the goldens -- predict.

    Asserts against the same REPO_URL/TARGET_REVISION/DEST_SERVER constants
    expand() uses, so the manifest and the mirror are pinned to one source
    of truth.
    """
    path = repo_root / "base-apps" / "managed-apps.yaml"
    doc = yaml.safe_load(path.read_text())
    template = doc["spec"]["template"]

    assert template["metadata"]["name"] == "{{ .name }}"
    assert template["spec"]["project"] == "default"
    assert template["spec"]["source"]["repoURL"] == REPO_URL
    assert template["spec"]["source"]["targetRevision"] == TARGET_REVISION
    # The literal Go template string, not an expanded value -- this is what
    # guards against a `.path` / `.sourcePath` field-name collision.
    assert template["spec"]["source"]["path"] == "{{ .sourcePath }}"
    assert template["spec"]["destination"]["server"] == DEST_SERVER
    assert template["spec"]["destination"]["namespace"] == "{{ .namespace }}"
    assert template["spec"]["syncPolicy"]["automated"] == {
        "prune": True,
        "selfHeal": True,
    }

    template_patch = doc["spec"]["templatePatch"]
    assert template_patch
    assert "range .syncOptions" in template_patch
