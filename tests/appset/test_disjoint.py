"""master-app and the managed-apps ApplicationSet must never own the same
Application name, nor point two different Applications at the same
spec.source.path.

master-app applies every top-level base-apps/*.yaml. If an Application name
also appears in appsets/managed-apps/, two controllers write the same object
and fight over it -- caught by test_appset_names_disjoint_from_app_of_apps.
But a name match isn't the only way to get double ownership: this repo has
several Applications whose name differs from the directory they source
(argo-cd-config, atlantis-config, cert-manager-config, kagent-secrets,
istio-gateway-api), so two differently-named Applications can still sync the
same directory with prune+selfHeal and fight over the tracking label instead.
test_appset_source_paths_disjoint_from_app_of_apps catches that form. Together
these are the only things standing between a future copy-paste (or a rename
made to dodge the name check) and that outcome.
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


def _top_level_application_source_paths(repo_root):
    paths = set()
    for path in sorted((repo_root / "base-apps").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict) and doc.get("kind") == "Application":
            source_path = doc.get("spec", {}).get("source", {}).get("path")
            if source_path:
                paths.add(source_path)
    return paths


def test_appset_names_disjoint_from_app_of_apps(configs, repo_root):
    generated = {cfg["name"] for cfg in configs.values()}
    hand_written = _top_level_application_names(repo_root)
    overlap = generated & hand_written
    assert not overlap, f"owned by both master-app and managed-apps: {sorted(overlap)}"


def test_appset_source_paths_disjoint_from_app_of_apps(configs, repo_root):
    """Name-disjointness alone isn't enough: two Applications with different
    names but the same spec.source.path both sync one directory with
    prune+selfHeal and fight over the tracking label -- the same hazard the
    name check guards against, just reachable by a rename instead of a
    copy-paste. See the module docstring.
    """
    generated = {cfg["sourcePath"] for cfg in configs.values()}
    hand_written = _top_level_application_source_paths(repo_root)
    overlap = generated & hand_written
    assert not overlap, (
        f"source path owned by both master-app and managed-apps: {sorted(overlap)}"
    )


def test_applicationset_manifest_exists_and_is_wellformed(repo_root):
    path = repo_root / "base-apps" / "managed-apps.yaml"
    doc = yaml.safe_load(path.read_text())
    assert doc["kind"] == "ApplicationSet"
    assert doc["metadata"]["name"] == "managed-apps"
    assert doc["metadata"]["namespace"] == "argo-cd"
    assert doc["spec"]["goTemplate"] is True
    assert doc["spec"]["goTemplateOptions"] == ["missingkey=error"]
    assert doc["spec"]["syncPolicy"]["preserveResourcesOnDeletion"] is True
    # create-update, not the default: a malformed config skips its entry rather
    # than halting the render, so under the default policy a typo could put an
    # app on the deletion path. Measured 2026-08-13.
    assert doc["spec"]["syncPolicy"]["applicationsSync"] == "create-update"
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
