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
