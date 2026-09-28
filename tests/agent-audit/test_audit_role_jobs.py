"""The SELECT-only audit roles' init Jobs: default privileges and lockstep.

Both Jobs create a read-only role on kagent's database. kagent's controller (the
database owner, role `kagent`) creates and migrates the tables, so the Jobs'
ALTER DEFAULT PRIVILEGES must name that role with FOR ROLE. Without it, default
privileges attach to the admin running the Job - who creates no tables - and any
table kagent creates later is unreadable until the next postgresql sync re-runs
the hook (reproduced against Postgres 18 on 2026-09-27; see the fix commit).
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
JOBS = ["init-kagent-audit-role.yaml", "init-agent-audit-web-role.yaml"]


def _sql(job: str) -> list[str]:
    doc = yaml.safe_load((ROOT / "base-apps/postgresql" / job).read_text())
    script = doc["spec"]["template"]["spec"]["containers"][0]["command"][2]
    return re.findall(r"<<'EOSQL'\n(.*?)\n\s*EOSQL", script, re.S)


def test_default_privileges_name_the_table_owner():
    for job in JOBS:
        grants = _sql(job)[1]
        assert "ALTER DEFAULT PRIVILEGES FOR ROLE" in grants, job
        assert not re.search(r"ALTER DEFAULT PRIVILEGES\s+IN SCHEMA", grants), job


def test_revoke_comes_before_grant():
    for job in JOBS:
        grants = _sql(job)[1]
        assert grants.index("REVOKE ALL") < grants.index("GRANT SELECT ON ALL TABLES"), job


def test_both_jobs_run_identical_sql():
    """init-agent-audit-web-role.yaml is derived from init-kagent-audit-role.yaml;
    its header says the SQL must stay identical. This is what enforces it."""
    assert _sql(JOBS[0]) == _sql(JOBS[1])
