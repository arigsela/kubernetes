"""Tests for the Vault backup + restore path (SPEC.md §T.35, §V.28, §V.21).

Vault has no backup today. `vault-0` runs `file` storage at /vault/data, replicas=1, on a
1Gi local-path volume pinned to k3s-control-01 — the node every control-plane hop restarts
— and it holds the credentials all 45 namespaces resolve through ESO. This is the only
protection that data has.

The design constraint: Vault's file backend has **no consistent-snapshot API**. There is no
`vault operator raft snapshot` equivalent; the documented method is to stop Vault and copy
the directory. Copying it live risks catching a partial write, which is exactly §B.1 — an
artifact that checksums cleanly and cannot restore. So cold is the default, and online must
brand its own output as untrustworthy.
"""
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from conftest import requires_docker, sha256_check

REPO = Path(__file__).resolve().parents[2]
BACKUP = REPO / "scripts" / "vault-backup.sh"
RESTORE = REPO / "scripts" / "vault-restore.sh"

VAULT_IMAGE = "hashicorp/vault:1.18.1"
MARKER_PATH = "secret/drill"
MARKER_VALUE = "vault-restore-ok"


def run(script: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    base.update(env or {})
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True, text=True, env=base, timeout=300,
    )


def _fake_vault_data(tmp_path: Path) -> Path:
    """Stand-in for /vault/data — the file backend's on-disk layout."""
    data = tmp_path / "vault-data"
    for sub in ("core", "logical", "sys", "auth"):
        (data / sub).mkdir(parents=True)
    (data / "core" / "_seal-config").write_text('{"type":"awskms"}')
    (data / "logical" / "marker").write_text("ciphertext-stand-in")
    return data


# --- §V.21 — artifacts must not live inside the cluster ------------------------------

def test_vault_backup_rejects_in_cluster_destination(tmp_path):
    data = _fake_vault_data(tmp_path)
    result = run(
        BACKUP, "--data-dir", str(data),
        "--dest", "/var/lib/rancher/k3s/storage/vault-backups", "--dry-run",
    )
    assert result.returncode != 0, "in-cluster destination accepted"
    assert "V21" in result.stderr


def test_vault_backup_requires_dest(tmp_path):
    data = _fake_vault_data(tmp_path)
    result = run(BACKUP, "--data-dir", str(data), "--dry-run")
    assert result.returncode != 0
    assert "dest" in result.stderr.lower()


# --- §B.1 lesson — an inconsistent copy must never look trustworthy ------------------

def test_vault_backup_online_refused_without_explicit_flag(tmp_path):
    """Vault's file backend cannot be snapshotted consistently while running. Online mode
    must be opt-in, not the path of least resistance."""
    data = _fake_vault_data(tmp_path)
    result = run(BACKUP, "--data-dir", str(data), "--dest", str(tmp_path / "out"),
                 "--mode", "online")
    assert result.returncode != 0, "online mode ran without acknowledgement"
    assert "--allow-inconsistent" in result.stderr


def test_vault_backup_online_brands_artifact(tmp_path):
    """If an operator insists, the artifact must carry the warning in its own filename —
    a torn backup that looks like a good one is worse than no backup (§B.1)."""
    data = _fake_vault_data(tmp_path)
    dest = tmp_path / "out"
    result = run(BACKUP, "--data-dir", str(data), "--dest", str(dest),
                 "--mode", "online", "--allow-inconsistent")
    assert result.returncode == 0, result.stderr
    artifacts = list(dest.glob("*.tar.gz"))
    assert artifacts, "no artifact produced"
    assert "INCONSISTENT" in artifacts[0].name, (
        f"online artifact not branded: {artifacts[0].name}"
    )


def test_vault_backup_cold_artifact_is_not_branded(tmp_path):
    data = _fake_vault_data(tmp_path)
    dest = tmp_path / "out"
    assert run(BACKUP, "--data-dir", str(data), "--dest", str(dest)).returncode == 0
    artifact = next(dest.glob("*.tar.gz"))
    assert "INCONSISTENT" not in artifact.name


def test_vault_backup_writes_verifiable_checksum(tmp_path):
    data = _fake_vault_data(tmp_path)
    dest = tmp_path / "out"
    assert run(BACKUP, "--data-dir", str(data), "--dest", str(dest)).returncode == 0
    artifact = next(dest.glob("*.tar.gz"))
    checksum = Path(str(artifact) + ".sha256")
    assert checksum.exists(), "no integrity manifest"
    verify = subprocess.run(sha256_check(checksum.name),
                            cwd=dest, capture_output=True, text=True)
    assert verify.returncode == 0, verify.stdout + verify.stderr


def test_vault_restore_rejects_corrupt_artifact(tmp_path):
    data = _fake_vault_data(tmp_path)
    dest = tmp_path / "out"
    assert run(BACKUP, "--data-dir", str(data), "--dest", str(dest)).returncode == 0
    artifact = next(dest.glob("*.tar.gz"))
    artifact.write_bytes(artifact.read_bytes() + b"corruption")
    result = run(RESTORE, "--artifact", str(artifact),
                 "--data-dir", str(tmp_path / "restored"), "--no-verify")
    assert result.returncode != 0, "corrupt artifact accepted"
    assert "checksum" in result.stderr.lower()


def test_vault_restore_refuses_inconsistent_artifact_without_ack(tmp_path):
    """An artifact branded INCONSISTENT must not restore silently — the operator has to
    say out loud that they are recovering from a possibly-torn copy."""
    data = _fake_vault_data(tmp_path)
    dest = tmp_path / "out"
    assert run(BACKUP, "--data-dir", str(data), "--dest", str(dest),
               "--mode", "online", "--allow-inconsistent").returncode == 0
    artifact = next(dest.glob("*.tar.gz"))
    result = run(RESTORE, "--artifact", str(artifact),
                 "--data-dir", str(tmp_path / "restored"), "--no-verify")
    assert result.returncode != 0
    assert "INCONSISTENT" in result.stderr


# --- §V.28 — the drill ---------------------------------------------------------------

def _docker(*args, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def _vault(container: str, *args: str, token: str | None = None):
    env = ["-e", "VAULT_ADDR=http://127.0.0.1:8200"]
    if token:
        env += ["-e", f"VAULT_TOKEN={token}"]
    return _docker("exec", *env, container, "vault", *args)


def _wait_listening(container: str, attempts: int = 40) -> bool:
    for _ in range(attempts):
        out = _vault(container, "status")
        # `vault status` exits 2 when sealed, 0 when unsealed — both mean it is listening.
        if out.returncode in (0, 2):
            return True
        time.sleep(2)
    return False


VAULT_CONFIG = json.dumps({
    "storage": {"file": {"path": "/vault/data"}},
    "listener": [{"tcp": {"address": "0.0.0.0:8200", "tls_disable": True}}],
    "disable_mlock": True,
    "ui": False,
})


@pytest.mark.slow
@requires_docker
def test_vault_restore_drill_docker(tmp_path):
    """§V.28: an untested Vault backup is not a backup.

    Seeds a real secret into a real Vault on `file` storage, stops it, backs the data up,
    destroys the container AND its volume, restores, and requires both that Vault unseals
    and that the secret reads back.

    Coverage boundary: this drill uses Shamir. Production `vault-0` seals with awskms.
    File-storage restore is byte-identical either way; what this does NOT exercise is the
    seal path — and note that awskms data is undecryptable without the KMS key, which no
    filesystem backup can capture.
    """
    vol, vol2 = "vault-drill-vol", "vault-drill-vol2"
    live, restored, helper = "vault-drill", "vault-drill-restored", "vault-drill-helper"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    def cleanup():
        for c in (live, restored, helper):
            _docker("rm", "-f", c)
        for v in (vol, vol2):
            _docker("volume", "rm", v)

    def prepare_volume(volume: str):
        """A fresh docker volume is root-owned; the Vault image runs as uid 100."""
        assert _docker("volume", "create", volume).returncode == 0
        assert _docker("run", "--rm", "-v", f"{volume}:/vault/data", "alpine:3.20",
                       "chown", "-R", "100:1000", "/vault/data").returncode == 0

    def start_vault(name: str, volume: str):
        return _docker(
            "run", "-d", "--name", name, "--cap-add=IPC_LOCK",
            "-e", f"VAULT_LOCAL_CONFIG={VAULT_CONFIG}",
            "-v", f"{volume}:/vault/data",
            VAULT_IMAGE, "server",
        )

    cleanup()
    try:
        prepare_volume(vol)
        assert start_vault(live, vol).returncode == 0
        assert _wait_listening(live), "drill Vault never started listening"

        init = _vault(live, "operator", "init", "-key-shares=1", "-key-threshold=1",
                      "-format=json")
        assert init.returncode == 0, init.stderr
        init_data = json.loads(init.stdout)
        unseal_key = init_data["unseal_keys_b64"][0]
        root_token = init_data["root_token"]

        assert _vault(live, "operator", "unseal", unseal_key).returncode == 0
        assert _vault(live, "secrets", "enable", "-path=secret", "kv-v2",
                      token=root_token).returncode == 0
        put = _vault(live, "kv", "put", MARKER_PATH, f"proof={MARKER_VALUE}",
                     token=root_token)
        assert put.returncode == 0, put.stderr

        # Quiesce: file backend has no consistent online snapshot, so stop first.
        assert _docker("stop", live).returncode == 0

        extracted = tmp_path / "extracted"
        extracted.mkdir()
        assert _docker("create", "--name", helper, "-v", f"{vol}:/vault/data",
                       "alpine:3.20", "true").returncode == 0
        assert _docker("cp", f"{helper}:/vault/data/.", str(extracted)).returncode == 0

        backup = run(BACKUP, "--data-dir", str(extracted), "--dest", str(artifacts))
        assert backup.returncode == 0, backup.stdout + backup.stderr
        artifact = next(artifacts.glob("*.tar.gz"))
        assert "INCONSISTENT" not in artifact.name

        # Destroy everything. The artifact is now the only copy.
        assert _docker("rm", "-f", live).returncode == 0
        assert _docker("rm", "-f", helper).returncode == 0
        assert _docker("volume", "rm", vol).returncode == 0

        target = tmp_path / "restored-data"
        rest = run(RESTORE, "--artifact", str(artifact), "--data-dir", str(target),
                   "--no-verify")
        assert rest.returncode == 0, rest.stdout + rest.stderr

        prepare_volume(vol2)
        assert _docker("create", "--name", restored, "--cap-add=IPC_LOCK",
                       "-e", f"VAULT_LOCAL_CONFIG={VAULT_CONFIG}",
                       "-v", f"{vol2}:/vault/data",
                       VAULT_IMAGE, "server").returncode == 0
        assert _docker("cp", f"{target}/.", f"{restored}:/vault/data/").returncode == 0
        # docker cp lands files as root; the vault image runs as uid 100.
        assert _docker("run", "--rm", "-v", f"{vol2}:/vault/data", "alpine:3.20",
                       "chown", "-R", "100:1000", "/vault/data").returncode == 0
        assert _docker("start", restored).returncode == 0
        assert _wait_listening(restored), "restored Vault never started listening"

        assert _vault(restored, "operator", "unseal", unseal_key).returncode == 0, (
            "§V.28: restored Vault would not unseal"
        )
        read = _vault(restored, "kv", "get", "-field=proof", MARKER_PATH,
                      token=root_token)
        assert read.returncode == 0, read.stderr
        assert read.stdout.strip() == MARKER_VALUE, "seeded secret did not survive"
    finally:
        cleanup()
