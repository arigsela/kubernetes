"""Tests for the operator-independent Postgres dump (SPEC.md §T.3, §V.6, §V.21).

The cluster already ships barman backups to S3 and they are healthy. This script is not
a replacement for those — it exists because §T.9 upgrades the CNPG operator itself, and a
barman restore needs a working operator to drive it. A plain `pg_dump` is the only
insurance that survives an operator upgrade going wrong.

`postgresql-cluster` runs a single instance on a node-pinned local-path volume with no
replica, so there is no second copy to fall back on.
"""
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from conftest import requires_docker, sha256_check

REPO = Path(__file__).resolve().parents[2]
PG_BACKUP = REPO / "scripts" / "pg-backup.sh"

PG_IMAGE = "postgres:16-alpine"
PG_PASSWORD = "drillpass"


def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    base.update(env or {})
    return subprocess.run(
        ["bash", str(PG_BACKUP), *args],
        capture_output=True, text=True, env=base, timeout=300,
    )


def _docker(*args, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def test_pg_backup_rejects_in_cluster_destination(tmp_path):
    """§V.21: local-path is the only StorageClass, so an artifact written to cluster
    storage is pinned to the same node as the database it protects."""
    result = run(
        "--dest", "/var/lib/rancher/k3s/storage/pgdump", "--dry-run",
        "--exec-cmd", "/usr/bin/true", "--database", "app",
    )
    assert result.returncode != 0
    assert "V21" in result.stderr


def test_pg_backup_requires_destination():
    result = run("--dry-run", "--exec-cmd", "/usr/bin/true", "--database", "app")
    assert result.returncode != 0
    assert "dest" in result.stderr.lower()


def test_pg_backup_requires_database_or_all(tmp_path):
    """postgresql-cluster hosts several databases (n8n, chores_tracker). A default of
    one name would silently under-protect the others, so the choice must be explicit."""
    result = run("--dest", str(tmp_path / "out"), "--dry-run", "--exec-cmd", "/usr/bin/true")
    assert result.returncode != 0
    assert "--all" in result.stderr


@pytest.mark.slow
@requires_docker
def test_pg_dumpall_captures_every_database(tmp_path, pg_container):
    """--all must capture every database, not just one — this is the pre-§T.9 insurance
    artifact, taken before the CNPG operator itself is upgraded."""
    def psql(sql: str, db: str = "postgres"):
        return _docker(
            "exec", "-e", f"PGPASSWORD={PG_PASSWORD}", pg_container,
            "psql", "-U", "postgres", "-d", db, "-tAc", sql,
        )

    for name in ("n8n_like", "chores_like"):
        assert psql(f"CREATE DATABASE {name};").returncode == 0
        assert psql(f"CREATE TABLE t_{name} (v text);", db=name).returncode == 0

    dest = tmp_path / "all"
    result = run(
        "--dest", str(dest), "--all",
        "--exec-cmd", f"docker exec -e PGPASSWORD={PG_PASSWORD} {pg_container}",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    artifact = next(dest.glob("pg-*-all-*.sql.gz"))
    dump = subprocess.run(
        ["gunzip", "-c", str(artifact)], capture_output=True, text=True, check=True
    ).stdout
    assert "n8n_like" in dump and "chores_like" in dump, "not every database was captured"
    assert "CREATE ROLE" in dump or "ROLE postgres" in dump, "globals/roles missing from dumpall"


@pytest.fixture
def pg_container():
    """A disposable Postgres standing in for the CNPG primary."""
    name = f"pg-drill-{uuid.uuid4().hex[:8]}"
    _docker(
        "run", "-d", "--name", name,
        "-e", f"POSTGRES_PASSWORD={PG_PASSWORD}",
        PG_IMAGE,
    )
    try:
        for _ in range(60):
            ready = _docker("exec", name, "pg_isready", "-U", "postgres")
            if ready.returncode == 0:
                break
            time.sleep(2)
        else:
            pytest.fail(f"postgres container {name} never became ready")
        yield name
    finally:
        _docker("rm", "-f", name)


@pytest.mark.slow
@requires_docker
def test_pg_dump_artifact_restores(tmp_path, pg_container):
    """§V.6: a dump that has not been restored is not a backup.

    Seed a row, dump through the script, drop the database entirely, restore from the
    artifact, and require the row back.
    """
    def psql(sql: str, db: str = "postgres") -> subprocess.CompletedProcess:
        return _docker(
            "exec", "-e", f"PGPASSWORD={PG_PASSWORD}", pg_container,
            "psql", "-U", "postgres", "-d", db, "-tAc", sql,
        )

    assert psql("CREATE DATABASE app;").returncode == 0
    assert psql(
        "CREATE TABLE chores (id serial primary key, name text); "
        "INSERT INTO chores (name) VALUES ('take-out-bins');",
        db="app",
    ).returncode == 0

    dest = tmp_path / "pgdumps"
    result = run(
        "--dest", str(dest), "--database", "app",
        "--exec-cmd", f"docker exec -e PGPASSWORD={PG_PASSWORD} {pg_container}",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    artifact = next(dest.glob("pg-*-app-*.sql.gz"))
    checksum = Path(str(artifact) + ".sha256")
    assert checksum.exists(), "§V.1: artifact has no integrity manifest"
    verify = subprocess.run(
        sha256_check(checksum.name),
        cwd=dest, capture_output=True, text=True,
    )
    assert verify.returncode == 0, verify.stdout

    # Destroy the database outright — the artifact is now the only copy.
    assert psql("DROP DATABASE app;").returncode == 0
    assert psql("CREATE DATABASE app;").returncode == 0
    assert "take-out-bins" not in psql("SELECT name FROM chores;", db="app").stdout

    restored_sql = subprocess.run(
        ["gunzip", "-c", str(artifact)], capture_output=True, text=True, check=True
    ).stdout
    load = subprocess.run(
        ["docker", "exec", "-i", "-e", f"PGPASSWORD={PG_PASSWORD}", pg_container,
         "psql", "-U", "postgres", "-d", "app"],
        input=restored_sql, capture_output=True, text=True,
    )
    assert load.returncode == 0, load.stderr

    rows = psql("SELECT name FROM chores;", db="app").stdout
    assert "take-out-bins" in rows, "dump did not restore the seeded row"
