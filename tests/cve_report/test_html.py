import re

OWNED = "852893458518.dkr.ecr."


def _f(image, severity="HIGH", fixed="1.2.3", vid="CVE-2026-1"):
    return {"image": image, "id": vid, "pkg": "libfoo", "installed": "1.0.0",
            "fixed": fixed, "severity": severity, "title": "example title"}


def _doc(render):
    findings = [
        _f(OWNED + "mine:v1", "CRITICAL", "9.9", "CVE-A"),
        _f(OWNED + "mine:v1", "HIGH", "", "CVE-B"),
        _f("docker.io/up:v1", "LOW", "", "CVE-C"),
    ]
    act = render.actionable(findings, OWNED)
    return render.render_html(
        render.summarise(findings, act), findings, act, "2026-08-18")


def test_html_is_a_complete_document(render):
    html = _doc(render)
    assert html.lstrip().startswith("<!doctype html>")
    assert "</html>" in html


def test_html_is_self_contained(render):
    """No CDN, no external fonts, no @import - it is opened from a presigned
    S3 URL where no external fetch is guaranteed to succeed.

    Asserts on FETCHES, not on the mere presence of a URL. Vulnerability titles
    come from an external feed and routinely contain them - the real 2026-08-18
    report carries five, e.g. "An integer overflow exists in the FTS5
    https://sqlite.org/fts5.html". Those are escaped <td> text and fetch
    nothing. A blanket `no https?:// anywhere` assertion passes on synthetic
    fixtures and fails on the first real report, which is the wrong way round.
    """
    html = _doc(render)
    assert not re.search(r"""(src|href)\s*=\s*['"]https?://""", html), \
        "external resource reference in a self-contained report"
    assert "@import" not in html.lower(), \
        "@import can pull an external stylesheet"


def test_html_allows_urls_inside_vulnerability_titles(render):
    """A URL in a title must not trip the self-containment check.

    Regression guard for the assertion above: the strict form of it failed
    against the first real report for exactly this reason.
    """
    findings = [{"image": "i", "id": "CVE-Y", "pkg": "libsqlite3-0",
                 "installed": "3.40.1", "fixed": "3.40.2", "severity": "HIGH",
                 "title": "integer overflow in FTS5 https://sqlite.org/fts5.html"}]
    act = render.actionable(findings, OWNED)
    html = render.render_html(
        render.summarise(findings, act), findings, act, "2026-08-18")
    assert "sqlite.org/fts5.html" in html, "the title text should survive"
    assert not re.search(r"""(src|href)\s*=\s*['"]https?://""", html)


def test_html_reports_the_actionable_count(render):
    html = _doc(render)
    m = re.search(r'class="big act">(\d+)<', html)
    assert m and m.group(1) == "1", \
        f"actionable count not rendered as 1: {m and m.group(1)}"


def test_html_includes_every_finding(render):
    html = _doc(render)
    for vid in ("CVE-A", "CVE-B", "CVE-C"):
        assert vid in html


def test_html_escapes_untrusted_text(render):
    """Titles come from an external vulnerability feed, not from us."""
    findings = [{"image": "i", "id": "CVE-X", "pkg": "p", "installed": "1",
                 "fixed": "2", "severity": "HIGH",
                 "title": "<script>alert(1)</script>"}]
    act = render.actionable(findings, OWNED)
    html = render.render_html(render.summarise(findings, act), findings, act, "2026-08-18")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
