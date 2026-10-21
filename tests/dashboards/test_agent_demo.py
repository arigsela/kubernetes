"""scripts/agent-demo.sh: three read-only agent runs before a demo, then the dashboard's address."""
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "agent-demo.sh"


@pytest.fixture
def stub(tmp_path):
    """A stand-in for scripts/ask-hk that records its arguments and exits with $STUB_EXIT on the
    agent named in $STUB_FAIL_AGENT."""
    log = tmp_path / "calls"
    fake = tmp_path / "ask-hk"
    fake.write_text(f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {log}
if [ "${{STUB_FAIL_AGENT:-}}" = "$2" ]; then exit "${{STUB_EXIT:-1}}"; fi
echo "an answer"
echo "context: ctx-$RANDOM"
""")
    fake.chmod(0o755)
    return fake, log


def run(fake, **env):
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                          env={**os.environ, "ASK_HK": str(fake), "GRAFANA_URL": "https://grafana.example", **env})


def test_three_agents_then_the_dashboard(stub):
    fake, log = stub
    r = run(fake)
    assert r.returncode == 0, r.stderr
    calls = log.read_text().splitlines()
    assert [c.split(" ")[1] for c in calls] == ["kagent/homelab-knowledge", "kagent/k8s-reader", "kagent/istio-agent"]
    assert all(c.startswith("--agent ") and "--context" not in c for c in calls)  # each a new session
    assert "https://grafana.example/d/agent-platform" in r.stdout


def test_questions_only_read():
    """No question asks an agent to change, restart or delete anything."""
    text = SCRIPT.read_text().lower()
    questions = text[text.index("questions=("):text.index(")\n\nfor")]
    for verb in ("restart", "delete", "scale", "apply", "create", "patch", "fix", "change"):
        assert verb not in questions, verb


@pytest.mark.parametrize("code", [1, 2])
def test_stops_at_the_first_failure_or_pause(stub, code):
    fake, log = stub
    r = run(fake, STUB_FAIL_AGENT="kagent/k8s-reader", STUB_EXIT=str(code))
    assert r.returncode == code
    assert len(log.read_text().splitlines()) == 2
    assert "d/agent-platform" not in r.stdout
