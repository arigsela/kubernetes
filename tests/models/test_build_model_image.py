"""scripts/build-model-image.sh (plan Phase 4, Task 4.1): no network, no Docker, no AWS.

The script is run for real against stub `docker`, `aws` and `curl` on PATH. What matters:
a download that does not match its pinned sha256 is rejected (and deleted), an existing ECR
tag is never overwritten with different content, and the pins themselves are well-formed.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "build-model-image.sh"
MODELS = REPO / "models"

DOCKER_STUB = """#!/usr/bin/env bash
# `docker buildx build ... --metadata-file F ...`: record the call, write a digest to F.
echo "$*" >> "$STUB_LOG"
[ "$1" = login ] && { cat >/dev/null; exit 0; }
prev=""; for a in "$@"; do [ "$prev" = --metadata-file ] && printf '{"containerimage.digest": "%s"}' "$BUILT_DIGEST" > "$a"; prev="$a"; done
"""
AWS_STUB = """#!/usr/bin/env bash
echo "aws $*" >> "$STUB_LOG"
case "$*" in
  *describe-repositories*) exit 0 ;;
  *describe-images*) [ -n "$ECR_DIGEST" ] && echo "$ECR_DIGEST" || echo None ;;
  *get-login-password*) echo token ;;
esac
"""


def _run(tmp_path, *args, env_extra=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("docker", DOCKER_STUB), ("aws", AWS_STUB),
                       ("curl", "#!/usr/bin/env bash\necho curl-called >> \"$STUB_LOG\"; exit 22\n")):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    env = {"PATH": f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin",
           "HOME": str(tmp_path), "MODEL_CACHE": str(tmp_path / "cache"),
           "STUB_LOG": str(tmp_path / "calls.log"), "BUILT_DIGEST": "sha256:" + "a" * 64,
           "ECR_DIGEST": ""}
    env.update(env_extra or {})
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env,
                       timeout=60)
    log = (tmp_path / "calls.log").read_text() if (tmp_path / "calls.log").exists() else ""
    return r, log


def test_help_and_unknown_model(tmp_path):
    r, _ = _run(tmp_path, "--help")
    assert r.returncode == 0 and "build-model-image.sh <qwen|nomic-embed-text>" in r.stdout
    r, _ = _run(tmp_path, "llama")
    assert r.returncode == 2 and "unknown model" in r.stderr


def test_a_download_with_the_wrong_sha256_is_rejected_and_deleted(tmp_path):
    rev = re.search(r"HF_REV=([0-9a-f]+)", SCRIPT.read_text()).group(1)
    ctx = tmp_path / "cache" / "qwen" / rev
    ctx.mkdir(parents=True)
    bad = ctx / "Qwen3.5-0.8B-UD-Q4_K_XL.gguf"
    bad.write_text("not the weights")
    r, log = _run(tmp_path, "qwen")
    assert r.returncode == 2 and "!= pinned" in r.stderr, r.stderr
    assert not bad.exists(), "a file failing verification must be deleted"
    assert "buildx" not in log, "nothing may be built from unverified weights"


def test_an_existing_tag_with_different_content_is_never_overwritten(tmp_path):
    r, log = _run(tmp_path, "nomic-embed-text", "--push",
                  env_extra={"ECR_DIGEST": "sha256:" + "b" * 64})
    assert r.returncode == 2 and "bump TAG" in r.stderr, r.stdout + r.stderr
    assert "push=true" not in log


def test_same_digest_already_in_ecr_is_skipped(tmp_path):
    r, log = _run(tmp_path, "nomic-embed-text", "--push",
                  env_extra={"ECR_DIGEST": "sha256:" + "a" * 64})
    assert r.returncode == 0 and "already pushed" in r.stdout, r.stdout + r.stderr
    assert "push=true" not in log


def test_push_is_reproducible_or_fails(tmp_path):
    """The pushed build must produce the digest the local build printed."""
    r, log = _run(tmp_path, "nomic-embed-text", "--push")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "push=true,unpack=false" in log
    assert "pin this:" in r.stdout and "@sha256:" + "a" * 64 in r.stdout


def test_pins_are_well_formed():
    s = SCRIPT.read_text()
    assert re.search(r"HF_REV=[0-9a-f]{40}\n", s), "Hugging Face revision must be a full commit sha"
    shas = re.findall(r"\.gguf ([0-9a-f]+)", s)
    assert len(shas) == 2 and all(len(h) == 64 for h in shas), shas
    assert re.search(r"MODEL_DIGEST=sha256:[0-9a-f]{64}", s)
    for flag in ("--provenance=false", "rewrite-timestamp=true", "SOURCE_DATE_EPOCH="):
        assert flag in s, f"reproducibility needs {flag}"


def test_dockerfiles_ship_data_only():
    for df in sorted(MODELS.glob("*/Dockerfile")):
        froms = [l for l in df.read_text().splitlines() if l.startswith("FROM ")]
        assert froms[-1].strip() == "FROM scratch", f"{df}: final stage must be FROM scratch"
    nomic = (MODELS / "nomic-embed-text" / "Dockerfile").read_text()
    assert "--platform=$BUILDPLATFORM" in nomic, "pull stage must run natively (no emulation)"
    assert "MODEL_DIGEST" in nomic and "grep -q" in nomic, "the model digest must be asserted"
