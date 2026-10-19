# tests/beacon/test_tracing.py
"""beacon's Phase 3 on this side: the tracing app (Tempo, from beacon §T.19 the collector)
and what it shares with the rest of the platform."""
import hashlib
import json
import re
from pathlib import Path

import pytest
import yaml

from conftest import REPO, load, one

TRACING = REPO / "base-apps" / "tracing"
INFRA = "base-apps/tracing-aws-infrastructure"
SMOKE = "base-apps/tracing/smoke-job.yaml"
PINNED = re.compile(r"^[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}$")


def images(doc) -> list[str]:
    out = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "image" and isinstance(v, str):
                    out.append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)
    return out


def tracing_images() -> set[str]:
    """Every image the tracing app (and, once it exists, the operator app) runs, smoke aside."""
    found = set()
    for path in sorted(TRACING.glob("*.yaml")):
        if path.name in ("smoke-job.yaml", "catalog-info.yaml", "mkdocs.yml"):
            continue
        for doc in load(path.relative_to(REPO).as_posix()):
            found.update(images(doc))
    operator = REPO / "base-apps" / "opentelemetry-operator.yaml"
    if operator.exists():
        values = yaml.safe_load(yaml.safe_load(operator.read_text())["spec"]["source"]["helm"]["values"])
        found.update(images(values))
    return found


def smoke_images() -> set[str]:
    job = one(SMOKE, "Job", "tracing-smoke")
    return {c["image"] for c in job["spec"]["template"]["spec"]["containers"]}


def test_v18_every_tracing_image_is_pinned_by_digest():
    for image in tracing_images() | smoke_images():
        assert PINNED.match(image), f"{image} is not pinned by digest"


def test_v18_tracing_smoke_covers_every_tracing_image():
    """beacon §V.18: nothing third-party rolls out before a node ran it. The smoke Job is
    PreSync, so a digest that is not in it would reach a node untested."""
    missing = tracing_images() - smoke_images()
    assert not missing, f"add to {SMOKE}: {sorted(missing)}"


def test_v18_tracing_smoke_is_a_presync_gate_on_application_nodes():
    job = one(SMOKE, "Job", "tracing-smoke")
    assert job["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    assert job["spec"]["backoffLimit"] == 0
    spec = job["spec"]["template"]["spec"]
    assert spec["nodeSelector"] == {"node.kubernetes.io/workload": "application"}
    assert spec["securityContext"]["runAsNonRoot"] is True


def tempo_config() -> dict:
    cm = one("base-apps/tracing/tempo.yaml", "ConfigMap", "tempo-config")
    return yaml.safe_load(cm["data"]["tempo.yaml"])


def test_t18_tempo_config_checksum_is_current():
    cm = one("base-apps/tracing/tempo.yaml", "ConfigMap", "tempo-config")
    want = hashlib.sha256(json.dumps(cm["data"], sort_keys=True).encode()).hexdigest()
    deploy = one("base-apps/tracing/tempo.yaml", "Deployment", "tempo")
    have = deploy["spec"]["template"]["metadata"]["annotations"]["checksum/config"]
    assert have == want, f"update checksum/config in base-apps/tracing/tempo.yaml to {want}"


def test_t18_one_bucket_name_everywhere():
    bucket = one(f"{INFRA}/s3-bucket.yaml", "Bucket", tempo_config()["storage"]["trace"]["s3"]["bucket"])
    name = bucket["metadata"]["name"]
    lifecycle = one(f"{INFRA}/s3-lifecycle.yaml", "BucketLifecycleConfiguration", "tempo-traces-lifecycle")
    assert lifecycle["spec"]["forProvider"]["bucketRef"]["name"] == name
    policy = json.loads(one(f"{INFRA}/iam.yaml", "Policy", "tempo-s3-access")["spec"]["forProvider"]["policy"])
    assert set(policy["Statement"][0]["Resource"]) == {f"arn:aws:s3:::{name}", f"arn:aws:s3:::{name}/*"}
    region = bucket["spec"]["forProvider"]["region"]
    s3 = tempo_config()["storage"]["trace"]["s3"]
    assert s3["region"] == region and s3["endpoint"] == f"s3.{region}.amazonaws.com"


def test_t18_tempo_reads_the_key_crossplane_writes():
    key = one(f"{INFRA}/iam.yaml", "AccessKey", "tempo-s3-key")
    ref = key["spec"]["writeConnectionSecretToRef"]
    assert ref["namespace"] == "tracing"
    env = {e["name"]: e for e in one("base-apps/tracing/tempo.yaml", "Deployment", "tempo")
           ["spec"]["template"]["spec"]["containers"][0]["env"]}
    for var, k in (("AWS_ACCESS_KEY_ID", "username"), ("AWS_SECRET_ACCESS_KEY", "attribute.secret")):
        sel = env[var]["valueFrom"]["secretKeyRef"]
        assert (sel["name"], sel["key"]) == (ref["name"], k), var


def test_t18_retention_is_a_week_and_the_bucket_outlives_it_by_a_day():
    cfg = tempo_config()
    assert cfg["backend_scheduler"]["provider"]["compaction"]["compaction"]["block_retention"] == "168h"
    assert cfg["backend_worker"]["compaction"]["block_retention"] == "168h"
    lifecycle = one(f"{INFRA}/s3-lifecycle.yaml", "BucketLifecycleConfiguration", "tempo-traces-lifecycle")
    assert lifecycle["spec"]["forProvider"]["rule"][0]["expiration"] == [{"days": 8}]


def test_t18_tempo_takes_otlp_grpc_only_and_reports_nothing_home():
    cfg = tempo_config()
    assert cfg["distributor"]["receivers"] == {"otlp": {"protocols": {"grpc": {"endpoint": "0.0.0.0:4317"}}}}
    assert cfg["usage_report"]["reporting_enabled"] is False
    assert "metrics_generator" not in cfg  # off until measured (beacon §R.5)


BARE = re.compile(r"(?<!beacon )§[TVRIB]\.\d+")


@pytest.mark.parametrize("path", sorted(
    [p.relative_to(REPO).as_posix() for p in TRACING.glob("*.yaml")]
    + [p.relative_to(REPO).as_posix() for p in TRACING.glob("*.md")]
    + [p.relative_to(REPO).as_posix() for p in (REPO / INFRA).glob("*.yaml")]
    + ["base-apps/tracing.yaml", "appsets/managed-apps/tracing-aws-infrastructure.yaml"]))
def test_v27_tracing_files_cite_beacon_sections_only(path):
    text = Path(REPO / path).read_text()
    folded = re.sub(r"beacon\s*\n\s*(#\s*)?§", "beacon §", text)
    assert not BARE.findall(folded), BARE.findall(folded)
