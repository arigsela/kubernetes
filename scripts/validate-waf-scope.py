#!/usr/bin/env python3
"""Assert that every internet-reachable public hostname on the ingress Gateway is
covered by the Coraza WAF's scope regex.

The design premise is: the WAF covers the hosts that are public by design. That
premise spans two files with nothing linking them —
base-apps/istio-ingress/authorizationpolicy.yaml decides who is public, and
base-apps/istio-waf/wasmplugin.yaml decides who is inspected. Without this check,
removing a `from:` block to make a host public leaves it with no IP allow-list AND
no WAF, and every other test still passes.

Exits non-zero if a public host is not in the WAF scope, or if the scope regex has
lost its port group.
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

# An exemption list ("_LAN_ONLY") used to live here for vault.local /
# vault.10.0.1.110, on the premise that LAN-only hostnames with no public DNS
# are not internet-reachable. That premise was wrong — Host-header routing
# needs no DNS, and both hosts were confirmed reachable from the public
# internet (see authorizationpolicy.yaml's 2026-08-11 correction). The
# exemption masked a real exposure instead of flagging it.
#
# Correct fix: restrict the hosts (give them a `from:` clause) rather than
# exempt them from this check. Do not reintroduce a "not really public"
# exemption list here — if a host is genuinely unreachable from the internet,
# that needs to be demonstrated the way this one's absence was: empirically,
# not asserted in a comment.

_RULE_9000 = re.compile(r'id:9000\b')
_SCOPE_RX = re.compile(r'!@rx\s+(\S+?)"')
_ALTERNATION = re.compile(r'\^\((?P<alts>[^)]+)\)(?P<suffix>[^"$]*)\$?')


def _strip_port(host):
    """"grafana.arigsela.com:*" and ":443" both name one host."""
    return host.split(":", 1)[0]


def public_hosts(policy_doc):
    """Hosts appearing in ANY rule that has no `from:` clause.

    "Any", not "all": n8n appears twice — once path-scoped with no `from:` (the
    public webhooks) and once IP-restricted (the admin UI). It is public.
    """
    found = set()
    for rule in policy_doc.get("spec", {}).get("rules", []) or []:
        if "from" in rule:
            continue
        for to in rule.get("to", []) or []:
            for host in to.get("operation", {}).get("hosts", []) or []:
                found.add(_strip_port(host))
    return found


def _default_directives(plugin_doc):
    return (
        plugin_doc.get("spec", {})
        .get("pluginConfig", {})
        .get("directives_map", {})
        .get("default", [])
        or []
    )


def _scope_regex(plugin_doc):
    """The negated-rx pattern from scope rule 9000, or None."""
    for directive in _default_directives(plugin_doc):
        if not _RULE_9000.search(directive):
            continue
        m = _SCOPE_RX.search(directive)
        if m:
            return m.group(1)
    return None


def waf_scope_hosts(plugin_doc):
    """Hostnames named by rule 9000's alternation, e.g. ^(a|b)\\.example\\.com$."""
    rx = _scope_regex(plugin_doc)
    if not rx:
        return set()
    m = _ALTERNATION.search(rx)
    if not m:
        return set()
    domain = m.group("suffix")
    # Drop the optional-port group and unescape the literal dots.
    domain = domain.replace(r"(:\d+)?", "").replace("\\.", ".")
    return {f"{alt}{domain}" for alt in m.group("alts").split("|")}


def check(policy_doc, plugin_doc):
    """Return a list of human-readable problems; empty means consistent."""
    problems = []

    rx = _scope_regex(plugin_doc)
    if rx is None:
        problems.append(
            "no scope rule id:9000 with a !@rx pattern found in the WAF "
            "directives - the WAF has no host scoping"
        )
        return problems

    if r"(:\d+)?" not in rx:
        problems.append(
            f"scope regex {rx!r} has no (:\\d+)? port group. Envoy strips the "
            "port when matching routes but Coraza's authority lookup is an exact "
            "string match, so 'Host: <host>:443' would bypass inspection "
            "entirely. See the design doc section 6.1."
        )

    covered = waf_scope_hosts(plugin_doc)
    for host in sorted(public_hosts(policy_doc) - covered):
        problems.append(
            f"{host} is public in authorizationpolicy.yaml (no `from:` clause) "
            f"but is not in the WAF scope regex {rx!r}. It is internet-facing "
            "with neither an IP allow-list nor L7 inspection. Add it to scope "
            "rule id:9000, or restrict it with a `from:` clause if it should "
            "not be public at all."
        )
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".", type=Path)
    args = ap.parse_args()

    policy_path = args.repo_root / "base-apps/istio-ingress/authorizationpolicy.yaml"
    plugin_path = args.repo_root / "base-apps/istio-waf/wasmplugin.yaml"

    for p in (policy_path, plugin_path):
        if not p.exists():
            print(f"ERROR: {p} not found", file=sys.stderr)
            return 1

    problems = check(
        yaml.safe_load(policy_path.read_text()),
        yaml.safe_load(plugin_path.read_text()),
    )
    if problems:
        print("WAF scope validation FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print("WAF scope OK: every public host is covered by the Coraza scope regex.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
