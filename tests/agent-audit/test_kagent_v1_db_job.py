"""init-kagent-v1-db creates kagent 1.0's two-login model (kagent#3160, §T.127): an owner login
that migrates and a read/write login that serves, each a NOINHERIT member of its NOLOGIN role,
plus pgvector USAGE for both. The kagent ExternalSecrets name the same Vault properties."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
JOB = ROOT / "base-apps/postgresql/init-kagent-v1-db.yaml"
ES_PG = ROOT / "base-apps/postgresql/external-secrets-kagent-v1.yaml"
ES_OWNER = ROOT / "base-apps/kagent/kagent-postgres-owner-external-secret.yaml"
ES_RW = ROOT / "base-apps/kagent/kagent-postgres-readwrite-external-secret.yaml"


def _job():
    return yaml.safe_load(JOB.read_text())


def _script():
    return _job()["spec"]["template"]["spec"]["containers"][0]["command"][2]


def _env():
    return {e["name"]: e for e in _job()["spec"]["template"]["spec"]["containers"][0]["env"]}


def _props(path):
    es = yaml.safe_load(path.read_text())
    return es, {d["secretKey"]: d["remoteRef"]["property"] for d in es["spec"]["data"]}


def test_job_creates_both_roles_and_both_logins():
    s = _script()
    for var in ("V1_USER", "V1_PASSWORD", "V1_READWRITE_USER", "V1_READWRITE_PASSWORD"):
        assert f'"${var}"' in s, var
    assert "('kagent_owner', false" in s and "('kagent_readwrite', false" in s
    assert "GRANT USAGE ON SCHEMA kagent TO kagent_readwrite" in s
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE kagent_owner IN SCHEMA kagent GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO kagent_readwrite" in s
    assert "GRANT USAGE ON SCHEMA extensions TO kagent_owner, kagent_readwrite" in s


def test_job_reads_the_readwrite_login_from_the_postgresql_externalsecret():
    env = _env()
    es, props = _props(ES_PG)
    for var, key in (("V1_READWRITE_USER", "readwrite-user"), ("V1_READWRITE_PASSWORD", "readwrite-password")):
        ref = env[var]["valueFrom"]["secretKeyRef"]
        assert ref["name"] == es["spec"]["target"]["name"] and ref["key"] == key
    assert props["readwrite-user"] == "v1-readwrite-user"
    assert props["readwrite-password"] == "v1-readwrite-password"


def test_kagent_externalsecrets_match_the_chart_defaults_and_the_vault_properties():
    owner, oprops = _props(ES_OWNER)
    rw, rprops = _props(ES_RW)
    assert owner["spec"]["target"]["name"] == "kagent-postgres-owner"
    assert "ownerConnectionString" in owner["spec"]["target"]["template"]["data"]
    assert rw["spec"]["target"]["name"] == "kagent-postgres-readwrite"
    assert "readWriteConnectionString" in rw["spec"]["target"]["template"]["data"]
    assert oprops == {"user": "v1-user", "password": "v1-password", "db": "v1-db-name"}
    assert rprops == {"user": "v1-readwrite-user", "password": "v1-readwrite-password", "db": "v1-db-name"}
    for es in (owner, rw):
        assert es["spec"]["secretStoreRef"] == {"name": "vault-kagent-db", "kind": "SecretStore"}
        tmpl = next(iter(es["spec"]["target"]["template"]["data"].values()))
        assert "postgresql.postgresql.svc.cluster.local:5432" in tmpl and "sslmode=disable" in tmpl
        assert "options=" not in tmpl, "kagent assumes the role itself; no role in the string"


def test_audit_role_jobs_run_after_the_database_jobs():
    """Same wave = a race on a clean-database sync: the audit Job may see kagent_v1 absent,
    skip its grants and still succeed (init-kagent-audit-role.yaml, 'does not exist yet')."""
    db_wave = int(_job()["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])
    for name in ("init-kagent-audit-role.yaml", "init-agent-audit-web-role.yaml"):
        job = yaml.safe_load((ROOT / "base-apps/postgresql" / name).read_text())
        assert int(job["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"]) > db_wave, name
