"""Native admission policies (base-apps/admission-policies/), tested against a real API server.

The static tests at the bottom run without Docker; the rest boot k3s (see conftest.py).
"""
import json
from pathlib import Path

import pytest
import yaml

from conftest import FIXTURES, policy_docs, policy_files, real_agent_files

SUITES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir() and p.name != "crds")
GOOD = sorted(FIXTURES.glob("*/good/*.yaml"))
BAD = sorted(FIXTURES.glob("*/bad/*/*.yaml"))


def _id(p: Path) -> str:
    return "/".join(p.relative_to(FIXTURES).with_suffix("").parts)


# --- against a real API server --------------------------------------------------------------

@pytest.mark.parametrize("path", GOOD, ids=_id)
def test_good_fixture_is_admitted_without_warnings(cluster, path):
    verdict, fired, out = cluster.verdict(path.read_text())
    assert verdict == "allow", f"{_id(path)} should be clean but got {verdict} from {fired}:\n{out}"


@pytest.mark.parametrize("path", BAD, ids=_id)
def test_bad_fixture_is_flagged_by_its_policy(cluster, path):
    """Warned (shadow) or denied (enforcing) — and by the policy its directory names, so a
    fixture caught by the wrong rule does not count as coverage."""
    expected = path.parent.name
    verdict, fired, out = cluster.verdict(path.read_text())
    assert verdict in ("warn", "deny"), f"{_id(path)} was {verdict}:\n{out}"
    assert expected in fired, f"{_id(path)} fired {fired}, expected {expected}:\n{out}"


@pytest.mark.parametrize("path", real_agent_files(), ids=lambda p: p.stem)
def test_real_agents_update_without_warnings(cluster, path):
    """Every Agent actually deployed must pass every policy — on UPDATE, the path a sync
    takes. A policy that flags a real agent would block that agent the day it enforces."""
    verdict, fired, out = cluster.verdict(path.read_text(), update=True)
    assert verdict == "allow", f"real agent {path.stem} got {verdict} from {fired}:\n{out}"


def test_api_server_service_is_exempt(cluster):
    """default/kubernetes belongs to the API server and cannot move, so
    disallow-default-namespace must not flag it. It already exists, so no create fixture can
    cover it: dry-run an UPDATE instead."""
    r = cluster.kubectl("label", "service", "kubernetes", "-n", "default", "probe=1",
                        "--dry-run=server")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ValidatingAdmissionPolicy" not in r.stderr, r.stderr


def test_no_type_checking_warnings(cluster):
    r = cluster.kubectl("get", "validatingadmissionpolicies", "-o", "json")
    assert r.returncode == 0, r.stderr
    for vap in json.loads(r.stdout)["items"]:
        warnings = (vap.get("status", {}).get("typeChecking") or {}).get("expressionWarnings") or []
        assert not warnings, f"{vap['metadata']['name']}: {warnings}"


# --- static: no cluster needed ----------------------------------------------------------------

def _by_kind(kind):
    return {d["metadata"]["name"]: d for d in policy_docs() if d.get("kind") == kind}


def test_policy_files_exist():
    assert policy_files(), "base-apps/admission-policies/ has no policy manifests"


def test_every_policy_has_a_binding():
    vaps = _by_kind("ValidatingAdmissionPolicy")
    bound = {b["spec"]["policyName"] for b in _by_kind("ValidatingAdmissionPolicyBinding").values()}
    assert set(vaps) <= bound, f"policies with no binding: {set(vaps) - bound}"


def test_failure_policy_matches_rollout_stage():
    """Shadow ([Warn, Audit]) must not block on a CEL evaluation error: failurePolicy Ignore.
    Enforcing ([Deny]) must not fail open: failurePolicy Fail. The flip is one change."""
    vaps = _by_kind("ValidatingAdmissionPolicy")
    for b in _by_kind("ValidatingAdmissionPolicyBinding").values():
        vap = vaps[b["spec"]["policyName"]]
        fp = vap["spec"].get("failurePolicy", "Fail")
        if "Deny" in b["spec"]["validationActions"]:
            assert fp == "Fail", f"{b['metadata']['name']} denies but fails open"
        else:
            assert fp == "Ignore", f"{b['metadata']['name']} is shadow but can block on error"


def test_every_policy_has_bad_fixtures():
    """A policy nothing exercises is a policy nobody knows works."""
    covered = {p.parent.name for p in BAD}
    missing = set(_by_kind("ValidatingAdmissionPolicy")) - covered
    assert not missing, f"no bad fixtures for: {missing}"


# Resource (plural) -> kind, for the resources the policies match. Extend with a new policy.
KINDS = {"pods": "Pod", "deployments": "Deployment", "statefulsets": "StatefulSet",
         "daemonsets": "DaemonSet", "jobs": "Job", "cronjobs": "CronJob", "services": "Service",
         "agents": "Agent", "externalsecrets": "ExternalSecret"}


def test_every_matched_kind_has_a_bad_fixture():
    """The API server does not type-check `variables`, so a policy that extracts the pod spec
    per kind (Deployment: spec.template.spec, CronJob: spec.jobTemplate...) is only checked by
    fixtures. Each kind a policy matches needs a bad fixture of that kind."""
    for name, vap in _by_kind("ValidatingAdmissionPolicy").items():
        matched = set()
        for rule in vap["spec"]["matchConstraints"]["resourceRules"]:
            for res in rule["resources"]:
                assert res in KINDS, f"{name}: add {res!r} to KINDS"
                matched.add(KINDS[res])
        covered = {yaml.safe_load(p.read_text())["kind"] for p in BAD if p.parent.name == name}
        assert matched <= covered, f"{name}: no bad fixture for kinds {matched - covered}"


def test_every_policy_has_a_message():
    for name, vap in _by_kind("ValidatingAdmissionPolicy").items():
        for v in vap["spec"]["validations"]:
            assert v.get("message") or v.get("messageExpression"), f"{name} has a silent validation"
