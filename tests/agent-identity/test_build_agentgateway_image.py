"""scripts/build-agentgateway-image.sh (§T.133): no network, no Docker, no AWS.

The script runs for real against stub `docker`, `aws` and `curl` on PATH, as in
tests/models/test_build_model_image.py. What matters: a binary that does not match the pinned
sha256 is rejected (and deleted) before anything is built, an existing ECR tag is never
overwritten with different content, the script never creates the ECR repository (Terraform
owns it), and the pins and the Dockerfile keep the shape the Deployments rely on.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "build-agentgateway-image.sh"
DOCKERFILE = REPO / "images" / "agentgateway" / "Dockerfile"
VERSION = re.search(r"^AGW_VERSION=(\S+)$", SCRIPT.read_text(), re.M).group(1)

DOCKER_STUB = """#!/usr/bin/env bash
# `docker buildx build ... --metadata-file F ...`: record the call, write a digest to F.
echo "$*" >> "$STUB_LOG"
[ "$1" = login ] && { cat >/dev/null; exit 0; }
prev=""; for a in "$@"; do [ "$prev" = --metadata-file ] && printf '{"containerimage.digest": "%s"}' "$BUILT_DIGEST" > "$a"; prev="$a"; done
"""
AWS_STUB = """#!/usr/bin/env bash
echo "aws $*" >> "$STUB_LOG"
case "$*" in
  *describe-repositories*) [ "$ECR_REPO_EXISTS" = 1 ] ;;
  *describe-images*) [ -n "$ECR_DIGEST" ] && echo "$ECR_DIGEST" || echo None ;;
  *get-login-password*) echo token ;;
esac
"""
CURL_STUB = """#!/usr/bin/env bash
# `curl -fL --retry 3 -o OUT URL`: write CURL_BODY to OUT, or fail like an HTTP 404.
echo "curl $*" >> "$STUB_LOG"
[ -n "$CURL_BODY" ] || exit 22
prev=""; for a in "$@"; do [ "$prev" = -o ] && printf '%s' "$CURL_BODY" > "$a"; prev="$a"; done
"""


def _run(tmp_path, *args, env_extra=None, script=SCRIPT):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("docker", DOCKER_STUB), ("aws", AWS_STUB), ("curl", CURL_STUB)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    env = {"PATH": f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin",
           "HOME": str(tmp_path), "IMAGE_CACHE": str(tmp_path / "cache"),
           "STUB_LOG": str(tmp_path / "calls.log"), "BUILT_DIGEST": "sha256:" + "a" * 64,
           "ECR_DIGEST": "", "ECR_REPO_EXISTS": "1", "CURL_BODY": ""}
    env.update(env_extra or {})
    r = subprocess.run(["bash", str(script), *args], capture_output=True, text=True, env=env,
                       timeout=60)
    log = (tmp_path / "calls.log").read_text() if (tmp_path / "calls.log").exists() else ""
    return r, log


def _stage_verified_binary(tmp_path):
    """Put a file whose sha256 is the pinned one where the script caches the download.

    The real binary is 90+ MB, so instead the test rewrites the pin: it copies the script with
    AGW_SHA256 set to the sha256 of a small stand-in file. Everything else is the real script."""
    import hashlib
    body = b"stand-in agentgateway binary"
    want = hashlib.sha256(body).hexdigest()
    ctx = tmp_path / "cache" / "agentgateway" / VERSION
    ctx.mkdir(parents=True)
    (ctx / "agentgateway").write_bytes(body)
    script = tmp_path / "build-agentgateway-image.sh"
    text = re.sub(r"^AGW_SHA256=\S+$", f"AGW_SHA256={want}", SCRIPT.read_text(), flags=re.M)
    # REPO_ROOT is derived from the script's own path; keep it pointing at this repo.
    text = text.replace('REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"', f'REPO_ROOT="{REPO}"')
    script.write_text(text)
    return script


def test_help_and_unknown_argument(tmp_path):
    r, _ = _run(tmp_path, "--help")
    assert r.returncode == 0 and "build-agentgateway-image.sh [--push]" in r.stdout
    r, _ = _run(tmp_path, "--pash")
    assert r.returncode == 2 and "unknown argument" in r.stderr


def test_a_binary_with_the_wrong_sha256_is_rejected_and_deleted(tmp_path):
    ctx = tmp_path / "cache" / "agentgateway" / VERSION
    ctx.mkdir(parents=True)
    bad = ctx / "agentgateway"
    bad.write_text("not the release binary")
    r, log = _run(tmp_path)
    assert r.returncode == 2 and "!= pinned" in r.stderr, r.stderr
    assert not bad.exists(), "a file failing verification must be deleted"
    assert "buildx" not in log, "nothing may be built from an unverified binary"


def test_a_fresh_download_is_verified_too(tmp_path):
    r, log = _run(tmp_path, env_extra={"CURL_BODY": "tampered"})
    assert f"releases/download/{VERSION}/agentgateway-linux-amd64" in log
    assert r.returncode == 2 and "!= pinned" in r.stderr, r.stderr
    assert "buildx" not in log


def test_a_failed_download_builds_nothing(tmp_path):
    r, log = _run(tmp_path)
    assert r.returncode != 0 and "buildx" not in log


def test_a_verified_binary_is_built_for_the_nodes_without_attestations(tmp_path):
    script = _stage_verified_binary(tmp_path)
    r, log = _run(tmp_path, script=script)
    assert r.returncode == 0, r.stdout + r.stderr
    build = [line for line in log.splitlines() if line.startswith("buildx build")]
    assert len(build) == 1, log
    assert "--platform linux/amd64" in build[0]
    assert "--provenance=false --sbom=false" in build[0]
    assert f"-f {DOCKERFILE}" in build[0]
    assert "rewrite-timestamp=true" in build[0]
    assert "push=true" not in log and "aws " not in log


def test_the_script_never_creates_the_ecr_repository(tmp_path):
    script = _stage_verified_binary(tmp_path)
    r, log = _run(tmp_path, "--push", env_extra={"ECR_REPO_EXISTS": "0"}, script=script)
    assert r.returncode == 2 and "ecr.tf" in r.stderr, r.stdout + r.stderr
    assert "create-repository" not in log and "push=true" not in log


def test_an_existing_tag_with_different_content_is_never_overwritten(tmp_path):
    script = _stage_verified_binary(tmp_path)
    r, log = _run(tmp_path, "--push", env_extra={"ECR_DIGEST": "sha256:" + "b" * 64}, script=script)
    assert r.returncode == 2 and "bump TAG" in r.stderr, r.stdout + r.stderr
    assert "push=true" not in log


def test_same_digest_already_in_ecr_is_skipped(tmp_path):
    script = _stage_verified_binary(tmp_path)
    r, log = _run(tmp_path, "--push", env_extra={"ECR_DIGEST": "sha256:" + "a" * 64}, script=script)
    assert r.returncode == 0 and "already pushed" in r.stdout, r.stdout + r.stderr
    assert "push=true" not in log


def test_a_new_tag_is_pushed_and_the_pin_printed(tmp_path):
    script = _stage_verified_binary(tmp_path)
    r, log = _run(tmp_path, "--push", script=script)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"name=852893458518.dkr.ecr.us-east-2.amazonaws.com/agentgateway:{VERSION},push=true" in log
    assert ("pin this:  852893458518.dkr.ecr.us-east-2.amazonaws.com/agentgateway:"
            f"{VERSION}@sha256:{'a' * 64}") in r.stdout


def test_pins_are_well_formed():
    text = SCRIPT.read_text()
    assert re.fullmatch(r"v\d+\.\d+\.\d+", VERSION), VERSION
    assert re.search(r"^AGW_SHA256=[0-9a-f]{64}$", text, re.M)
    # Renovate's regex manager (renovate.json5) reads the comment directly above the version.
    assert re.search(r"^# renovate: datasource=github-releases depName=agentgateway/agentgateway\n"
                     r"AGW_VERSION=", text, re.M)


def test_dockerfile_keeps_the_path_and_entrypoint_the_deployments_use():
    """--copy-self copies argv[0], and the init containers pass only `--copy-self <dest>`."""
    lines = [l for l in DOCKERFILE.read_text().splitlines() if l and not l.startswith("#")]
    assert lines[0] == "FROM scratch"
    assert "COPY --chmod=0755 agentgateway /app/agentgateway" in lines
    assert 'ENTRYPOINT ["/app/agentgateway"]' in lines
