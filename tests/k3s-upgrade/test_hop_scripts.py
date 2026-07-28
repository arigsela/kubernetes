"""Guards on the hop plumbing (SPEC.md §T.25, §T.28, §T.29, §T.30).

These scripts run at the moments where a mistake is most expensive — immediately before
and after a k3s hop — so the properties worth protecting are the ones that would silently
weaken the gate rather than break it loudly.
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SYNC_WINDOW = REPO / "scripts" / "argo-sync-window.sh"
HOP_VERIFY = REPO / "scripts" / "hop-verify.sh"
KMS_TF = REPO / "terraform" / "roots" / "asela-cluster" / "vault-kms.tf"


def test_scripts_exist_and_parse():
    for s in (SYNC_WINDOW, HOP_VERIFY):
        assert s.exists(), f"{s.name} missing"
        r = subprocess.run(["bash", "-n", str(s)], capture_output=True, text=True)
        assert r.returncode == 0, f"{s.name} has a syntax error: {r.stderr}"


# --- §T.25 / §B.4 -------------------------------------------------------------------

def test_sync_window_suspends_the_parent_app():
    """§B.4: suspending a child Argo app is not enough — master-app re-applies it from git
    mid-operation. That silently undid the suspension during T36 and nearly started Vault
    on a half-restored volume."""
    t = SYNC_WINDOW.read_text()
    assert "master-app" in t, "must suspend master-app, not just the children"


def test_sync_window_discovers_volumeclaimtemplate_apps():
    """Querying only PVCs carrying an Argo tracking-id misses StatefulSets using
    volumeClaimTemplates, whose claims the StatefulSet controller creates. That omission
    would have skipped vault and openshell — the two that matter most."""
    t = SYNC_WINDOW.read_text()
    assert "volumeClaimTemplates" in t, (
        "scope must union tracked PVCs with volumeClaimTemplate StatefulSets"
    )


def test_sync_window_records_prior_policy():
    """Resume must restore what was there, not assume prune/selfHeal true — an app that was
    already suspended before the window must stay suspended after it."""
    t = SYNC_WINDOW.read_text()
    assert "--state" in t and "json.dump" in t, "must persist prior syncPolicy for exact restore"


# --- §T.29 / §T.30 ------------------------------------------------------------------

def test_hop_verify_checks_all_three_artifacts():
    """§V.1, §V.6 and §V.28 each require a verified artifact. A gate that checks only one
    would pass while a whole recovery path was missing."""
    t = HOP_VERIFY.read_text()
    for glob in ("k3s-backup-*.tar.gz", "pg-*.sql.gz", "vault-backup-*.tar.gz"):
        assert glob in t, f"gate does not check for {glob}"


def test_hop_verify_validates_checksums_not_just_presence():
    t = HOP_VERIFY.read_text()
    assert "sha_check" in t, "a backup that exists but does not verify is not a backup (§V.16)"


def test_hop_verify_separates_diagnosed_from_tolerated_drift():
    """§V.47 exists because allow-listing undiagnosed drift is what made §V.5 meaningless.
    The two tiers must stay distinct so tolerated items keep being visible."""
    t = HOP_VERIFY.read_text()
    assert "KNOWN_DRIFT_DIAGNOSED" in t and "KNOWN_DRIFT_TOLERATED" in t, (
        "drift allow-list must distinguish diagnosed causes from merely tolerated ones"
    )


def test_hop_verify_checks_istio_dataplane():
    """§V.49: Istio is deferred, and istio-cni is a CNI plugin chained into node
    networking — a failure could break pod creation rather than merely degrade."""
    t = HOP_VERIFY.read_text()
    assert "istio-cni-node" in t and "ztunnel" in t, "gate must check the ambient dataplane"


def test_hop_verify_measures_the_v9_window_from_k3s_stop():
    """§V.9 was undefined until §T.30. Measuring from anything later than the k3s stop —
    or ending at anything earlier than the cluster being usable — flatters the number."""
    t = HOP_VERIFY.read_text()
    assert "--since" in t, "window needs an explicit start"
    assert "900" in t, "must compare against §V.9's 15 minute bound"


def test_hop_verify_detects_orphaned_cni_plugin():
    """§V.50 / §B.7: a k3s hop rotates data/<hash> and repoints data/current, orphaning the
    istio-cni binary installed under it. Every new pod sandbox on that node then fails —
    coredns included, so cluster DNS goes down and takes Argo's repo-server with it.

    The §V.49 DaemonSet-Ready check cannot see this: all three pods stayed Ready 3/3 for the
    entire 27-minute outage. The gate needs the symptom, not the pod count."""
    t = HOP_VERIFY.read_text()
    assert "check_cni_plugin" in t, "gate must check for an orphaned CNI plugin"
    assert "failed to find plugin" in t, "must match containerd's actual sandbox error"
    assert "check_cni_plugin" in t.split("case \"$ACTION\"")[1], (
        "check_cni_plugin must actually be wired into the gate, not merely defined"
    )


def test_hop_verify_cni_check_distinguishes_live_from_stale():
    """k3s retains events for ~an hour, so matching on events alone keeps failing the gate
    long after the node is fixed. A gate that fails on a resolved incident is one operators
    learn to override — which is how a real failure gets waved through."""
    t = HOP_VERIFY.read_text()
    assert "ContainerCreating" in t, (
        "live-vs-history must be decided by pods stuck now, not by event presence"
    )


def test_hop_verify_cni_check_does_not_exec_into_the_pod():
    """The obvious check — exec into istio-cni-node and test for the binary — PASSES while
    the node is broken, because that pod's own hostPath mount still resolves to the old
    data dir. That is the whole reason the DaemonSet never self-heals."""
    t = HOP_VERIFY.read_text()
    cni = t.split("check_cni_plugin()")[1].split("\n}")[0]
    assert "kubectl exec" not in cni, (
        "an exec-based binary check gives a false PASS — the pod's mount points at the old dir"
    )


# --- §T.28 / §V.35 ------------------------------------------------------------------

def test_kms_key_has_prevent_destroy():
    """§V.35: Vault's storage is ciphertext sealed with this key, so the key IS the backup.
    Losing it loses every secret in the cluster no matter how good the artifacts are."""
    t = KMS_TF.read_text()
    block = re.search(r'resource\s+"aws_kms_key"\s+"vault_auto_unseal"\s*\{.*?\n\}', t, re.S)
    assert block, "vault_auto_unseal KMS key resource not found"
    assert "prevent_destroy = true" in block.group(0), (
        "the Vault unseal key must carry lifecycle.prevent_destroy — a terraform destroy "
        "or forced replacement would make every Vault backup undecryptable (§V.35)"
    )
