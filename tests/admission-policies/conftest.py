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
[Warn, Audit] to [Deny].
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
K3S_IMAGE = "rancher/k3s:v1.36.4-k3s1"
NAME = "admission-harness-pytest"
NAMESPACES = ["kagent", "team-a", "postgresql", "admission-test"]
NOT_MANIFESTS = {"catalog-info.yaml", "mkdocs.yml"}
EXPECT = "test.homelab/expect-pull-secrets"


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0


def policy_files():
    return sorted(p for p in POLICIES.glob("*.yaml") if p.name not in NOT_MANIFESTS)


def real_agent_files():
    return sorted((REPO / "base-apps" / "kagent" / "agents").glob("*.yaml"))


def policy_docs():
    return [d for p in policy_files() for d in yaml.safe_load_all(p.read_text()) if d]


def _docker(*args, input=None):
    return subprocess.run(["docker", *args], capture_output=True, text=True, input=input)


class Cluster:
    def kubectl(self, *args, input=None):
        return _docker("exec", "-i", NAME, "kubectl", *args, input=input)

    def apply(self, text):
        r = self.kubectl("apply", "-f", "-", input=text)
        assert r.returncode == 0, r.stdout + r.stderr

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
    _docker("rm", "-f", NAME)
    r = _docker("run", "-d", "--name", NAME, "--privileged", K3S_IMAGE, "server",
                "--disable", "traefik", "--disable", "metrics-server",
                "--disable", "local-storage", "--disable", "servicelb")
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
            c.apply(crd.read_text())
        _wait(lambda: c.kubectl("wait", "--for=condition=established", "crd", "--all",
                                "--timeout=5s").returncode == 0, 60, "CRDs established")
        # "Established" is not enough: policy type checking reads the schema from the
        # published OpenAPI, which lags a little. A policy created in that window is
        # type-checked against nothing ("undefined field 'spec'"). Wait until each CRD's
        # schema is actually served.
        for crd in sorted((FIXTURES / "crds").glob("*.yaml")):
            plural_group = yaml.safe_load(crd.read_text())["metadata"]["name"]
            _wait(lambda: c.kubectl("explain", f"{plural_group}.spec").returncode == 0, 60,
                  f"OpenAPI schema for {plural_group}")
        for p in policy_files():
            c.apply(p.read_text())
        # The cluster's real Agents, created for real: they are the delegation policy's
        # parameters (a delegate must exist to be judged), and each must itself be clean.
        for p in real_agent_files():
            c.apply(p.read_text())
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
        _docker("rm", "-f", NAME)
