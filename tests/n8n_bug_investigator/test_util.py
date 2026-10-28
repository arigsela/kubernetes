# tests/n8n_bug_investigator/test_util.py
"""util.js against the Python core it ports: times, windows, clip/fence, scrubbing (§T.152)."""
from __future__ import annotations

from datetime import timedelta


def test_parse_time_matches_core(core, js):
    for s in ["2026-10-27T15:43:00Z", "2026-10-27 15:43:00+02:00", "2026-10-27T15:43:00.1234567Z",
              "2026-10-27T15:43:00", "", "nope", None,
              "2026-10-27", "2026-10-27T15:43Z", "2026-10-27T15:43:00+0530", "2026-10-27T15:43:00.5-07:00",
              "2026-13-01T00:00:00Z", "2026-02-30T00:00:00Z", " 2026-10-27T15:43:00Z "]:
        assert js("util", "isoOfParsed", s) == core.util.iso(core.util.parse_time(s)), s


def test_window_matches_core(core, js, frozen_now):
    now = frozen_now
    h = lambda n: now + timedelta(hours=n)  # noqa: E731
    cases = [
        (None, None, h(-5)),        # both None: fallback ± 2 h
        (None, None, None),         # both None, no fallback: centred on now, end clamped to now
        (h(-10), None, None),       # start only
        (None, h(-6), None),        # end only
        (h(-3), h(5), None),        # end in the future → now
        (h(-1), h(-2), None),       # start >= end → end − pad
        (h(-50), h(-1), None),      # longer than 24 h → capped
    ]
    iso = core.util.iso
    for start, end, fallback in cases:
        py = [iso(x) for x in core.util.window(start, end, fallback)]
        assert js("util", "windowIso", iso(start), iso(end), iso(fallback), iso(now)) == py, (start, end, fallback)


def test_short_clip_fence_match_core(core, js):
    for s in ["2026-10-27T15:43:00Z", "2026-10-27 15:43:59+02:00", "", None, "nope"]:
        assert js("util", "shortOf", s) == core.gather._short(s), s
    for s, n in [("hello world", 5), ("  padded  ", 6), ("a" * 10, 10), ("😀😀😀😀", 3),
                 ("ééé", 4), ("\x1c text  ", 20), ("﻿text", 10), (None, 5),
                 ("word  " * 5, 8)]:
        assert js("util", "clip", s, n) == core.gather._clip(s, n), (s, n)
    for s in ["plain", "has ``` inside", "has ~~~~ tilde", "", "mix `` and ~~~~~~"]:
        assert js("util", "fence", s) == core.gather._fence(s), s


def test_scrub_matches_core(core, js):
    secret = "extra-secret-123"
    for s in ["Bearer abcdefghijklmnop", "ghp_" + "a" * 20, "github_pat_" + "b" * 22, "sk-ant-api03-xyz12345",
              "sha 0123456789abcdef0123456789abcdef01234567", "taskboard_http_requests_total_bucket_" + "x" * 30,
              "AKIAABCDEFGHIJKLMNOP", "Mixed41CharsBlobAAAAAAAAAAAAAAAAAAAAAAAAAaaaa9",
              f"token {secret} and again {secret}.", "ghp_" + "c" * 20 + " then ghp_" + "d" * 20,
              "path/to/some/very/long/file/name/that/is/over/forty/one/chars.py", "short-secret", "xoxb-1234567890-abc"]:
        out = js("util", "scrubText", s, [secret, "short"])
        assert out == core.util.scrub_text(s, [secret, "short"]), s
        assert secret not in out


def test_scrub_walks_json_like_values(core, js):
    obj = {"a": ["Bearer abcdefghijklmnop", 3, None, True], "b": {"c": "ghp_" + "e" * 20}, "d": 1.5}
    assert js("util", "scrub", obj, []) == core.util.scrub(obj, [])
