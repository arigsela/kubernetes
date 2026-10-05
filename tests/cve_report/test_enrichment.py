"""KEV and EPSS enrichment: rank findings by real-world risk, never block the report.

CISA's Known Exploited Vulnerabilities catalog (KEV) says "this is being exploited
now"; FIRST's EPSS gives a 30-day exploitation probability. Both are fetched once
per run and are best effort: a feed that can't be read leaves the fields null and
must never stop the S3 publish or the Slack post, exactly like the S3 block.
"""
import gzip
import json

OWNED = "852893458518.dkr.ecr."
KEV_URL = "https://feeds.example.invalid/kev.json"
EPSS_URL = "https://feeds.example.invalid/epss.csv.gz"

KEV_DOC = {"vulnerabilities": [{"cveID": "CVE-2024-0001"}, {"cveID": "CVE-2024-0002"}]}
EPSS_CSV = ("#model_version:v2025.03.14,score_date:2026-10-05T12:55:00Z\n"
            "cve,epss,percentile\n"
            "CVE-2024-0001,0.94210,0.99901\n"
            "CVE-2024-0003,0.00042,0.10000\n")


class _Resp:
    def __init__(self, body=b"ok"):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _feeds(kev=True, epss=True):
    """A fake urlopen: serves the two feeds by URL, records webhook POSTs."""
    posted = []

    def urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if url == KEV_URL:
            if not kev:
                raise OSError("kev feed down")
            return _Resp(json.dumps(KEV_DOC).encode())
        if url == EPSS_URL:
            if not epss:
                raise OSError("epss feed down")
            return _Resp(gzip.compress(EPSS_CSV.encode()))
        posted.append(json.loads(req.data))
        return _Resp()
    return urlopen, posted


def _finding(image, vid, severity="HIGH", fixed="1.2.3"):
    return {"image": image, "id": vid, "pkg": "libfoo", "installed": "1.0.0",
            "fixed": fixed, "severity": severity, "title": "t"}


def _trivy_report(path, image, vulns):
    path.write_text(json.dumps({"ArtifactName": image, "Results": [{"Vulnerabilities": [
        {"VulnerabilityID": vid, "PkgName": "p", "InstalledVersion": "1",
         "FixedVersion": fixed, "Severity": sev, "Title": "t"}
        for vid, sev, fixed in vulns]}]}))


# ------------------------------------------------------------------ loaders

def test_load_kev_returns_the_catalog_cve_ids(render, monkeypatch):
    urlopen, _ = _feeds()
    monkeypatch.setattr(render.urllib.request, "urlopen", urlopen)
    assert render.load_kev(KEV_URL) == {"CVE-2024-0001", "CVE-2024-0002"}


def test_load_kev_returns_none_when_the_feed_is_down(render, monkeypatch, capsys):
    urlopen, _ = _feeds(kev=False)
    monkeypatch.setattr(render.urllib.request, "urlopen", urlopen)
    assert render.load_kev(KEV_URL) is None
    assert "WARNING" in capsys.readouterr().err


def test_load_epss_parses_the_gzipped_csv_past_its_comment_line(render, monkeypatch):
    urlopen, _ = _feeds()
    monkeypatch.setattr(render.urllib.request, "urlopen", urlopen)
    scores = render.load_epss(EPSS_URL)
    assert scores == {"CVE-2024-0001": 0.9421, "CVE-2024-0003": 0.00042}


def test_load_epss_returns_none_on_unreadable_data(render, monkeypatch, capsys):
    monkeypatch.setattr(render.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(b"not gzip"))
    assert render.load_epss(EPSS_URL) is None
    assert "WARNING" in capsys.readouterr().err


# ------------------------------------------------------------------ enrich

def test_enrich_marks_kev_and_epss(render):
    findings = [_finding("img", "CVE-2024-0001"), _finding("img", "GHSA-xxxx")]
    render.enrich(findings, {"CVE-2024-0001"}, {"CVE-2024-0001": 0.9})
    assert (findings[0]["kev"], findings[0]["epss"]) == (True, 0.9)
    assert (findings[1]["kev"], findings[1]["epss"]) == (False, None)


def test_enrich_leaves_null_not_false_when_a_feed_is_missing(render):
    """False would claim 'checked, not exploited'. Null says 'not checked'."""
    findings = [_finding("img", "CVE-2024-0001")]
    render.enrich(findings, None, None)
    assert findings[0]["kev"] is None and findings[0]["epss"] is None


def test_summary_counts_kev_fixable_in_any_image_and_kev_actionable_in_ours(render):
    findings = [
        _finding(OWNED + "mine:v1", "CVE-2024-0001", "CRITICAL"),     # KEV, ours, fixable
        _finding("docker.io/up:v1", "CVE-2024-0002", "HIGH"),         # KEV, upstream, fixable
        _finding("docker.io/up:v1", "CVE-2024-0003", "HIGH", fixed=""),  # KEV, no fix
        _finding("docker.io/up:v1", "CVE-2024-0004", "MEDIUM"),       # KEV, too low
        _finding(OWNED + "mine:v1", "CVE-2024-9999", "HIGH"),         # not KEV
    ]
    render.enrich(findings, {"CVE-2024-0001", "CVE-2024-0002", "CVE-2024-0003",
                             "CVE-2024-0004"}, None)
    act = render.actionable(findings, OWNED)
    summary = render.summarise(findings, act)
    assert summary["kev_fixable"] == 2
    assert summary["kev_actionable"] == 1


# ------------------------------------------------------------------ slack text

def test_slack_text_names_exploited_images_including_upstream(render):
    kev = [_finding("docker.io/library/vault:1.18.1", "CVE-2024-0002"),
           _finding("docker.io/library/vault:1.18.1", "CVE-2024-0001")]
    text = render._slack_text([], [], kev_fixable=kev)
    assert "actively exploited" in text
    assert "vault:1.18.1" in text and "2" in text


def test_slack_text_has_no_kev_line_without_hits(render):
    text = render._slack_text([_finding(OWNED + "mine:v1", "CVE-1")], [])
    assert "actively exploited" not in text


# ------------------------------------------------------------------ main

def test_main_posts_for_an_upstream_kev_hit_even_with_nothing_actionable(
        render, tmp_path, monkeypatch):
    """Upstream images never alert on their own (design section 8). A KEV hit with
    a fix is the exception: exploitation is happening now."""
    _trivy_report(tmp_path / "r.json", "docker.io/up:v1",
                  [("CVE-2024-0002", "HIGH", "2.0")])
    urlopen, posted = _feeds()
    monkeypatch.setattr(render.urllib.request, "urlopen", urlopen)
    rc = render.main(["--reports", str(tmp_path), "--out", str(tmp_path / "o"),
                      "--owned-prefix", OWNED, "--webhook", "http://x/y",
                      "--kev-url", KEV_URL, "--epss-url", EPSS_URL])
    assert rc == 0
    assert len(posted) == 1 and posted[0]["kev"] == 1
    assert "actively exploited" in posted[0]["text"]


def test_main_still_reports_when_both_feeds_are_down(render, tmp_path, monkeypatch):
    _trivy_report(tmp_path / "r.json", OWNED + "mine:v1",
                  [("CVE-2024-0001", "CRITICAL", "2.0")])
    urlopen, posted = _feeds(kev=False, epss=False)
    monkeypatch.setattr(render.urllib.request, "urlopen", urlopen)
    rc = render.main(["--reports", str(tmp_path), "--out", str(tmp_path / "o"),
                      "--owned-prefix", OWNED, "--webhook", "http://x/y",
                      "--kev-url", KEV_URL, "--epss-url", EPSS_URL])
    full = json.loads((tmp_path / "o" / "full-report.json").read_text())
    assert rc == 0, "a feed outage is not a broken scan"
    assert posted, "the actionable finding must still alert"
    assert full["findings"][0]["kev"] is None and full["findings"][0]["epss"] is None


def test_main_without_feed_urls_does_not_fetch_or_add_fields(render, tmp_path, monkeypatch):
    _trivy_report(tmp_path / "r.json", OWNED + "mine:v1",
                  [("CVE-2024-0001", "CRITICAL", "2.0")])
    urlopen, posted = _feeds()
    monkeypatch.setattr(render.urllib.request, "urlopen", urlopen)
    render.main(["--reports", str(tmp_path), "--out", str(tmp_path / "o"),
                 "--owned-prefix", OWNED, "--webhook", "http://x/y"])
    full = json.loads((tmp_path / "o" / "full-report.json").read_text())
    assert "kev" not in full["findings"][0]
    assert len(posted) == 1, "only the webhook POST, no feed fetches"


# ------------------------------------------------------------------ html

def test_html_has_kev_and_epss_columns_and_a_kev_card(render):
    findings = [_finding("docker.io/up:v1", "CVE-2024-0002"),
                _finding(OWNED + "mine:v1", "CVE-2024-9999")]
    render.enrich(findings, {"CVE-2024-0002"}, {"CVE-2024-0002": 0.42})
    act = render.actionable(findings, OWNED)
    html = render.render_html(render.summarise(findings, act), findings, act, "2026-10-05")
    assert "<th>KEV</th>" in html and "<th>EPSS</th>" in html
    assert "Actively exploited" in html
    assert "data-kev='1'" in html, "the upstream KEV row must be marked for the filter"
    assert "42.0%" in html
