import json
import urllib.request


class FakeS3:
    """Minimal stand-in for a boto3 S3 client."""

    def __init__(self):
        self.puts = []

    def put_object(self, Bucket, Key, Body, ContentType=None, **kw):
        self.puts.append({"bucket": Bucket, "key": Key,
                          "body": Body, "content_type": ContentType})


def test_publish_writes_latest_and_dated_for_both_formats(render):
    s3 = FakeS3()
    keys = render.publish(s3, "b", "cve-reports/", "2026-08-18", '{"a":1}', "<html>")
    assert keys == [
        "cve-reports/2026-08-18.json",
        "cve-reports/2026-08-18.html",
        "cve-reports/latest.json",
        "cve-reports/latest.html",
    ], "dated copies must be written before latest, so latest is never newer than its history"
    assert all(p["bucket"] == "b" for p in s3.puts)


def test_publish_sets_content_types_so_browsers_render_the_html(render):
    s3 = FakeS3()
    render.publish(s3, "b", "cve-reports/", "2026-08-18", "{}", "<html>")
    by_key = {p["key"]: p["content_type"] for p in s3.puts}
    assert by_key["cve-reports/latest.html"] == "text/html; charset=utf-8"
    assert by_key["cve-reports/latest.json"] == "application/json"


def test_publish_bodies_are_bytes(render):
    s3 = FakeS3()
    render.publish(s3, "b", "cve-reports/", "2026-08-18", "{}", "<html>")
    assert all(isinstance(p["body"], bytes) for p in s3.puts)


OWNED = "852893458518.dkr.ecr."


def test_main_posts_to_slack_and_keeps_exit_code_when_publish_raises(render, tmp_path, monkeypatch):
    """publish() is a convenience. Even if report generation/S3-write blows up,
    the Slack/n8n POST -- what actually pages someone -- and the exit-code
    contract (1 iff there are actionable findings) must survive untouched."""
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "report.json").write_text(json.dumps({
        "ArtifactName": OWNED + "mine:v1",
        "Results": [{"Vulnerabilities": [
            {"VulnerabilityID": "CVE-2026-9", "PkgName": "libfoo",
             "InstalledVersion": "1.0.0", "FixedVersion": "1.2.3",
             "Severity": "CRITICAL", "Title": "bad"}]}],
    }))
    out = tmp_path / "out"

    def raise_publish(*a, **kw):
        raise RuntimeError("S3 is on fire")
    monkeypatch.setattr(render, "publish", raise_publish)

    posted = []

    class FakeResponse:
        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=30):
        posted.append(req)
        return FakeResponse()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    rc = render.main([
        "--reports", str(reports),
        "--out", str(out),
        "--owned-prefix", OWNED,
        "--webhook", "http://n8n.example.invalid/hook",
        "--bucket", "some-bucket",
        "--date", "2026-08-18",
    ])

    assert posted, "publish() raising must not suppress the Slack/n8n POST"
    assert rc == 1, "exit-code contract (1 iff actionable findings) must survive a publish failure"
