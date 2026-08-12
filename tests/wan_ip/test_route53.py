"""The Route 53 selector.

`records_needing_update` selects by an EXPLICIT managed-name list plus "is not
already on the address detected right now" - never by "its value equals the
previously known address". The old value-based selector could only learn
"previous" from the allow-list annotation, which does not move until a human
merges a PR, so it stopped matching as soon as DNS and the annotation diverged
and left records stranded on a dead address while reporting success. These
tests pin the replacement, and pin the two safety rules that survived it.
"""
import pytest

CURRENT = "76.97.99.1"

MANAGED = [
    "argocd.arigsela.com",
    "grafana.arigsela.com",
    "already.arigsela.com",
    "multi.arigsela.com",
    "alias.arigsela.com",
]

RECORDSETS = [
    # Managed, still on the address the allow-list annotation names.
    {"Name": "argocd.arigsela.com.", "Type": "A", "TTL": 300,
     "ResourceRecords": [{"Value": "76.97.4.210"}]},
    # Managed, stranded on an address NOTHING in the system tracks - this is
    # the flap case, and the one a value-based selector can never find.
    {"Name": "grafana.arigsela.com.", "Type": "A", "TTL": 0,
     "ResourceRecords": [{"Value": "76.97.55.55"}]},
    # Managed and already correct.
    {"Name": "already.arigsela.com.", "Type": "A", "TTL": 300,
     "ResourceRecords": [{"Value": CURRENT}]},
    # NOT managed, and pointing somewhere else entirely. Never ours to move.
    {"Name": "elsewhere.arigsela.com.", "Type": "A", "TTL": 300,
     "ResourceRecords": [{"Value": "8.8.4.4"}]},
    # Not an A record.
    {"Name": "arigsela.com.", "Type": "NS", "TTL": 172800,
     "ResourceRecords": [{"Value": "ns-1337.awsdns-39.org."}]},
    # Managed but multi-value: not ours to rewrite from a single-address signal.
    {"Name": "multi.arigsela.com.", "Type": "A", "TTL": 300,
     "ResourceRecords": [{"Value": "76.97.4.210"}, {"Value": "1.2.3.4"}]},
    # Managed but an ALIAS record - no ResourceRecords at all.
    {"Name": "alias.arigsela.com.", "Type": "A",
     "AliasTarget": {"DNSName": "dualstack.elb.amazonaws.com.",
                     "HostedZoneId": "Z35SXDOTRQ7X7K",
                     "EvaluateTargetHealth": False}},
]


def _selected(reconcile, current=CURRENT, managed=None):
    return [r["Name"] for r in reconcile.records_needing_update(
        RECORDSETS, current, MANAGED if managed is None else managed)]


def test_selects_managed_records_that_are_not_on_the_detected_address(reconcile):
    assert _selected(reconcile) == ["argocd.arigsela.com.", "grafana.arigsela.com."]


def test_selects_a_record_stranded_on_a_completely_untracked_address(reconcile):
    """grafana sits on 76.97.55.55 - a value no annotation, no previous run and
    no config knows about. The whole point of selecting by name is that this
    record still gets corrected; a value-based selector would skip it forever."""
    assert "grafana.arigsela.com." in _selected(reconcile)


def test_leaves_a_managed_record_that_is_already_correct(reconcile):
    assert "already.arigsela.com." not in _selected(reconcile)


def test_never_touches_an_unmanaged_record_even_though_it_is_off_the_address(reconcile):
    """A hostname absent from MANAGED_HOSTNAMES fails safe: it is simply never
    considered, no matter what it currently points at."""
    assert "elsewhere.arigsela.com." not in _selected(reconcile)


def test_ignores_non_a_records(reconcile):
    got = reconcile.records_needing_update(RECORDSETS, CURRENT, MANAGED + ["arigsela.com"])
    assert all(r["Type"] == "A" for r in got)
    assert "arigsela.com." not in [r["Name"] for r in got]


def test_ignores_multi_value_records(reconcile):
    """A record with several values is not ours to rewrite blindly."""
    assert "multi.arigsela.com." not in _selected(reconcile)


def test_ignores_alias_records(reconcile):
    """An alias A record has no ResourceRecords at all. Under a naive "sole
    value != current" test it reads as zero values and therefore not current,
    and the UPSERT would flatten the alias into a plain A record. The
    exactly-one-value rule is what prevents that."""
    assert "alias.arigsela.com." not in _selected(reconcile)


def test_returns_nothing_when_every_managed_record_is_already_current(reconcile):
    assert reconcile.records_needing_update(
        RECORDSETS, CURRENT, ["already.arigsela.com"]) == []


def test_matching_ignores_the_trailing_dot_and_case(reconcile):
    """Route 53 returns fully-qualified, case-insensitive names; the env var is
    hand-maintained. A trailing dot or a stray capital must not silently make a
    managed record look unmanaged - that would strand it with no error."""
    assert _selected(reconcile, managed=["ARGOCD.arigsela.com."]) == ["argocd.arigsela.com."]


def test_change_batch_preserves_ttl_per_record(reconcile):
    records = reconcile.records_needing_update(RECORDSETS, CURRENT, MANAGED)
    batch = reconcile.build_change_batch(records, CURRENT)
    ttls = {c["ResourceRecordSet"]["Name"]: c["ResourceRecordSet"]["TTL"]
            for c in batch["Changes"]}
    assert ttls == {"argocd.arigsela.com.": 300, "grafana.arigsela.com.": 0}


def test_change_batch_upserts_the_new_address(reconcile):
    records = reconcile.records_needing_update(RECORDSETS, CURRENT, MANAGED)
    batch = reconcile.build_change_batch(records, CURRENT)
    assert all(c["Action"] == "UPSERT" for c in batch["Changes"])
    for change in batch["Changes"]:
        assert change["ResourceRecordSet"]["ResourceRecords"] == [{"Value": CURRENT}]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a.arigsela.com,b.arigsela.com", ["a.arigsela.com", "b.arigsela.com"]),
        ("  a.arigsela.com. , B.ARIGSELA.COM  ", ["a.arigsela.com", "b.arigsela.com"]),
        ("a.arigsela.com,,  ,b.arigsela.com", ["a.arigsela.com", "b.arigsela.com"]),
        ("", []),
    ],
)
def test_managed_hostnames_parsing(reconcile, raw, expected):
    assert reconcile.managed_hostnames(raw) == expected


def test_managed_hostnames_reads_the_env_var(reconcile, monkeypatch):
    monkeypatch.setenv("MANAGED_HOSTNAMES", "one.arigsela.com, two.arigsela.com.")
    assert reconcile.managed_hostnames() == ["one.arigsela.com", "two.arigsela.com"]


def test_managed_hostnames_is_empty_when_unset(reconcile, monkeypatch):
    """An empty list means "manage nothing", which reconcile_route53 treats as
    skip-the-DNS-half rather than as "every record is unmanaged, update none of
    them silently"."""
    monkeypatch.delenv("MANAGED_HOSTNAMES", raising=False)
    assert reconcile.managed_hostnames() == []


def test_cronjob_manages_every_hostname_the_allow_list_protects(reconcile):
    """The seeded list in cronjob.yaml must cover the hosts the Istio policy
    gates. A protected host missing from MANAGED_HOSTNAMES keeps resolving to
    the old WAN address after a rotation while the allow-list moves on - it
    would break and nothing would say why."""
    import pathlib

    import yaml

    repo = pathlib.Path(__file__).resolve().parents[2]
    cronjob = yaml.safe_load((repo / "base-apps" / "wan-ip-monitor" / "cronjob.yaml").read_text())
    env = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["env"]
    raw = [e for e in env if e["name"] == "MANAGED_HOSTNAMES"][0]["value"]
    managed = set(reconcile.managed_hostnames(raw))

    policy = yaml.safe_load(
        (repo / "base-apps" / "istio-ingress" / "authorizationpolicy.yaml").read_text())
    # Istio hosts come in pairs ("vault.arigsela.com" and "vault.arigsela.com:*")
    # and include LAN-only names (vault.local, vault.10.0.1.110) that have no
    # Route 53 record at all. Only public arigsela.com names are in scope here.
    protected = set()
    for rule in policy["spec"]["rules"]:
        if "from" not in rule:
            continue
        for entry in rule.get("to", []):
            for host in entry["operation"].get("hosts", []):
                name = reconcile.normalise_hostname(host.split(":")[0])
                if name.endswith(".arigsela.com"):
                    protected.add(name)

    assert protected, "expected the allow-list to name at least one protected host"
    assert protected <= managed, (
        "these allow-listed hosts are not in MANAGED_HOSTNAMES, so their A "
        "records would never follow a rotation: %s" % sorted(protected - managed))
