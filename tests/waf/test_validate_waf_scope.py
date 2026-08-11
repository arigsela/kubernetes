import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate-waf-scope.py"
_spec = importlib.util.spec_from_file_location("validate_waf_scope", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

import yaml


def _policy(rules):
    return {"spec": {"rules": rules}}


def _plugin(regex):
    return {
        "spec": {
            "pluginConfig": {
                "directives_map": {
                    "default": [
                        "SecRuleEngine On",
                        f'SecRule REQUEST_HEADERS:Host "!@rx {regex}" '
                        '"id:9000,phase:1,pass,nolog,ctl:ruleEngine=Off"',
                    ]
                }
            }
        }
    }


def test_rule_without_from_is_public():
    doc = _policy([{"to": [{"operation": {"hosts": ["grafana.arigsela.com"]}}]}])
    assert mod.public_hosts(doc) == {"grafana.arigsela.com"}


def test_rule_with_from_is_not_public():
    doc = _policy([
        {
            "to": [{"operation": {"hosts": ["argocd.arigsela.com"]}}],
            "from": [{"source": {"ipBlocks": ["73.7.190.154/32"]}}],
        }
    ])
    assert mod.public_hosts(doc) == set()


def test_host_public_if_any_rule_lacks_from():
    """n8n appears twice: path-scoped webhooks (public) + admin UI (restricted).
    It is public."""
    doc = _policy([
        {
            "to": [{"operation": {"hosts": ["n8n.arigsela.com"],
                                  "paths": ["/webhook/*"]}}]
        },
        {
            "to": [{"operation": {"hosts": ["n8n.arigsela.com"]}}],
            "from": [{"source": {"ipBlocks": ["73.7.190.154/32"]}}],
        },
    ])
    assert "n8n.arigsela.com" in mod.public_hosts(doc)


def test_port_suffix_hosts_are_normalised():
    """The policy lists both "host" and "host:*"; they are one host."""
    doc = _policy([
        {"to": [{"operation": {"hosts": ["grafana.arigsela.com",
                                         "grafana.arigsela.com:*"]}}]}
    ])
    assert mod.public_hosts(doc) == {"grafana.arigsela.com"}


def test_lan_only_hosts_are_exempt():
    """vault.local has no from: clause but is not internet-reachable."""
    doc = _policy([
        {"to": [{"operation": {"hosts": ["vault.local", "vault.10.0.1.110"]}}]}
    ])
    assert mod.public_hosts(doc) == set()


def test_waf_scope_extracts_hosts_from_rule_9000():
    doc = _plugin(r"^(grafana|oncall|n8n)\.arigsela\.com(:\d+)?$")
    assert mod.waf_scope_hosts(doc) == {
        "grafana.arigsela.com",
        "oncall.arigsela.com",
        "n8n.arigsela.com",
    }


def test_check_passes_when_covered():
    policy = _policy([{"to": [{"operation": {"hosts": ["grafana.arigsela.com"]}}]}])
    plugin = _plugin(r"^(grafana)\.arigsela\.com(:\d+)?$")
    assert mod.check(policy, plugin) == []


def test_check_reports_uncovered_public_host():
    policy = _policy([
        {"to": [{"operation": {"hosts": ["grafana.arigsela.com"]}}]},
        {"to": [{"operation": {"hosts": ["newapp.arigsela.com"]}}]},
    ])
    plugin = _plugin(r"^(grafana)\.arigsela\.com(:\d+)?$")
    problems = mod.check(policy, plugin)
    assert len(problems) == 1
    assert "newapp.arigsela.com" in problems[0]


def test_scope_regex_must_carry_port_group():
    """Dropping (:\\d+)? reintroduces the Host-header bypass."""
    doc = _plugin(r"^(grafana)\.arigsela\.com$")
    policy = _policy([{"to": [{"operation": {"hosts": ["grafana.arigsela.com"]}}]}])
    problems = mod.check(policy, doc)
    assert any("(:\\d+)?" in p or "port" in p.lower() for p in problems)


def test_real_repo_is_consistent():
    root = Path(__file__).resolve().parents[2]
    policy = yaml.safe_load(
        (root / "base-apps/istio-ingress/authorizationpolicy.yaml").read_text())
    plugin = yaml.safe_load(
        (root / "base-apps/istio-waf/wasmplugin.yaml").read_text())
    assert mod.check(policy, plugin) == []
