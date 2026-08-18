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
