"""Deduplication, week-over-week delta, and the exit-code contract."""
import json

OWNED = "852893458518.dkr.ecr."


def _f(image, vid, pkg, installed="1.0", fixed="2.0", severity="HIGH"):
    return {"image": image, "id": vid, "pkg": pkg, "installed": installed,
            "fixed": fixed, "severity": severity, "title": "t"}


# --------------------------------------------------------------- dedup

def test_load_reports_drops_exact_duplicates(render, tmp_path):
    """Trivy reports a package once per Result, so the same CVE+pkg+version
    arrives several times for one image. Measured on the real 2026-08-18 run:
    24,359 findings collapse to 23,385 unique, and 394 'actionable' to 383."""
    (tmp_path / "r.json").write_text(json.dumps({
        "ArtifactName": "img",
        "Results": [
            {"Vulnerabilities": [
                {"VulnerabilityID": "CVE-1", "PkgName": "wheel",
                 "InstalledVersion": "0.45.1", "FixedVersion": "0.46.2",
                 "Severity": "HIGH", "Title": "t"}]},
            {"Vulnerabilities": [
                {"VulnerabilityID": "CVE-1", "PkgName": "wheel",
                 "InstalledVersion": "0.45.1", "FixedVersion": "0.46.2",
                 "Severity": "HIGH", "Title": "t"}]},
        ],
    }))
    findings, _, _ = render.load_reports(str(tmp_path))
    assert len(findings) == 1, "identical CVE+pkg+version must collapse to one row"


def test_load_reports_keeps_same_cve_at_different_versions(render, tmp_path):
    """form-data 2.5.5 and 4.0.5 under one CVE are two real problems."""
    (tmp_path / "r.json").write_text(json.dumps({
        "ArtifactName": "img",
        "Results": [{"Vulnerabilities": [
            {"VulnerabilityID": "CVE-2", "PkgName": "form-data",
             "InstalledVersion": "2.5.5", "FixedVersion": "2.5.6",
             "Severity": "HIGH", "Title": "t"},
            {"VulnerabilityID": "CVE-2", "PkgName": "form-data",
             "InstalledVersion": "4.0.5", "FixedVersion": "4.0.6",
             "Severity": "HIGH", "Title": "t"},
        ]}],
    }))
    findings, _, _ = render.load_reports(str(tmp_path))
    assert len(findings) == 2, "different installed versions are distinct findings"


# --------------------------------------------------------------- delta

def test_previous_report_key_picks_the_newest_before_today(render):
    class FakeS3:
        def get_paginator(self, _):
            class P:
                def paginate(self, **kw):
                    return [{"Contents": [
                        {"Key": "cve-reports/2026-08-04.json"},
                        {"Key": "cve-reports/2026-08-11.json"},
                        {"Key": "cve-reports/2026-08-18.json"},
                        {"Key": "cve-reports/latest.json"},
                        {"Key": "cve-reports/2026-08-11.html"},
                    ]}]
            return P()
    got = render.previous_report_key(FakeS3(), "b", "cve-reports/", "2026-08-18")
    assert got == "cve-reports/2026-08-11.json", "today and non-dated keys must be ignored"


def test_previous_report_key_returns_none_on_the_first_ever_run(render):
    class FakeS3:
        def get_paginator(self, _):
            class P:
                def paginate(self, **kw):
                    return [{}]
            return P()
    assert render.previous_report_key(FakeS3(), "b", "cve-reports/", "2026-08-18") is None


def test_compute_delta_counts_new_and_resolved(render):
    prev = [_f(OWNED + "app:v1", "CVE-A", "p1"), _f(OWNED + "app:v1", "CVE-B", "p2")]
    cur = [_f(OWNED + "app:v2", "CVE-B", "p2"), _f(OWNED + "app:v2", "CVE-C", "p3")]
    new, resolved = render.compute_delta(cur, prev)
    assert new == 1, "CVE-C is new"
    assert resolved == 1, "CVE-A is gone"


def test_compute_delta_ignores_the_image_tag(render):
    """Rebuilding bumps the tag. A CVE that survives a rebuild is not 'new'."""
    prev = [_f(OWNED + "app:v1.4.12", "CVE-A", "p1")]
    cur = [_f(OWNED + "app:v1.5.0", "CVE-A", "p1")]
    new, resolved = render.compute_delta(cur, prev)
    assert (new, resolved) == (0, 0), "same CVE across a rebuild is unchanged"


# --------------------------------------------------- slack message shape

def test_slack_text_is_one_line_per_image_not_per_cve(render):
    act = [_f(OWNED + "a:v1", f"CVE-{i}", f"p{i}") for i in range(40)]
    act += [_f(OWNED + "b:v1", "CVE-X", "px", severity="CRITICAL")]
    text = render._slack_text(act, [], delta=None)
    lines = [l for l in text.split("\n") if l.strip()]
    assert len(lines) <= 6, f"41 findings must not produce {len(lines)} lines"
    assert "a:v1" in text and "b:v1" in text
    assert "CVE-0" not in text, "per-CVE detail belongs in the report, not Slack"


def test_slack_text_leads_with_the_delta_when_available(render):
    act = [_f(OWNED + "a:v1", "CVE-1", "p")]
    text = render._slack_text(act, [], delta=(3, 7))
    assert "+3" in text and "7" in text


# ------------------------------------------------------- exit semantics

def test_exit_code_is_zero_when_the_scan_succeeds(render, tmp_path, monkeypatch):
    """Red must mean 'the scan broke', not 'findings exist'. Findings exist
    every week; a run that is always red carries no signal."""
    (tmp_path / "r.json").write_text(json.dumps({
        "ArtifactName": OWNED + "a:v1",
        "Results": [{"Vulnerabilities": [
            {"VulnerabilityID": "CVE-1", "PkgName": "p", "InstalledVersion": "1",
             "FixedVersion": "2", "Severity": "CRITICAL", "Title": "t"}]}],
    }))
    posted = []
    monkeypatch.setattr(render.urllib.request, "urlopen",
                        lambda req, timeout=None: posted.append(req) or _Resp())
    rc = render.main([
        "--reports", str(tmp_path), "--out", str(tmp_path / "o"),
        "--owned-prefix", OWNED, "--webhook", "http://x/y",
    ])
    assert rc == 0, "actionable findings must NOT fail the run"
    assert posted, "but they must still alert"


def test_exit_code_is_one_when_an_image_could_not_be_scanned(render, tmp_path, monkeypatch):
    (tmp_path / "bad.json").write_text("{not json")
    monkeypatch.setattr(render.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp())
    rc = render.main([
        "--reports", str(tmp_path), "--out", str(tmp_path / "o"),
        "--owned-prefix", OWNED, "--webhook", "http://x/y",
    ])
    assert rc == 1, "an unscanned image means the scan did not do its job"


class _Resp:
    def read(self): return b"ok"
    def __enter__(self): return self
    def __exit__(self, *a): return False
