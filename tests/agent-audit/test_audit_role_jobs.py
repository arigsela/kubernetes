"""The SELECT-only audit roles' init Jobs: default privileges and lockstep.

Both Jobs create a read-only role on kagent's databases. kagent's controller (the
database owner, role `kagent`) creates and migrates the 0.10 tables, so the Jobs'
ALTER DEFAULT PRIVILEGES must name that role with FOR ROLE. Without it, default
privileges attach to the admin running the Job - who creates no tables - and any
table kagent creates later is unreadable until the next postgresql sync re-runs
the hook (reproduced against Postgres 18 on 2026-09-27; see the fix commit).

kagent 1.0 (database kagent_v1) migrates as role kagent_owner into schema
`kagent`, so its block names kagent_owner the same way.
"""
import importlib.util
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
JOBS = ["init-kagent-audit-role.yaml", "init-agent-audit-web-role.yaml"]
SECRETS = {"init-kagent-audit-role.yaml": "external-secrets-kagent-audit.yaml",
           "init-agent-audit-web-role.yaml": "external-secrets-agent-audit-web-db.yaml"}

_spec = importlib.util.spec_from_file_location("agent_audit", ROOT / "scripts" / "agent-audit.py")
aa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aa)


def _container(job: str) -> dict:
    doc = yaml.safe_load((ROOT / "base-apps/postgresql" / job).read_text())
    return doc["spec"]["template"]["spec"]["containers"][0]


def _sql(job: str) -> list[str]:
    script = _container(job)["command"][2]
    return re.findall(r"<<'EOSQL'\n(.*?)\n\s*EOSQL", script, re.S)


def _grant_blocks(job: str) -> list[str]:
    """[0.10 archive grants, kagent 1.0 grants]"""
    return _sql(job)[1:]


def _env(job: str) -> dict:
    return {e["name"]: e for e in _container(job)["env"]}


def test_default_privileges_name_the_table_owner():
    for job in JOBS:
        for grants in _grant_blocks(job):
            assert "ALTER DEFAULT PRIVILEGES FOR ROLE" in grants, job
            assert not re.search(r"ALTER DEFAULT PRIVILEGES\s+IN SCHEMA", grants), job


def test_revoke_comes_before_grant():
    for job in JOBS:
        for grants in _grant_blocks(job):
            assert grants.index("REVOKE ALL") < grants.index("GRANT SELECT ON ALL TABLES"), job


def test_both_jobs_run_identical_sql():
    """init-agent-audit-web-role.yaml is derived from init-kagent-audit-role.yaml;
    its header says the SQL must stay identical. This is what enforces it."""
    assert _sql(JOBS[0]) == _sql(JOBS[1])


def test_archive_and_v1_are_both_granted():
    for job in JOBS:
        archive, v1 = _grant_blocks(job)
        assert "SCHEMA public" in archive
        assert 'SCHEMA :"kagent_schema"' in v1 and 'FOR ROLE :"owner_role"' in v1


def test_v1_owner_and_schema_are_kagents():
    """kagent chart values database.postgres.role / .schema (v1.0.0-alpha11 defaults),
    and the schema agent-audit.py queries."""
    for job in JOBS:
        env = _env(job)
        assert env["KAGENT_V1_OWNER_ROLE"]["value"] == "kagent_owner"
        assert env["KAGENT_V1_SCHEMA"]["value"] == aa.V1_SCHEMA


def test_v1_database_name_comes_from_the_externalsecret():
    """optional, so a missing Vault property skips the 1.0 grants instead of failing
    every postgresql sync; and it must be a key the ExternalSecret really writes."""
    for job in JOBS:
        ref = _env(job)["AUDIT_DB_V1"]["valueFrom"]["secretKeyRef"]
        assert ref["optional"] is True
        es = yaml.safe_load((ROOT / "base-apps/postgresql" / SECRETS[job]).read_text())
        assert ref["name"] == es["spec"]["target"]["name"]
        data = {d["secretKey"]: d["remoteRef"] for d in es["spec"]["data"]}
        assert data[ref["key"]]["property"] == "db-name-v1"


def test_v1_grants_are_skipped_not_failed_without_a_database():
    for job in JOBS:
        script = _container(job)["command"][2]
        assert 'if [ -z "${AUDIT_DB_V1:-}" ]' in script
        assert "does not exist yet: skipping the kagent 1.0 grants" in script
