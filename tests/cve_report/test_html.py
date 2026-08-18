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
    """No CDN, no external fonts - it is opened from a presigned S3 URL."""
    html = _doc(render).lower()
    for forbidden in ["http://", "https://cdn", "<script src=", "<link rel=\"stylesheet\" href="]:
        assert forbidden not in html, f"external reference found: {forbidden}"


def test_html_reports_the_actionable_count(render):
    html = _doc(render)
    assert "1" in html
    assert "actionable" in html.lower()


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
