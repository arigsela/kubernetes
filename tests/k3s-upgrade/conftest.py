import shutil
import subprocess

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: containerised end-to-end drills (minutes, needs a working Docker daemon)",
    )


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(
        ["docker", "info"], capture_output=True, timeout=30
    ).returncode == 0


requires_docker = pytest.mark.skipif(
    not docker_available(), reason="needs a running Docker daemon for the k3s drill"
)


def sha256_check(checksum_filename: str) -> list[str]:
    """Argv to verify a .sha256 manifest, whichever tool the platform ships.

    `shasum` is the BSD/macOS spelling; Linux CI runners have `sha256sum`. The scripts
    under test already handle both — the tests must too, or they pass on a laptop and
    fail on the runner.
    """
    if shutil.which("sha256sum"):
        return ["sha256sum", "-c", checksum_filename]
    return ["shasum", "-a", "256", "-c", checksum_filename]
