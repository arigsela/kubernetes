import json
import sys
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


def _write_actionable_report(reports_dir):
    reports_dir.mkdir()
    (reports_dir / "report.json").write_text(json.dumps({
        "ArtifactName": OWNED + "mine:v1",
        "Results": [{"Vulnerabilities": [
            {"VulnerabilityID": "CVE-2026-9", "PkgName": "libfoo",
             "InstalledVersion": "1.0.0", "FixedVersion": "1.2.3",
             "Severity": "CRITICAL", "Title": "bad"}]}],
    }))


def _patch_urlopen(monkeypatch):
    """Record webhook posts instead of hitting the network. Returns the list
    that gets one entry per POST main() attempts."""
    posted = []

    class FakeResponse:
        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=30):
        posted.append(req)
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return posted


def test_main_posts_to_slack_and_keeps_exit_code_when_render_html_raises(
        render, tmp_path, monkeypatch):
    """The block the publish-is-a-convenience constraint protects spans
    render_html(), the report.html write, the boto3 import, and publish()
    itself. render_html() is reached first -- before boto3 is even imported --
    so this is the failure window Finding 1 was about: a plain RuntimeError
    here, with no boto3 involved at all, must still let the Slack/n8n POST
    happen and the exit-code contract hold.
    """
    reports = tmp_path / "reports"
    _write_actionable_report(reports)
    out = tmp_path / "out"

    def raise_render_html(*a, **kw):
        raise RuntimeError("rendering blew up")
    monkeypatch.setattr(render, "render_html", raise_render_html)

    posted = _patch_urlopen(monkeypatch)

    rc = render.main([
        "--reports", str(reports),
        "--out", str(out),
        "--owned-prefix", OWNED,
        "--webhook", "http://n8n.example.invalid/hook",
        "--bucket", "some-bucket",
        "--date", "2026-08-18",
    ])

    assert posted, "render_html() raising must not suppress the Slack/n8n POST"
    assert rc == 0, \
        "the scan succeeded, so it must stay green - a publish/render failure is\n"\
        " a warning, and findings alone never fail the run (contract changed 2026-08-18)"


def test_main_posts_to_slack_and_keeps_exit_code_when_publish_raises(
        render, tmp_path, monkeypatch):
    """Same property, for a failure inside publish() itself. Reaching that
    call requires `import boto3` to succeed first, and boto3 is not installed
    in this environment, so it is stubbed in sys.modules for the duration of
    the test -- a plain object with a .client() method is all main() touches
    before calling publish(), which is what actually raises here.
    """
    reports = tmp_path / "reports"
    _write_actionable_report(reports)
    out = tmp_path / "out"

    class FakeBoto3Module:
        def client(self, *a, **kw):
            return object()
    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3Module())

    def raise_publish(*a, **kw):
        raise RuntimeError("S3 is on fire")
    monkeypatch.setattr(render, "publish", raise_publish)

    posted = _patch_urlopen(monkeypatch)

    rc = render.main([
        "--reports", str(reports),
        "--out", str(out),
        "--owned-prefix", OWNED,
        "--webhook", "http://n8n.example.invalid/hook",
        "--bucket", "some-bucket",
        "--date", "2026-08-18",
    ])

    assert posted, "publish() raising must not suppress the Slack/n8n POST"
    assert rc == 0, \
        "the scan succeeded, so it must stay green - a publish failure is a\n"\
        " warning, not a red run (contract changed 2026-08-18)"


def test_main_publishes_generated_and_summary_but_leaves_full_report_untouched(
        render, tmp_path, monkeypatch):
    """The published S3 copy (dated + latest) must carry 'generated' and
    'summary' so a consumer of latest.json can tell how fresh the pointer is
    and read the actionable count without re-deriving it from thousands of
    raw findings. full-report.json -- the Argo artifact -- is a separate
    dict built earlier in main() and must not gain these keys.
    """
    reports = tmp_path / "reports"
    _write_actionable_report(reports)
    out = tmp_path / "out"

    s3 = FakeS3()

    class FakeBoto3Module:
        def client(self, *a, **kw):
            return s3
    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3Module())

    posted = _patch_urlopen(monkeypatch)

    rc = render.main([
        "--reports", str(reports),
        "--out", str(out),
        "--owned-prefix", OWNED,
        "--webhook", "http://n8n.example.invalid/hook",
        "--bucket", "some-bucket",
        "--date", "2026-08-18",
    ])

    assert rc == 0, "scan succeeded: green"
    assert posted

    by_key = {p["key"]: p["body"] for p in s3.puts}
    for key in ("cve-reports/2026-08-18.json", "cve-reports/latest.json"):
        body = json.loads(by_key[key])
        assert body["generated"] == "2026-08-18"
        assert body["summary"]["actionable"] == 1
        assert "scanned" in body and "findings" in body

    full_report = json.loads((out / "full-report.json").read_text())
    assert "generated" not in full_report, \
        "full-report.json is the Argo artifact; only the published copy gains fields"
    assert "summary" not in full_report
