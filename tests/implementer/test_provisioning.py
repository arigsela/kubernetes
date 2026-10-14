# tests/implementer/test_provisioning.py
"""The provisioning script and the manifests agree on every name (a stray property name is
the failure the agent-docs runbook already documents)."""
import subprocess
from conftest import REPO

SCRIPT = REPO / "scripts/provision-implementer-vault.sh"


def test_script_parses_and_refuses_values_on_the_command_line():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    text = SCRIPT.read_text()
    assert "--key-file" in text and "--app-id" in text and "--installation-id" in text
    assert "GITHUB_APP_PRIVATE_KEY=$(cat" in text or 'GITHUB_APP_PRIVATE_KEY="$(<' in text
    assert "VAULT_ADDR:?" in text and "vault token lookup" in text


def test_script_names_match_the_contract():
    text = SCRIPT.read_text()
    for must in ("KEY=implementer-github-app", "bound_service_account_names=eso-implementer-mcp",
                 "bound_service_account_namespaces=kagent", "vault policy write implementer-mcp",
                 "auth/kubernetes/role/implementer-mcp", "GITHUB_APP_ID=", "GITHUB_APP_INSTALLATION_ID="):
        assert must in text, must
