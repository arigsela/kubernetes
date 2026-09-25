"""Guards on scripts/resize-pod.sh, run against a stub kubectl (no cluster needed)."""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "resize-pod.sh"


def _pod(resources, qos, labels=None, owner="StatefulSet"):
    return {
        "metadata": {"name": "p-0", "labels": labels or {},
                     "ownerReferences": [{"kind": owner, "name": "p"}]},
        "spec": {"containers": [{"name": "app", "resources": resources}]},
        "status": {"qosClass": qos, "containerStatuses": [{"name": "app", "restartCount": 3}]},
    }


def _run(tmp_path, pod, *args):
    """Run the script with a kubectl stub that serves `pod` and logs every patch call."""
    (tmp_path / "pod.json").write_text(json.dumps(pod))
    log = tmp_path / "patches.log"
    stub = tmp_path / "bin" / "kubectl"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text(f"""#!/usr/bin/env bash
case "$1" in
  get)   cat {tmp_path / 'pod.json'} ;;
  patch) printf '%s\\n' "$*" >> {log} ;;
esac
""")
    stub.chmod(0o755)
    env = {"PATH": f"{stub.parent}:{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(tmp_path)}
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env)
    return r, (log.read_text() if log.exists() else "")


BURSTABLE = {"requests": {"cpu": "200m", "memory": "1Gi"}, "limits": {"cpu": "1", "memory": "2Gi"}}


def test_syntax_and_help():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    r = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "resize-pod.sh <namespace> <pod> <container>" in r.stdout


def test_requires_a_resource(tmp_path):
    r, patches = _run(tmp_path, _pod(BURSTABLE, "Burstable"), "ns", "p-0", "app", "--apply")
    assert r.returncode == 2 and "at least one" in r.stderr and not patches


def test_rejects_a_bad_quantity(tmp_path):
    r, patches = _run(tmp_path, _pod(BURSTABLE, "Burstable"), "ns", "p-0", "app", "cpu=lots/1")
    assert r.returncode == 2 and "not a Kubernetes quantity" in r.stderr and not patches


def test_refuses_a_qos_change_before_calling_the_api(tmp_path):
    """BestEffort -> Burstable is exactly what the API rejects (vault-0 before Task 2.3)."""
    r, patches = _run(tmp_path, _pod({}, "BestEffort"), "ns", "p-0", "app", "cpu=100m/500m", "--apply")
    assert r.returncode == 2 and "BestEffort -> Burstable" in r.stderr
    assert not patches, "no resize may be attempted when the QoS class would change"


def test_refuses_cloudnative_pg(tmp_path):
    pod = _pod(BURSTABLE, "Burstable", labels={"cnpg.io/cluster": "pg"}, owner="Cluster")
    r, patches = _run(tmp_path, pod, "ns", "p-0", "app", "memory=1Gi/2Gi", "--apply")
    assert r.returncode == 2 and "CloudNativePG" in r.stderr and not patches


def test_guaranteed_stays_guaranteed_across_units(tmp_path):
    """'2' and '2000m' are the same CPU; a string compare would call this Burstable."""
    pod = _pod({"requests": {"cpu": "1", "memory": "1Gi"}, "limits": {"cpu": "1000m", "memory": "1024Mi"}},
               "Guaranteed")
    r, patches = _run(tmp_path, pod, "ns", "p-0", "app", "cpu=2/2000m", "memory=2Gi/2048Mi")
    assert r.returncode == 0, r.stderr
    assert "Guaranteed (unchanged)" in r.stdout


def test_dry_run_sends_the_expected_patch(tmp_path):
    r, patches = _run(tmp_path, _pod(BURSTABLE, "Burstable"), "ns", "p-0", "app", "cpu=250m/1500m")
    assert r.returncode == 0, r.stderr
    assert "--subresource resize" in patches and "--dry-run=server" in patches
    body = json.loads(patches.split(" -p ", 1)[1].strip())
    assert body == {"spec": {"containers": [{"name": "app", "resources": {
        "requests": {"cpu": "250m"}, "limits": {"cpu": "1500m"}}}]}}
    assert "re-run with --apply" in r.stdout
