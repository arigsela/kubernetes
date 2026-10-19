# tests/substrate/conftest.py
"""Shared helpers for the Substrate suite: atelet's ECR credential provider (SPEC §T.129, §V.100).

The pieces live in four places that must agree: the chart values in base-apps/substrate.yaml, the
host files laid down by ansible/playbooks/ate-image-credentials.yml, the keys written by
scripts/provision-docs-checker-pull.sh, and the IAM user in Terraform. Design and the spike that
found the rules these tests pin: docs/superpowers/specs/2026-10-18-substrate-ecr-pulls.md.
"""
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SUBSTRATE_APP = REPO / "base-apps/substrate.yaml"
PLAYBOOK = REPO / "ansible/playbooks/ate-image-credentials.yml"
SCRIPT = REPO / "scripts/provision-docs-checker-pull.sh"
TERRAFORM = REPO / "terraform/roots/asela-cluster/docs-checker-pull-iam.tf"

# Where the chart mounts the bin dir and config inside atelet's container
# (chart 0.5.0-alpha2, templates/atelet.yaml:142-145, 225-235).
CONTAINER_BIN_DIR = "/run/image-credential-provider/bin"


def substrate_app() -> dict:
    return yaml.safe_load(SUBSTRATE_APP.read_text())


def substrate_values() -> dict:
    return substrate_app()["spec"]["source"]["helm"]["valuesObject"]


def provider_config() -> dict:
    """The CredentialProviderConfig atelet loads, parsed from the inline chart value."""
    text = substrate_values()["atelet"]["imageCredentialProviderConfigYAML"]
    assert isinstance(text, str) and text.strip(), "atelet.imageCredentialProviderConfigYAML must be a YAML string"
    return yaml.safe_load(text)


def the_provider() -> dict:
    providers = provider_config()["providers"]
    assert len(providers) == 1, f"expected exactly one provider, found {len(providers)}"
    return providers[0]


def provider_env() -> dict:
    return {e["name"]: e["value"] for e in the_provider().get("env", [])}


def playbook_vars() -> dict:
    plays = yaml.safe_load(PLAYBOOK.read_text())
    assert len(plays) == 1, "ate-image-credentials.yml is one play"
    return plays[0]["vars"]


_REF = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")


def rendered(value):
    """A playbook var with its plain `{{ other_var }}` references filled in from the play's vars."""
    pv = playbook_vars()
    if isinstance(value, dict):
        return {k: rendered(v) for k, v in value.items()}
    for _ in range(5):
        value = _REF.sub(lambda m: str(pv[m.group(1)]), value)
    assert "{{" not in value, f"unrendered template left in {value!r}"
    return value
