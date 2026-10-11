"""Real-API-server harness for base-apps/admission-policies/ (plan Task 3.1).

Static tools cannot be trusted for native admission policies: kubeconform skips v1
MutatingAdmissionPolicy files, and the kyverno CLI mis-evaluates MAPs and parameterised VAPs.
So these tests boot a real k3s, the same version as the cluster, apply the policies, and
server-side dry-run fixtures against them.

Fixture layout encodes the expectation:
    fixtures/<suite>/good/*.yaml                 must be admitted with no policy warning
    fixtures/<suite>/bad/<policy-name>/*.yaml    must be warned about (shadow) or denied
                                                 (enforcing) BY THAT POLICY
    fixtures/<suite>/mutate/<policy-name>/*.yaml a Pod created FOR REAL; its admitted
                                                 imagePullSecrets must equal the annotation
                                                 test.homelab/expect-pull-secrets
The verdict is mode-agnostic, so the same fixtures keep passing when a binding moves from
[Warn, Audit] to [Deny], with one rule: a bad fixture must violate ONLY its own policy. In
warn mode every violated policy is reported; with [Deny] the API server reports just the first
policy that denies, so a fixture that also broke another policy would be credited to it.
"""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
POLICIES = REPO / "base-apps" / "admission-policies"
FIXTURES = Path(__file__).parent / "fixtures"
# Pinned to the cluster's version (SPEC T21). Bump together with the cluster.
K3S_IMAGE = "rancher/k3s:v1.37.1-k3s1"
NAME = "admission-harness-pytest"
# ate-system / podcertificate-controller-system: the hygiene policies' Substrate exclusion
# (fixtures/workload-hygiene/good/exempt-*-namespace-deployment.yaml).
NAMESPACES = ["kagent", "team-a", "postgresql", "admission-test",
              "ate-system", "podcertificate-controller-system"]
NOT_MANIFESTS = {"catalog-info.yaml", "mkdocs.yml"}
EXPECT = "test.homelab/expect-pull-secrets"


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0


def policy_files():
    return sorted(p for p in POLICIES.glob("*.yaml") if p.name not in NOT_MANIFESTS)


KAGENT = REPO / "base-apps" / "kagent"
# Creation order for the real kagent manifests. Admission does not check references, but the
# delegation policy's parameters are AgentTemplates: a subagent's template must exist for its
# parent to be judged against it, so templates without subagents go first.
KIND_ORDER = ["ModelConfig", "RemoteMCPServer", "MCPServer", "Harness", "AgentTemplate", "Agent"]


def _is_kagent(doc) -> bool:
    return str(doc.get("apiVersion", "")).split("/")[0] in ("api.kagent.dev", "kagent.dev")


def _apply_rank(path: Path):
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    kind = docs[0]["kind"]
    has_subagent = any(t.get("subAgent") for d in docs for t in (d.get("spec") or {}).get("tools") or [])
    return (KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER), has_subagent, path.name)


def real_kagent_files():
    """Every kagent manifest in base-apps/kagent (Agents, AgentTemplates, Harnesses,
    ModelConfigs, MCP servers), found by apiVersion, not directory, in creation order. They are
    created for real: the delegation policy's parameters are the AgentTemplates, and every one
    must itself pass every policy (a policy that flags a real object would block it on sync)."""
    files = [p for p in KAGENT.rglob("*.yaml")
             if any(d and _is_kagent(d) for d in yaml.safe_load_all(p.read_text()))]
    return sorted(files, key=_apply_rank)


def crd_names():
    return [d["metadata"]["name"] for f in sorted((FIXTURES / "crds").glob("*.yaml"))
            for d in yaml.safe_load_all(f.read_text()) if d]


def policy_docs():
    return [d for p in policy_files() for d in yaml.safe_load_all(p.read_text()) if d]


# Always `rm -fv`: the k3s image declares VOLUMEs, so every run otherwise leaves ~4 anonymous
# volumes behind (1602 had piled up locally by 2026-09-27, ~14 GB).
def _docker(*args, input=None):
    return subprocess.run(["docker", *args], capture_output=True, text=True, input=input)


class Cluster:
    def kubectl(self, *args, input=None):
        return _docker("exec", "-i", NAME, "kubectl", *args, input=input)

    def apply(self, text, server_side=False):
        # Server-side for the CRDs: the real mcpservers.kagent.dev CRD is ~390 KB, over the
        # 256 KB limit of client-side apply's last-applied-configuration annotation.
        extra = ("--server-side",) if server_side else ()
        r = self.kubectl("apply", *extra, "-f", "-", input=text)
        assert r.returncode == 0, r.stdout + r.stderr

    def apply_when_params_synced(self, text, timeout=90):
        """apply, retrying ONLY the transient denial of a parameterised policy whose param
        informer the API server has not synced yet ("failed to configure binding: paramKind
        ... not yet synced to use for admission"). With failurePolicy Fail that denies for a
        moment after the policy is created; it failed a whole CI session once. Any other
        failure is final."""
        deadline = time.time() + timeout
        while True:
            r = self.kubectl("apply", "-f", "-", input=text)
            if r.returncode == 0:
                return
            assert "not yet synced" in r.stderr and time.time() < deadline, r.stdout + r.stderr
            time.sleep(2)

    def verdict(self, text, update=False):
        """(verdict, {policy names that fired}, raw output) for a server-side dry run: a
        create, or with update=True an apply over the existing object (the UPDATE path)."""
        verb = ("apply",) if update else ("create",)
        r = self.kubectl(*verb, "--dry-run=server", "-f", "-", input=text)
        out = r.stdout + r.stderr
        fired = set(re.findall(r"ValidatingAdmissionPolicy '([^']+)'", out))
        if r.returncode != 0:
            return ("deny" if fired else "error"), fired, out
        return ("warn" if fired else "allow"), fired, out


    def dry_run_pull_secrets(self, text):
        r = self.kubectl("create", "--dry-run=server", "-o", "json", "-f", "-", input=text)
        if r.returncode != 0:
            return None
        return sorted(s["name"] for s in json.loads(r.stdout)["spec"].get("imagePullSecrets", []))

    def admitted_pull_secrets(self, text):
        """Create the Pod for real (a dry run is not proof of what gets persisted), read back
        its imagePullSecrets names in order, delete it."""
        doc = yaml.safe_load(text)
        ns, name = doc["metadata"]["namespace"], doc["metadata"]["name"]
        r = self.kubectl("create", "-f", "-", input=text)
        assert r.returncode == 0, r.stdout + r.stderr
        try:
            r = self.kubectl("get", "pod", "-n", ns, name, "-o", "json")
            assert r.returncode == 0, r.stderr
            return [s["name"] for s in json.loads(r.stdout)["spec"].get("imagePullSecrets", [])]
        finally:
            self.kubectl("delete", "pod", "-n", ns, name, "--wait=false", "--grace-period=0",
                         "--force")


def expected_pull_secrets(text):
    raw = yaml.safe_load(text)["metadata"]["annotations"][EXPECT]
    return sorted(s for s in raw.split(",") if s)


def input_pull_secrets(text):
    return sorted(s["name"] for s in yaml.safe_load(text)["spec"].get("imagePullSecrets", []))


def api_server_panics():
    """Panics the API server recovered from. A recovered panic still fails the request, and
    failurePolicy does not apply to it (seen with a MutatingAdmissionPolicy on 1.36)."""
    logs = _docker("logs", NAME)
    return [l for l in (logs.stdout + logs.stderr).splitlines() if "Observed a panic" in l]


def _wait(cond, timeout, what, soft=False):
    """soft: on timeout, carry on. Used for "policy is active" waits, so a broken policy makes
    its own tests fail by name instead of erroring the whole session."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return
        time.sleep(2)
    if not soft:
        raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture(scope="session")
def cluster():
    if not docker_available():
        pytest.skip("needs a running Docker daemon for the real-API-server harness")
    _docker("rm", "-fv", NAME)
    r = _docker("run", "-d", "--name", NAME, "--privileged", K3S_IMAGE, "server",
                "--disable", "traefik", "--disable", "metrics-server",
                "--disable", "local-storage", "--disable", "servicelb",
                # 1.37 bundles Gateway API CRDs; prod disables them (SPEC §V.83), so does the harness
                "--disable", "gateway-api-crd")
    assert r.returncode == 0, r.stderr
    c = Cluster()
    try:
        _wait(lambda: c.kubectl("get", "--raw", "/readyz").stdout.strip() == "ok", 180, "API server ready")
        for ns in NAMESPACES:
            c.apply(f"apiVersion: v1\nkind: Namespace\nmetadata:\n  name: {ns}\n")
        # A Pod is admitted only once its namespace's default ServiceAccount exists; the
        # controller creates it a moment after the namespace.
        for ns in NAMESPACES + ["default", "kube-system"]:
            _wait(lambda: c.kubectl("get", "serviceaccount", "default", "-n", ns).returncode == 0,
                  60, f"default ServiceAccount in {ns}")
        for crd in sorted((FIXTURES / "crds").glob("*.yaml")):
            c.apply(crd.read_text(), server_side=True)
        _wait(lambda: c.kubectl("wait", "--for=condition=established", "crd", "--all",
                                "--timeout=5s").returncode == 0, 60, "CRDs established")
        # "Established" is not enough: policy type checking reads the schema from the
        # published OpenAPI, which lags a little. A policy created in that window is
        # type-checked against nothing ("undefined field 'spec'"). Wait until each CRD's
        # schema is actually served.
        for plural_group in crd_names():
            _wait(lambda: c.kubectl("explain", f"{plural_group}.spec").returncode == 0, 60,
                  f"OpenAPI schema for {plural_group}")
        for p in policy_files():
            c.apply(p.read_text())
        # The cluster's real kagent objects, created for real: the AgentTemplates are the
        # delegation policy's parameters (a subagent must exist to be judged), and each object
        # must itself be clean. Riding out only the param-informer lag (SPEC §V.78).
        for p in real_kagent_files():
            c.apply_when_params_synced(p.read_text())
        # A new policy takes a moment to reach the admission plugin. Wait until every policy
        # that has a bad fixture fires on at least one: ANY one, so a policy broken for one
        # kind still starts and that fixture fails by name instead of the whole session.
        for pdir in sorted(FIXTURES.glob("*/bad/*")):
            samples = [p.read_text() for p in sorted(pdir.glob("*.yaml"))]
            _wait(lambda: any(pdir.name in c.verdict(s)[1] for s in samples), 90,
                  f"policy {pdir.name} active", soft=True)
        # Same for mutating policies: wait until a dry run of a fixture that EXPECTS A CHANGE
        # comes back with it (read from the object, not grepped: the expectation annotation
        # itself contains the names; and a no-change fixture proves nothing).
        for pdir in sorted(FIXTURES.glob("*/mutate/*")):
            samples = [s for s in (p.read_text() for p in sorted(pdir.glob("*.yaml")))
                       if expected_pull_secrets(s) != input_pull_secrets(s)]
            _wait(lambda: any(c.dry_run_pull_secrets(s) == expected_pull_secrets(s)
                              for s in samples), 90, f"policy {pdir.name} active", soft=True)
        yield c
    finally:
        _docker("rm", "-fv", NAME)
