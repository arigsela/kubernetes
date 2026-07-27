"""Tests for the k3s upgrade safety net (SPEC.md §T.1-§T.3).

These scripts are the only rollback that exists. The cluster's datastore is SQLite
(no `--cluster-init`), k3s migrates it one-way on every minor bump, and downgrade is
unsupported — so a restored filesystem artifact is the entire recovery story.

That makes the *guard clauses* the thing worth testing, not the happy path. §V.15 exists
because tarring a live `state.db` yields a torn copy that still checksums cleanly: §V.1
would then report a verified backup that cannot restore. A backup you falsely trust is
worse than none at all, so the script must refuse rather than warn.
"""
import os
import subprocess
from pathlib import Path

import pytest

from conftest import requires_docker

REPO = Path(__file__).resolve().parents[2]
BACKUP = REPO / "scripts" / "k3s-backup.sh"
RESTORE = REPO / "scripts" / "k3s-restore.sh"
PG_BACKUP = REPO / "scripts" / "pg-backup.sh"


def run(script: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Invoke a script with a controlled environment, never inheriting real cluster state."""
    base = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    base.update(env or {})
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=base,
        timeout=120,
    )


def _fake_k3s_tree(tmp_path: Path) -> Path:
    """Minimal stand-in for /var/lib/rancher/k3s with a non-empty SQLite datastore."""
    data = tmp_path / "k3s"
    (data / "server" / "db").mkdir(parents=True)
    (data / "server" / "tls").mkdir(parents=True)
    (data / "storage").mkdir(parents=True)  # local-path PV root
    # k3s/kine runs SQLite in WAL mode, so a real datastore is a *file set*:
    # state.db plus state.db-wal and state.db-shm. Reproduce that here.
    subprocess.run(
        ["sqlite3", str(data / "server" / "db" / "state.db"),
         "PRAGMA journal_mode=WAL; "
         "CREATE TABLE kine (id INTEGER PRIMARY KEY, name TEXT); "
         "INSERT INTO kine (name) VALUES ('marker-row');"],
        check=True,
    )
    # Leave a WAL behind, as a running server would.
    (data / "server" / "db" / "state.db-wal").touch()
    (data / "server" / "db" / "state.db-shm").touch()
    (data / "server" / "token").write_text("fake-token\n")
    (data / "server" / "tls" / "server-ca.crt").write_text("fake-cert\n")
    return data


# --- §V.21 — artifacts must not live inside the cluster ------------------------------

def test_backup_rejects_in_cluster_destination(tmp_path):
    """§V.21: a destination inside the k3s data dir is on a local-path volume — it dies
    with the node it exists to protect. The script must refuse, not warn."""
    data = _fake_k3s_tree(tmp_path)
    result = run(
        BACKUP, "--mode", "online", "--dest", str(data / "storage" / "backups"), "--dry-run",
        env={"K3S_DATA_DIR": str(data)},
    )
    assert result.returncode != 0, "in-cluster destination was accepted"
    assert "V21" in result.stderr


def test_backup_accepts_off_cluster_destination(tmp_path):
    data = _fake_k3s_tree(tmp_path)
    result = run(
        BACKUP, "--mode", "online", "--dest", str(tmp_path / "artifacts"), "--dry-run",
        env={"K3S_DATA_DIR": str(data)},
    )
    assert result.returncode == 0, result.stderr


# --- §V.15 — never tar a live state.db -----------------------------------------------

def test_backup_refuses_live_state_db(tmp_path):
    """§V.15: cold mode tars the whole tree including state.db, so it must prove k3s is
    stopped first. K3S_STATUS_CMD exiting 0 means 'still running' -> must abort."""
    data = _fake_k3s_tree(tmp_path)
    result = run(
        BACKUP, "--mode", "cold", "--dest", str(tmp_path / "out"), "--skip-stop",
        env={"K3S_DATA_DIR": str(data), "K3S_STATUS_CMD": "/usr/bin/true"},
    )
    assert result.returncode != 0, "cold backup proceeded while k3s was still running"
    assert "V15" in result.stderr
    assert "state.db" in result.stderr


def test_backup_cold_proceeds_when_k3s_stopped(tmp_path):
    """Same path, but k3s confirmed stopped -> the tar may include state.db."""
    data = _fake_k3s_tree(tmp_path)
    dest = tmp_path / "out"
    result = run(
        BACKUP, "--mode", "cold", "--dest", str(dest), "--skip-stop",
        env={"K3S_DATA_DIR": str(data), "K3S_STATUS_CMD": "/usr/bin/false"},
    )
    assert result.returncode == 0, result.stderr
    artifacts = list(dest.glob("k3s-backup-*.tar.gz"))
    assert artifacts, "no artifact produced"
    listing = subprocess.run(
        ["tar", "-tzf", str(artifacts[0])], capture_output=True, text=True, check=True
    ).stdout
    assert "server/db/state.db" in listing


def test_backup_online_excludes_live_state_db(tmp_path):
    """§V.15 online path: the live state.db must be excluded from the tar and replaced by
    a `sqlite3 .backup` snapshot, which is consistent while k3s keeps running."""
    data = _fake_k3s_tree(tmp_path)
    dest = tmp_path / "out"
    result = run(
        BACKUP, "--mode", "online", "--dest", str(dest),
        env={"K3S_DATA_DIR": str(data), "K3S_STATUS_CMD": "/usr/bin/true"},
    )
    assert result.returncode == 0, result.stderr
    artifacts = list(dest.glob("k3s-backup-*.tar.gz"))
    assert artifacts, "no artifact produced"
    listing = subprocess.run(
        ["tar", "-tzf", str(artifacts[0])], capture_output=True, text=True, check=True
    ).stdout
    assert "server/db/state.db\n" not in listing, "live state.db was tarred despite §V.15"
    assert "state.db.backup" in listing, "no consistent sqlite snapshot in artifact"


def test_backup_snapshot_is_readable_sqlite(tmp_path):
    """A snapshot that is not a queryable database is not a backup."""
    data = _fake_k3s_tree(tmp_path)
    dest = tmp_path / "out"
    assert run(
        BACKUP, "--mode", "online", "--dest", str(dest),
        env={"K3S_DATA_DIR": str(data), "K3S_STATUS_CMD": "/usr/bin/true"},
    ).returncode == 0
    artifact = next(dest.glob("k3s-backup-*.tar.gz"))
    subprocess.run(["tar", "-xzf", str(artifact), "-C", str(tmp_path)], check=True)
    snapshot = next(tmp_path.rglob("state.db.backup"))
    rows = subprocess.run(
        ["sqlite3", str(snapshot), "SELECT name FROM kine;"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "marker-row" in rows


def test_backup_online_excludes_stale_wal(tmp_path):
    """§V.15 applies to the whole datastore file set, not just state.db.

    An online artifact carries a standalone `sqlite3 .backup` snapshot. Shipping the live
    -wal/-shm beside it means that on restore SQLite finds a write-ahead log belonging to
    a *different* database and replays it over the snapshot. That is precisely the
    silent-corruption failure §V.15 exists to prevent, so the sidecars must be excluded.
    """
    data = _fake_k3s_tree(tmp_path)
    dest = tmp_path / "out"
    assert run(
        BACKUP, "--mode", "online", "--dest", str(dest),
        env={"K3S_DATA_DIR": str(data), "K3S_STATUS_CMD": "/usr/bin/true"},
    ).returncode == 0
    artifact = next(dest.glob("k3s-backup-*.tar.gz"))
    listing = subprocess.run(
        ["tar", "-tzf", str(artifact)], capture_output=True, text=True, check=True
    ).stdout
    assert "state.db-wal" not in listing, "live WAL shipped alongside the snapshot"
    assert "state.db-shm" not in listing, "live shared-memory file shipped with the snapshot"


def test_restore_clears_stale_wal_before_promoting(tmp_path):
    """Defence in depth: even given a bad artifact, restore must not leave a foreign WAL
    next to the promoted snapshot."""
    data = _fake_k3s_tree(tmp_path)
    dest = tmp_path / "out"
    assert run(
        BACKUP, "--mode", "online", "--dest", str(dest),
        env={"K3S_DATA_DIR": str(data), "K3S_STATUS_CMD": "/usr/bin/true"},
    ).returncode == 0
    artifact = next(dest.glob("k3s-backup-*.tar.gz"))
    target = tmp_path / "restored"
    # Pre-seed the target with junk sidecars, as a failed prior restore would leave.
    (target / "server" / "db").mkdir(parents=True)
    (target / "server" / "db" / "state.db-wal").write_text("stale")
    result = run(
        RESTORE, "--artifact", str(artifact), "--data-dir", str(target),
        "--expect-version", "v1.33.5+k3s1", "--skip-service", "--no-verify",
    )
    assert result.returncode == 0, result.stderr
    db_dir = target / "server" / "db"
    assert (db_dir / "state.db").exists()
    assert not (db_dir / "state.db-wal").exists(), "stale WAL survived the restore"


# --- §V.1 — the artifact must carry an integrity manifest ----------------------------

def test_backup_writes_verifiable_checksum(tmp_path):
    data = _fake_k3s_tree(tmp_path)
    dest = tmp_path / "out"
    assert run(
        BACKUP, "--mode", "online", "--dest", str(dest),
        env={"K3S_DATA_DIR": str(data), "K3S_STATUS_CMD": "/usr/bin/true"},
    ).returncode == 0
    artifact = next(dest.glob("k3s-backup-*.tar.gz"))
    checksum = Path(str(artifact) + ".sha256")
    assert checksum.exists(), "no checksum written"
    verify = subprocess.run(
        ["shasum", "-a", "256", "-c", checksum.name],
        cwd=dest, capture_output=True, text=True,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr


# --- §T.2 / §V.16 — a restore that is not proven is not a restore --------------------

def _make_artifact(tmp_path: Path, mode: str = "online") -> Path:
    data = _fake_k3s_tree(tmp_path)
    dest = tmp_path / "artifacts"
    status = "/usr/bin/true" if mode == "online" else "/usr/bin/false"
    extra = [] if mode == "online" else ["--skip-stop"]
    result = run(
        BACKUP, "--mode", mode, "--dest", str(dest), *extra,
        env={"K3S_DATA_DIR": str(data), "K3S_STATUS_CMD": status},
    )
    assert result.returncode == 0, result.stderr
    return next(dest.glob("k3s-backup-*.tar.gz"))


def test_restore_rejects_corrupt_artifact(tmp_path):
    """A silently-corrupt artifact is the failure mode §V.1 exists to prevent, so the
    checksum is a gate and not a log line."""
    artifact = _make_artifact(tmp_path)
    artifact.write_bytes(artifact.read_bytes() + b"corruption")
    result = run(
        RESTORE, "--artifact", str(artifact), "--data-dir", str(tmp_path / "restored"),
        "--expect-version", "v1.33.5+k3s1", "--skip-service", "--no-verify",
    )
    assert result.returncode != 0, "corrupt artifact was accepted"
    assert "checksum" in result.stderr.lower()


def test_restore_promotes_online_snapshot(tmp_path):
    """An online artifact carries state.db.backup and no state.db. Restore must promote
    the snapshot into place, otherwise k3s starts with no datastore."""
    artifact = _make_artifact(tmp_path, mode="online")
    target = tmp_path / "restored"
    result = run(
        RESTORE, "--artifact", str(artifact), "--data-dir", str(target),
        "--expect-version", "v1.33.5+k3s1", "--skip-service", "--no-verify",
    )
    assert result.returncode == 0, result.stderr
    restored_db = target / "server" / "db" / "state.db"
    assert restored_db.exists(), "state.db was not promoted from the snapshot"
    rows = subprocess.run(
        ["sqlite3", str(restored_db), "SELECT name FROM kine;"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "marker-row" in rows


def test_restore_refuses_on_version_mismatch(tmp_path):
    """§V.16: restore only succeeds if the API comes back at the expected version. A
    verify command reporting anything else must fail the run, not warn."""
    artifact = _make_artifact(tmp_path)
    result = run(
        RESTORE, "--artifact", str(artifact), "--data-dir", str(tmp_path / "restored"),
        "--expect-version", "v1.33.5+k3s1", "--skip-service",
        "--verify-cmd", "echo 'Server Version: v1.34.9+k3s1'",
    )
    assert result.returncode != 0, "restore reported success at the wrong version"
    assert "V16" in result.stderr


def test_restore_succeeds_when_version_matches(tmp_path):
    artifact = _make_artifact(tmp_path)
    result = run(
        RESTORE, "--artifact", str(artifact), "--data-dir", str(tmp_path / "restored"),
        "--expect-version", "v1.33.5+k3s1", "--skip-service",
        "--verify-cmd", "echo 'Server Version: v1.33.5+k3s1'",
    )
    assert result.returncode == 0, result.stderr


# --- §V.1 + §V.16 — the end-to-end drill --------------------------------------------

K3S_IMAGE = "rancher/k3s:v1.33.5-k3s1"
EXPECT_VERSION = "v1.33.5+k3s1"


def _docker(*args, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def _wait_ready(container: str, attempts: int = 60) -> bool:
    import time
    for _ in range(attempts):
        out = _docker("exec", container, "kubectl", "get", "nodes")
        if " Ready " in out.stdout:
            return True
        time.sleep(5)
    return False


@pytest.mark.slow
@requires_docker
def test_restore_drill_docker_k3s(tmp_path):
    """§V.16: an untested artifact is not a backup.

    Exercises the whole contract against a real k3s server on the same SQLite datastore
    the cluster uses: seed state, back up *while k3s is running* (online mode), destroy
    the cluster and its volume outright, restore, and require both that the API returns
    at the prior version and that the seeded state survived.

    Deliberately online mode — cold mode is the easy case, and §B.1 was a defect that
    only the online path could produce.
    """
    vol, outvol = "k3sdrill-pytest-vol", "k3sdrill-pytest-out"
    live, restored, extractor = (
        "k3s-drill-pytest", "k3s-drill-pytest-restored", "k3s-drill-pytest-extract",
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    def cleanup():
        for c in (live, restored, extractor):
            _docker("rm", "-f", c)
        for v in (vol, outvol):
            _docker("volume", "rm", v)

    cleanup()
    try:
        assert _docker("volume", "create", vol).returncode == 0
        assert _docker("volume", "create", outvol).returncode == 0
        assert _docker(
            "run", "-d", "--name", live, "--hostname", "k3sdrill", "--privileged",
            "-v", f"{vol}:/var/lib/rancher/k3s", K3S_IMAGE,
            "server", "--disable", "traefik", "--disable", "metrics-server",
            "--disable", "local-storage",
        ).returncode == 0
        assert _wait_ready(live), "drill cluster never became Ready"

        seeded = _docker(
            "exec", live, "kubectl", "create", "configmap", "drill-marker",
            "-n", "default", "--from-literal=proof=online-restore-ok",
        )
        assert seeded.returncode == 0, seeded.stderr

        # Back up the live datastore from a helper container sharing the volume. The
        # k3s image itself carries no shell tooling, and this mirrors reality: k3s keeps
        # serving while the snapshot is taken.
        # Output goes to a named volume rather than a host bind mount: on macOS the VM
        # only shares selected host paths, and pytest's tmp_path is not one of them.
        # docker cp works regardless of what the VM has mounted.
        backup = _docker(
            "run", "--rm",
            "-v", f"{vol}:/data",
            "-v", f"{REPO / 'scripts'}:/scripts:ro",
            "-v", f"{outvol}:/out",
            "alpine:3.20", "sh", "-c",
            "test -f /scripts/k3s-backup.sh || { echo 'scripts not mounted' >&2; exit 90; }; "
            "apk add --no-cache bash sqlite tar >/dev/null 2>&1 && "
            "K3S_DATA_DIR=/data K3S_STATUS_CMD=/bin/true "
            "bash /scripts/k3s-backup.sh --mode online --dest /out",
        )
        assert backup.returncode == 0, backup.stdout + backup.stderr

        assert _docker(
            "create", "--name", extractor, "-v", f"{outvol}:/out", "alpine:3.20", "true"
        ).returncode == 0
        assert _docker("cp", f"{extractor}:/out/.", str(artifacts)).returncode == 0
        found = sorted(artifacts.glob("k3s-backup-*.tar.gz"))
        assert found, f"no artifact recovered from the drill: {list(artifacts.iterdir())}"
        artifact = found[0]

        # §B.1 regression: the artifact must carry a standalone snapshot and no live set.
        listing = subprocess.run(
            ["tar", "-tzf", str(artifact)], capture_output=True, text=True, check=True
        ).stdout
        assert "state.db.backup" in listing
        assert "state.db-wal" not in listing

        # Destroy the cluster completely — no fallback, the artifact is all that is left.
        assert _docker("rm", "-f", live).returncode == 0
        assert _docker("volume", "rm", vol).returncode == 0

        target = tmp_path / "restored"
        restore = run(
            RESTORE, "--artifact", str(artifact), "--data-dir", str(target),
            "--expect-version", EXPECT_VERSION, "--skip-service", "--no-verify",
        )
        assert restore.returncode == 0, restore.stderr

        assert _docker(
            "create", "--name", restored, "--hostname", "k3sdrill", "--privileged",
            K3S_IMAGE, "server", "--disable", "traefik", "--disable", "metrics-server",
            "--disable", "local-storage",
        ).returncode == 0
        assert _docker(
            "cp", f"{target}/.", f"{restored}:/var/lib/rancher/k3s/"
        ).returncode == 0
        assert _docker("start", restored).returncode == 0
        assert _wait_ready(restored), "restored cluster never became Ready"

        version = _docker("exec", restored, "kubectl", "version")
        assert EXPECT_VERSION in version.stdout, f"§V.16: got {version.stdout!r}"

        marker = _docker(
            "exec", restored, "kubectl", "get", "cm", "drill-marker",
            "-n", "default", "-o", "jsonpath={.data.proof}",
        )
        assert marker.stdout.strip() == "online-restore-ok", "seeded state did not survive"
    finally:
        cleanup()
