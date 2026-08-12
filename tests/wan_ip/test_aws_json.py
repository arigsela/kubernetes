"""Coverage for aws_json's boto3-backed implementation (see task-5-report.md,
Critical 1 / Important 3: the CronJob's image has no `aws` CLI, so aws_json
was rewritten from shelling out to the CLI to calling boto3 directly).

boto3 is NOT a dependency of this repo's test/CI environment - aws_json
imports it lazily, specifically so importing reconcile.py never requires it.
These tests keep that property: rather than installing the real boto3, they
inject a small fake module into sys.modules before calling aws_json, so the
`import boto3` inside aws_json's body picks up the fake instead of touching
the network or requiring the real package. No new dependency is introduced.
"""
import json
import sys
import types

import pytest


class _FakePaginator:
    """A minimal stand-in for boto3's Route53.Paginator.ListResourceRecordSets."""

    def __init__(self, pages):
        self._pages = pages
        self.calls = []

    def paginate(self, **kwargs):
        self.calls.append(kwargs)
        for page in self._pages:
            yield page


class _FakeClient:
    def __init__(self, pages=None, change_result=None):
        self.paginator = _FakePaginator(pages or [])
        self._change_result = change_result if change_result is not None else {}
        self.change_calls = []

    def get_paginator(self, operation_name):
        assert operation_name == "list_resource_record_sets"
        return self.paginator

    def change_resource_record_sets(self, **kwargs):
        self.change_calls.append(kwargs)
        return self._change_result


@pytest.fixture
def fake_boto3(monkeypatch):
    """Install a fake `boto3` module and return the FakeClient it will hand
    back from boto3.client("route53"), so the test can inspect what aws_json
    did with it."""
    client_holder = {}

    def install(pages=None, change_result=None):
        client = _FakeClient(pages=pages, change_result=change_result)
        client_holder["client"] = client

        fake_module = types.SimpleNamespace(client=lambda service: client)
        monkeypatch.setitem(sys.modules, "boto3", fake_module)
        return client

    return install


def test_list_paginates_across_multiple_pages(reconcile, fake_boto3):
    """The `aws` CLI this replaced auto-paginated; a raw
    client.list_resource_record_sets() call returns only one page. Without
    the paginator, records on page 2+ would never be seen - main() would
    print success and exit 0 having silently ignored part of the zone."""
    page1 = {"ResourceRecordSets": [{"Name": "a.arigsela.com.", "Type": "A", "TTL": 300,
                                      "ResourceRecords": [{"Value": "76.97.4.210"}]}]}
    page2 = {"ResourceRecordSets": [{"Name": "b.arigsela.com.", "Type": "A", "TTL": 300,
                                      "ResourceRecords": [{"Value": "76.97.4.210"}]}]}
    client = fake_boto3(pages=[page1, page2])

    result = reconcile.aws_json(
        ["route53", "list-resource-record-sets", "--hosted-zone-id", "Z0524483LR4JCFNLS7N0",
         "--output", "json"]
    )

    names = [r["Name"] for r in result["ResourceRecordSets"]]
    assert names == ["a.arigsela.com.", "b.arigsela.com."], (
        "records from both pages must be present - losing page 2 is exactly "
        "the silent-truncation failure mode this test guards against"
    )
    assert client.paginator.calls == [{"HostedZoneId": "Z0524483LR4JCFNLS7N0"}]


def test_list_with_a_single_page_still_works(reconcile, fake_boto3):
    page = {"ResourceRecordSets": [{"Name": "only.arigsela.com.", "Type": "A", "TTL": 300,
                                     "ResourceRecords": [{"Value": "76.97.4.210"}]}]}
    fake_boto3(pages=[page])

    result = reconcile.aws_json(
        ["route53", "list-resource-record-sets", "--hosted-zone-id", "ZTEST",
         "--output", "json"]
    )

    assert [r["Name"] for r in result["ResourceRecordSets"]] == ["only.arigsela.com."]


def test_change_passes_hosted_zone_id_and_change_batch_as_separate_kwargs(reconcile, fake_boto3):
    """build_change_batch's output is JSON-encoded into the CLI-shaped args
    list, then decoded back out inside aws_json before reaching boto3. This
    pins that round trip: the client must see HostedZoneId and ChangeBatch as
    two distinct kwargs (not, say, ChangeBatch nested inside a CLI-args blob),
    and the batch shape itself - Comment / Changes / Action=UPSERT /
    ResourceRecordSet - must survive intact."""
    client = fake_boto3(change_result={"ChangeInfo": {"Id": "/change/123", "Status": "PENDING"}})

    batch = {
        "Comment": "wan-ip-monitor: rotate to 76.97.99.1",
        "Changes": [
            {
                "Action": "UPSERT",
                "ResourceRecordSet": {
                    "Name": "grafana.arigsela.com.",
                    "Type": "A",
                    "TTL": 0,
                    "ResourceRecords": [{"Value": "76.97.99.1"}],
                },
            }
        ],
    }
    args = [
        "route53", "change-resource-record-sets",
        "--hosted-zone-id", "Z0524483LR4JCFNLS7N0",
        "--change-batch", json.dumps(batch),
        "--output", "json",
    ]

    reconcile.aws_json(args)

    assert len(client.change_calls) == 1
    call = client.change_calls[0]
    assert call["HostedZoneId"] == "Z0524483LR4JCFNLS7N0"
    assert call["ChangeBatch"] == batch
    assert call["ChangeBatch"]["Changes"][0]["Action"] == "UPSERT"


def test_change_batch_ttl_zero_survives_the_json_round_trip(reconcile, fake_boto3):
    """A TTL of 0 (e.g. grafana's record - see tests/wan_ip/test_route53.py)
    must arrive at the client as the integer 0, not a falsy-dropped or
    stringified value, after passing through json.dumps in build_change_batch
    and json.loads inside aws_json."""
    client = fake_boto3()
    batch = {
        "Comment": "x",
        "Changes": [
            {
                "Action": "UPSERT",
                "ResourceRecordSet": {
                    "Name": "grafana.arigsela.com.",
                    "Type": "A",
                    "TTL": 0,
                    "ResourceRecords": [{"Value": "76.97.99.1"}],
                },
            }
        ],
    }
    args = [
        "route53", "change-resource-record-sets",
        "--hosted-zone-id", "Z0524483LR4JCFNLS7N0",
        "--change-batch", json.dumps(batch),
        "--output", "json",
    ]

    reconcile.aws_json(args)

    ttl = client.change_calls[0]["ChangeBatch"]["Changes"][0]["ResourceRecordSet"]["TTL"]
    assert ttl == 0
    assert isinstance(ttl, int)


def test_unsupported_args_raise_rather_than_silently_no_op(reconcile, fake_boto3):
    fake_boto3()
    with pytest.raises(ValueError):
        reconcile.aws_json(["route53", "get-hosted-zone", "--id", "Z1"])
