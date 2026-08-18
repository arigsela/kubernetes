import json

OWNED = "852893458518.dkr.ecr."


def _finding(image, severity="HIGH", fixed="1.2.3", vid="CVE-2026-1"):
    return {
        "image": image, "id": vid, "pkg": "libfoo",
        "installed": "1.0.0", "fixed": fixed,
        "severity": severity, "title": "example",
    }


def test_load_reports_reads_every_json(render, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "report.json").write_text(json.dumps({
        "ArtifactName": "img-a",
        "Results": [{"Vulnerabilities": [
            {"VulnerabilityID": "CVE-1", "PkgName": "p", "InstalledVersion": "1",
             "FixedVersion": "2", "Severity": "HIGH", "Title": "t"}]}],
    }))
    (tmp_path / "b" / "report.json").write_text(json.dumps({
        "ArtifactName": "img-b", "Results": [],
    }))

    findings, scanned, failed = render.load_reports(str(tmp_path))

    assert sorted(scanned) == ["img-a", "img-b"]
    assert failed == []
    assert len(findings) == 1
    assert findings[0]["image"] == "img-a"
    assert findings[0]["id"] == "CVE-1"


def test_load_reports_records_unparseable_as_failed_not_clean(render, tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    findings, scanned, failed = render.load_reports(str(tmp_path))
    assert findings == []
    assert scanned == []
    assert len(failed) == 1, "an unreadable report must be failed, never silently clean"


def test_actionable_requires_owned_severity_and_a_fix(render):
    findings = [
        _finding(OWNED + "mine:v1", "CRITICAL", "9.9"),          # counts
        _finding(OWNED + "mine:v1", "HIGH", "9.9"),              # counts
        _finding(OWNED + "mine:v1", "HIGH", ""),                 # no fix
        _finding(OWNED + "mine:v1", "MEDIUM", "9.9"),            # too low
        _finding("docker.io/upstream:v1", "CRITICAL", "9.9"),    # not ours
    ]
    got = render.actionable(findings, OWNED)
    assert len(got) == 2
    assert {f["severity"] for f in got} == {"CRITICAL", "HIGH"}


def test_summarise_counts_by_severity_and_image(render):
    # A real ECR reference always has a "/" between the registry host and the
    # repo (e.g. "...amazonaws.com/repo:tag"), which is what by_image's
    # split("/")[-1] extracts down to the short name. Written explicitly here
    # (rather than OWNED + "a:v1", which has no "/" at all) so this fixture
    # matches that shape instead of silently asserting against the whole
    # prefixed string.
    findings = [
        _finding(OWNED + "/a:v1", "CRITICAL"),
        _finding(OWNED + "/a:v1", "HIGH"),
        _finding(OWNED + "/b:v1", "HIGH"),
        _finding("docker.io/up:v1", "LOW", ""),
    ]
    s = render.summarise(findings, render.actionable(findings, OWNED))
    assert s["total"] == 4
    assert s["actionable"] == 3
    assert s["by_severity"]["CRITICAL"] == 1
    assert s["by_image"]["a:v1"] == 2
