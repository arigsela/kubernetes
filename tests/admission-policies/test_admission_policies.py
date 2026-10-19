"""Native admission policies (base-apps/admission-policies/), tested against a real API server.

The static tests at the bottom run without Docker; the rest boot k3s (see conftest.py).
"""
import json
from pathlib import Path

import pytest
import yaml

from conftest import (ARGO, FIXTURES, REPO, _wait, api_server_panics, expected_pull_secrets,
                      policy_docs, policy_files, real_kagent_files)

SUITES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir() and p.name != "crds")
GOOD = sorted(FIXTURES.glob("*/good/*.yaml"))
BAD = sorted(FIXTURES.glob("*/bad/*/*.yaml"))
MUTATE = sorted(FIXTURES.glob("*/mutate/*/*.yaml"))


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


@pytest.mark.parametrize("path", real_kagent_files(),
                         ids=lambda p: "/".join(p.relative_to(p.parents[1]).with_suffix("").parts))
def test_real_kagent_manifests_update_without_warnings(cluster, path):
    """Every kagent object actually deployed (Agents, AgentTemplates incl. build-orchestrator,
    Harnesses, ModelConfigs, MCP servers) must pass every policy — on UPDATE, the path a sync
    takes. A policy that flags a real object would block it the day it enforces."""
    verdict, fired, out = cluster.verdict(path.read_text(), update=True)
    assert verdict == "allow", f"real {path.name} got {verdict} from {fired}:\n{out}"


def test_real_kagent_manifests_are_all_there():
    """The harness must exercise the real agents, not a subset: eight Agents and their eight
    AgentTemplates. A glob that silently found nothing would make the test above vacuous."""
    kinds = {}
    for p in real_kagent_files():
        for d in yaml.safe_load_all(p.read_text()):
            if d:
                kinds.setdefault(d["kind"], set()).add(d["metadata"]["name"])
    want = {"homelab-knowledge", "k8s-reader", "k8s-agent", "istio-agent", "build-orchestrator",
            "skill-suggester", "dungeon-crawler-carl-agent", "implementer", "restarter"}
    assert kinds.get("Agent") == want, kinds.get("Agent")
    assert kinds.get("AgentTemplate") == want, kinds.get("AgentTemplate")
    assert kinds.get("Harness"), "no Harness found"


@pytest.mark.parametrize("path", MUTATE, ids=_id)
def test_mutation_fixture_gets_expected_pull_secrets(cluster, path):
    """A real Pod: the imagePullSecrets it is admitted with must be exactly the expected set,
    with no duplicates. Order is not asserted: a keyed-list merge may append or re-sort."""
    text = path.read_text()
    expected = expected_pull_secrets(text)
    got = cluster.admitted_pull_secrets(text)
    assert len(got) == len(set(got)), f"{_id(path)}: duplicate pull secrets {got}"
    assert sorted(got) == expected, f"{_id(path)}: got {got}, expected {expected}"


def test_api_server_service_is_exempt(cluster):
    """default/kubernetes belongs to the API server and cannot move, so
    disallow-default-namespace must not flag it. It already exists, so no create fixture can
    cover it: dry-run an UPDATE instead."""
    r = cluster.kubectl("label", "service", "kubernetes", "-n", "default", "probe=1",
                        "--dry-run=server")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ValidatingAdmissionPolicy" not in r.stderr, r.stderr


# --- agent-definitions-from-git: who may write an agent definition (SPEC §V.101) -------------

WRITERS = "agent-definitions-from-git"
BREAK_GLASS = "homelab:kagent-break-glass"
KAGENT_CONTROLLER = "system:serviceaccount:kagent:kagent-controller"
GC = ("system:serviceaccount:kube-system:generic-garbage-collector", ())
# Real objects the harness created as Argo CD, one per kind the policy matches.
LIVE = {"template": ("agenttemplates.api.kagent.dev", "k8s-reader"),
        "agent": ("agents.api.kagent.dev", "k8s-reader"),
        "harness": ("harnesses.api.kagent.dev", "kagent-stateless"),
        "server": ("remotemcpservers.api.kagent.dev", "agent-docs")}
# Every identity with write RBAC on these kinds in the live cluster (2026-10-19), except Argo CD's
# application controller: none of them is git.
NOT_GIT = {"x509-admin": ("system:admin", ("system:masters",)),
           "oidc-owner": ("oidc:arigsela", ()),
           "kagent-ui-api": (KAGENT_CONTROLLER, ()),
           "argocd-server": ("system:serviceaccount:argo-cd:argocd-server", ()),
           "garbage-collector": GC}
MAY_WRITE = {"argo-cd": (ARGO, ()),
             "break-glass-oidc": ("oidc:arigsela", (BREAK_GLASS,)),
             "break-glass-x509": ("system:admin", ("system:masters", BREAK_GLASS))}


def _patch(cluster, live, patch, who, *extra):
    (resource, name), (user, groups) = LIVE[live], who
    return cluster.verdict_of("patch", resource, name, "-n", "kagent", "--type=merge", "-p",
                              json.dumps(patch), "--dry-run=server", *extra,
                              as_user=user, as_groups=groups)


@pytest.mark.parametrize("who", NOT_GIT)
@pytest.mark.parametrize("live", LIVE)
def test_direct_change_to_a_live_definition_is_denied(cluster, live, who):
    """The UPDATE path, for every kind and every non-git writer. A label is part of the
    definition: the class label is one, and kagent selects on labels."""
    verdict, fired, out = _patch(cluster, live, {"metadata": {"labels": {"probe": "x"}}},
                                 NOT_GIT[who])
    assert verdict == "deny" and WRITERS in fired, out


def test_direct_spec_change_is_denied(cluster):
    verdict, fired, out = _patch(cluster, "template", {"spec": {"description": "edited live"}},
                                 NOT_GIT["x509-admin"])
    assert verdict == "deny" and WRITERS in fired, out


@pytest.mark.parametrize("who", MAY_WRITE)
@pytest.mark.parametrize("live", LIVE)
def test_argo_and_break_glass_may_change_a_live_definition(cluster, live, who):
    verdict, fired, out = _patch(cluster, live, {"metadata": {"labels": {"probe": "x"}}},
                                 MAY_WRITE[who])
    assert verdict == "allow", out


@pytest.mark.parametrize("patch", [
    {"metadata": {"finalizers": ["test.homelab/probe"]}},
    {"metadata": {"ownerReferences": [{"apiVersion": "v1", "kind": "ConfigMap", "name": "probe",
                                       "uid": "00000000-0000-0000-0000-000000000001"}]}},
], ids=["finalizer", "owner-reference"])
@pytest.mark.parametrize("live", LIVE)
def test_metadata_only_update_is_allowed(cluster, live, patch):
    """What the garbage collector writes in a foreground prune or an orphaning (finalizers,
    ownerReferences) changes nothing an agent does, so it is allowed by its shape, not by who
    sends it. The same identity changing a label is denied (test above)."""
    verdict, fired, out = _patch(cluster, live, patch, GC)
    assert verdict == "allow", out


def test_hop_verify_replace_of_a_live_template_is_allowed(cluster):
    """scripts/hop-verify.sh (check_admission_policies, step 4) re-applies a live read/write
    AgentTemplate UNCHANGED with `kubectl replace --dry-run=server` as the owner's x509 admin, to
    prove the delegation policy synced. An unchanged object is not a direct edit: denying it
    would fail every hop gate. Same stripping as the script."""
    r = cluster.kubectl("get", "agenttemplates.api.kagent.dev", "k8s-reader", "-n", "kagent",
                        "-o", "json")
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    o.pop("status", None)
    for k in ("managedFields", "resourceVersion", "uid", "creationTimestamp", "generation"):
        o["metadata"].pop(k, None)
    verdict, fired, out = cluster.verdict_of("replace", "--dry-run=server", "-f", "-",
                                             input=json.dumps(o), as_user="system:admin",
                                             as_groups=("system:masters",))
    assert verdict == "allow", out


def test_foreground_prune_of_a_definition_completes(cluster):
    """Argo CD prunes with foreground propagation: the API server keeps the object, with the
    foregroundDeletion finalizer, until the garbage collector removes it by UPDATE. Denying that
    update hangs the prune (§B.18, §V.77). Delete as Argo, then remove the finalizer as the
    garbage collector; k3s's own collector may get there first, which proves the same."""
    resource, name = "agenttemplates.api.kagent.dev", "probe-foreground-prune"
    text = yaml.safe_dump({
        "apiVersion": "api.kagent.dev/v1alpha3", "kind": "AgentTemplate",
        "metadata": {"name": name, "namespace": "kagent",
                     "labels": {"capability.homelab/class": "read"}},
        "spec": {"modelConfig": {"name": "anthropic-claude-haiku-5-5"}, "systemPrompt": "probe"}})
    r = cluster.kubectl("create", "-f", "-", input=text, as_user=ARGO)
    assert r.returncode == 0, r.stdout + r.stderr
    r = cluster.kubectl("delete", resource, name, "-n", "kagent", "--cascade=foreground",
                        "--wait=false", as_user=ARGO)
    assert r.returncode == 0, r.stdout + r.stderr
    r = cluster.kubectl("patch", resource, name, "-n", "kagent", "--type=json", "-p",
                        '[{"op": "remove", "path": "/metadata/finalizers"}]', as_user=GC[0])
    assert r.returncode == 0 or "NotFound" in r.stderr, r.stdout + r.stderr
    _wait(lambda: "NotFound" in cluster.kubectl("get", resource, name, "-n", "kagent").stderr,
          60, f"{name} deleted")


@pytest.mark.parametrize("live", ["agent", "server"])
def test_status_writes_are_not_restricted(cluster, live):
    """The kagent controller writes only the status subresource (live managedFields; alpha14
    source), a different resource the policy does not match."""
    verdict, fired, out = _patch(cluster, live, {"status": {"observedGeneration": 1}},
                                 (KAGENT_CONTROLLER, ()), "--subresource=status")
    assert verdict == "allow", out


def test_no_type_checking_warnings(cluster):
    """VAPs only: on 1.36 a MutatingAdmissionPolicy gets no status at all (no typeChecking),
    so a MAP is checked only by its mutate fixtures and test_api_server_did_not_panic."""
    r = cluster.kubectl("get", "validatingadmissionpolicies", "-o", "json")
    assert r.returncode == 0, r.stderr
    for p in json.loads(r.stdout)["items"]:
        warnings = (p.get("status", {}).get("typeChecking") or {}).get("expressionWarnings") or []
        assert not warnings, f"{p['metadata']['name']}: {warnings}"


def test_api_server_did_not_panic(cluster):
    """Runs after the tests above (definition order). A policy expression can panic the API
    server's request handler, e.g. a JSONPatch whose value is a list of typed objects; the
    request then fails with a 500 whatever the failurePolicy. Catch any such panic by name."""
    panics = api_server_panics()
    assert not panics, "\n".join(panics)


# --- static: no cluster needed ----------------------------------------------------------------

def _by_kind(kind):
    return {d["metadata"]["name"]: d for d in policy_docs() if d.get("kind") == kind}


def test_policy_files_exist():
    assert policy_files(), "base-apps/admission-policies/ has no policy manifests"


@pytest.mark.parametrize("kind", ["ValidatingAdmissionPolicy", "MutatingAdmissionPolicy"])
def test_every_policy_has_a_binding(kind):
    policies = _by_kind(kind)
    bound = {b["spec"]["policyName"] for b in _by_kind(kind + "Binding").values()}
    assert set(policies) <= bound, f"{kind}s with no binding: {set(policies) - bound}"


def test_mutations_fail_open():
    """A mutating policy with failurePolicy Fail turns one CEL error into 'no pod can be
    created' cluster-wide. Ignore degrades to one pod missing the mutation instead."""
    for name, map_ in _by_kind("MutatingAdmissionPolicy").items():
        assert map_["spec"].get("failurePolicy") == "Ignore", f"{name} can block every pod"


def test_every_mutating_policy_has_fixtures():
    covered = {p.parent.name for p in MUTATE}
    missing = set(_by_kind("MutatingAdmissionPolicy")) - covered
    assert not missing, f"no mutate fixtures for: {missing}"


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
         "agents": "Agent", "agenttemplates": "AgentTemplate", "harnesses": "Harness",
         "remotemcpservers": "RemoteMCPServer", "externalsecrets": "ExternalSecret"}


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


REPORTING_LABEL = "reports.kyverno.io/enabled"


def _request_dependent(vap) -> bool:
    """The verdict depends on the request (who sent it, CREATE or UPDATE, the old object)."""
    text = json.dumps(vap["spec"])
    return "request." in text or "oldObject" in text


def test_kyverno_reporting_opt_in():
    """Kyverno's reports controller (v1.19) skips a native policy unless it is labelled
    reports.kyverno.io/enabled=true: opt-in, the reverse of Kyverno's own policies. Without it
    a policy's violations appear in no PolicyReport. A parameterised policy must NOT opt in:
    Kyverno's engine mis-evaluates paramKind policies, so its report rows would be wrong. Nor may
    a request-dependent one: a background scan has no request, so every object it matches would
    be reported as a violation (§V.76)."""
    for name, vap in _by_kind("ValidatingAdmissionPolicy").items():
        labelled = (vap["metadata"].get("labels") or {}).get(REPORTING_LABEL) == "true"
        if "paramKind" in vap["spec"]:
            assert not labelled, f"{name} is parameterised: Kyverno would misreport it"
        elif _request_dependent(vap):
            assert not labelled, f"{name} depends on the request: a background scan has none"
        else:
            assert labelled, f"{name} lacks {REPORTING_LABEL}=true: reported nowhere"


def _writers_policy():
    return _by_kind("ValidatingAdmissionPolicy")[WRITERS]


def test_agent_definitions_policy_covers_every_definition_kind_cluster_wide():
    """Main resources only (status is a separate resource), CREATE and UPDATE, every
    namespace: the kagent controller watches all of them (KAGENT_WATCH_NAMESPACES is empty), so
    an Agent created anywhere runs."""
    mc = _writers_policy()["spec"]["matchConstraints"]
    assert "namespaceSelector" not in mc and "objectSelector" not in mc, mc
    for rule in mc["resourceRules"]:
        assert rule["apiGroups"] == ["api.kagent.dev"] and set(rule["operations"]) == {"CREATE",
                                                                                      "UPDATE"}
    assert {r for rule in mc["resourceRules"] for r in rule["resources"]} == {
        "agents", "agenttemplates", "harnesses", "remotemcpservers"}


def test_harness_argo_identity_is_the_policys():
    """The harness submits as ARGO, and the policy exempts exactly ARGO and the break-glass
    group: one exempt identity each, nothing else."""
    conds = _writers_policy()["spec"]["matchConditions"]
    text = " ".join(c["expression"] for c in conds)
    assert f"'{ARGO}'" in text and f"'{BREAK_GLASS}'" in text, text
    assert text.count("system:serviceaccount:") == 1 and text.count("request.userInfo") == 2, text


def _byo_images():
    tax = next(d for d in yaml.safe_load_all(
        (REPO / "base-apps/admission-policies/agent-capability-taxonomy.yaml").read_text())
        if d and d.get("kind") == "ConfigMap")
    return json.loads(tax["data"]["byo-images"])


def test_byo_fixture_images_are_listed():
    """A good byo fixture runs exactly a byo-images entry, so pinning the digest updates both."""
    byo = [p for p in GOOD if "byo" in (yaml.safe_load(p.read_text()).get("spec") or {})]
    assert byo, "no good byo fixture: the byo exception is untested"
    for p in byo:
        image = yaml.safe_load(p.read_text())["spec"]["workload"]["image"]
        assert image in _byo_images(), f"{_id(p)}: {image} is not in byo-images"


def test_every_policy_has_a_message():
    for name, vap in _by_kind("ValidatingAdmissionPolicy").items():
        for v in vap["spec"]["validations"]:
            assert v.get("message") or v.get("messageExpression"), f"{name} has a silent validation"


def _served_crd_versions():
    out = set()
    for f in sorted((FIXTURES / "crds").glob("*.yaml")):
        for d in yaml.safe_load_all(f.read_text()):
            if d and d.get("kind") == "CustomResourceDefinition":
                for v in d["spec"]["versions"]:
                    if v.get("served"):
                        out.add((d["spec"]["group"], v["name"], d["spec"]["names"]["plural"]))
    return out


def test_kagent_policies_match_a_served_version():
    """SPEC §V.84: a guardrail must not pass vacuously. A policy that matches a kagent
    group/version/resource the (real, rendered) CRDs do not serve matches NOTHING: after a
    kagent API bump it would admit everything while looking green. Bump the CRD fixture with
    the chart, and this fails until every kagent policy is moved to the new version."""
    served = _served_crd_versions()
    for name, vap in _by_kind("ValidatingAdmissionPolicy").items():
        for rule in vap["spec"]["matchConstraints"]["resourceRules"]:
            for group in rule["apiGroups"]:
                if not group.endswith("kagent.dev"):
                    continue
                for version in rule["apiVersions"]:
                    for res in rule["resources"]:
                        assert (group, version, res) in served, (
                            f"{name} matches {group}/{version} {res}, which the kagent CRDs "
                            f"in fixtures/crds do not serve")
        pk = vap["spec"].get("paramKind")
        if pk and pk["apiVersion"].split("/")[0].endswith("kagent.dev"):
            group, version = pk["apiVersion"].split("/")
            kinds = {(d["spec"]["group"], d["spec"]["names"]["kind"])
                     for f in sorted((FIXTURES / "crds").glob("*.yaml"))
                     for d in yaml.safe_load_all(f.read_text()) if d}
            assert (group, pk["kind"]) in kinds, f"{name}: paramKind {pk} is not a served CRD"
            assert any(g == group and v == version for g, v, _ in served), pk
