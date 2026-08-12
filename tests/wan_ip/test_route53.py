RECORDSETS = [
    {"Name": "argocd.arigsela.com.", "Type": "A", "TTL": 300,
     "ResourceRecords": [{"Value": "76.97.4.210"}]},
    {"Name": "grafana.arigsela.com.", "Type": "A", "TTL": 0,
     "ResourceRecords": [{"Value": "76.97.4.210"}]},
    {"Name": "elsewhere.arigsela.com.", "Type": "A", "TTL": 300,
     "ResourceRecords": [{"Value": "8.8.4.4"}]},
    {"Name": "arigsela.com.", "Type": "NS", "TTL": 172800,
     "ResourceRecords": [{"Value": "ns-1337.awsdns-39.org."}]},
    {"Name": "multi.arigsela.com.", "Type": "A", "TTL": 300,
     "ResourceRecords": [{"Value": "76.97.4.210"}, {"Value": "1.2.3.4"}]},
]


def test_selects_only_records_on_the_old_address(reconcile):
    got = reconcile.records_needing_update(RECORDSETS, "76.97.4.210")
    assert [r["Name"] for r in got] == ["argocd.arigsela.com.", "grafana.arigsela.com."]


def test_ignores_non_a_records(reconcile):
    got = reconcile.records_needing_update(RECORDSETS, "76.97.4.210")
    assert all(r["Type"] == "A" for r in got)


def test_ignores_multi_value_records(reconcile):
    """A record with several values is not ours to rewrite blindly."""
    got = reconcile.records_needing_update(RECORDSETS, "76.97.4.210")
    assert "multi.arigsela.com." not in [r["Name"] for r in got]


def test_returns_nothing_when_already_current(reconcile):
    assert reconcile.records_needing_update(RECORDSETS, "76.97.99.1") == []


def test_change_batch_preserves_ttl_per_record(reconcile):
    records = reconcile.records_needing_update(RECORDSETS, "76.97.4.210")
    batch = reconcile.build_change_batch(records, "76.97.99.1")
    ttls = {c["ResourceRecordSet"]["Name"]: c["ResourceRecordSet"]["TTL"]
            for c in batch["Changes"]}
    assert ttls == {"argocd.arigsela.com.": 300, "grafana.arigsela.com.": 0}


def test_change_batch_upserts_the_new_address(reconcile):
    records = reconcile.records_needing_update(RECORDSETS, "76.97.4.210")
    batch = reconcile.build_change_batch(records, "76.97.99.1")
    assert all(c["Action"] == "UPSERT" for c in batch["Changes"])
    for change in batch["Changes"]:
        assert change["ResourceRecordSet"]["ResourceRecords"] == [{"Value": "76.97.99.1"}]
