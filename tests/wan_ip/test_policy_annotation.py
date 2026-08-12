"""The wan-ip annotation must stay consistent with the rules it describes.

The reconciler replaces exactly the IP named in the annotation. If someone
edits the ipBlocks by hand without moving the annotation, the reconciler would
later rewrite the wrong /32 - or silently no-op. This test makes that
divergence a CI failure instead of a 3am surprise.
"""
import pathlib

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
POLICY = REPO / "base-apps" / "istio-ingress" / "authorizationpolicy.yaml"
ANNOTATION = "arigsela.com/wan-ip"


def _policy():
    return yaml.safe_load(POLICY.read_text())


def test_annotation_present():
    ann = (_policy()["metadata"].get("annotations") or {})
    assert ANNOTATION in ann, f"{ANNOTATION} annotation missing from gateway-allow"


def test_annotation_value_appears_in_every_restricted_rule():
    doc = _policy()
    wan = doc["metadata"]["annotations"][ANNOTATION]
    restricted = [r for r in doc["spec"]["rules"] if "from" in r]
    assert restricted, "expected at least one restricted rule"
    for rule in restricted:
        blocks = rule["from"][0]["source"]["ipBlocks"]
        host = rule["to"][0]["operation"]["hosts"][0]
        assert f"{wan}/32" in blocks, f"{host} does not allow the declared WAN IP {wan}"


def test_no_other_ip_looks_like_a_stale_wan_entry():
    """Every restricted rule should carry the same declared WAN /32 exactly once."""
    doc = _policy()
    wan = f"{doc['metadata']['annotations'][ANNOTATION]}/32"
    for rule in [r for r in doc["spec"]["rules"] if "from" in r]:
        blocks = rule["from"][0]["source"]["ipBlocks"]
        assert blocks.count(wan) == 1, f"expected exactly one {wan}, got {blocks}"
