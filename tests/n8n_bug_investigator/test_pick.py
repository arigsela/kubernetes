# tests/n8n_bug_investigator/test_pick.py
"""Picking the issue on the POC's own labels, one at a time, with an atomic claim (§T.152, Task 4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest

from conftest import JsError

API = "https://api.github.com"
REPO = "arigsela/taskboard"
R = f"{API}/repos/{REPO}"
NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
NOT_BEFORE = "2026-10-01T00:00:00Z"
ME = "ari-pat"
KAGENT_LABELS = {"investigated", "investigation-failed", "fix-proposed", "from-alert", "bug"}


def ago(minutes: float) -> str:
    return (NOW - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def labeled(name, minutes, actor="ari", action="labeled"):
    return {"event": action, "label": {"name": name}, "actor": {"login": actor}, "created_at": ago(minutes)}


class FakeRepo:
    """Routes for a fake arigsela/taskboard: issues, their events, permissions, reactions, labels."""

    def __init__(self):
        self.issues, self.events, self.perms, self.claims, self.reactions = [], {}, {}, {}, {}
        self.event_pages = {}

    def issue(self, n, created="2026-10-20T10:00:00Z", labels=("bug",), author="ari", bug_by="ari", events=()):
        self.issues.append({"number": n, "title": f"Bug {n}", "body": f"body {n}", "created_at": created,
                            "html_url": f"https://github.com/{REPO}/issues/{n}", "user": {"login": author},
                            "labels": [{"name": x} for x in labels]})
        evs = [labeled("bug", 60 * 24 * 5, actor=bug_by)] if bug_by else []
        self.events[n] = evs + list(events)
        return self

    def routes(self):
        listing = f"{R}/issues?labels=bug&state=open&sort=created&direction=asc&per_page=100"
        routes = {f"GET {listing}": {"body": self.issues}, f"GET {API}/user": {"body": {"login": ME}}}
        for login, role in self.perms.items():
            routes[f"GET {R}/collaborators/{quote(login, safe='')}/permission"] = {"body": {"permission": role, "role_name": role}}
        for i in self.issues:
            n = i["number"]
            ev_url = f"{R}/issues/{n}/events?per_page=100"
            if n in self.event_pages:
                p1, p2 = self.event_pages[n]
                routes[f"GET {ev_url}"] = {"body": p1, "headers": {"link": f'<{ev_url}&page=2>; rel="next"'}}
                routes[f"GET {ev_url}&page=2"] = {"body": p2}
            else:
                routes[f"GET {ev_url}"] = {"body": self.events[n]}
            routes[f"POST {R}/issues/{n}/reactions"] = self.claims.get(n, {"status": 201, "body": {"id": 900 + n, "content": "eyes", "user": {"login": ME}, "created_at": ago(0)}})
            routes[f"GET {R}/issues/{n}/reactions?content=eyes&per_page=100"] = {"body": self.reactions.get(n, [])}
            for r in self.reactions.get(n, []):
                routes[f"DELETE {R}/issues/{n}/reactions/{r['id']}"] = {"status": 204}
            routes[f"POST {R}/issues/{n}/labels"] = {"body": []}
            for lab in ("n8n-investigating",):
                routes[f"DELETE {R}/issues/{n}/labels/{lab}"] = {"body": []}
        return routes


def pick(js, repo: FakeRepo, now=NOW, not_before=NOT_BEFORE):
    ctx = {"fake": "fakeHttp", "spec": {"routes": repo.routes()}}
    return js("pick", "pickVia", REPO, now.strftime("%Y-%m-%dT%H:%M:%SZ"), not_before, ctx=ctx, full=True)


def label_calls(calls):
    """(verb, issue, label) for every label added or removed."""
    out = []
    for c in calls:
        parts = c["url"].split("/issues/")
        if len(parts) < 2:
            continue
        n = int(parts[1].split("/")[0].split("?")[0])
        if c["method"] == "POST" and c["url"].endswith("/labels"):
            out += [("add", n, lab) for lab in c["body"]["labels"]]
        if c["method"] == "DELETE" and "/labels/" in c["url"]:
            out.append(("remove", n, c["url"].rsplit("/labels/", 1)[1]))
    return out


def reaction_calls(calls):
    return [(c["method"], c["url"].split("/issues/")[1]) for c in calls if "/reactions" in c["url"] and c["method"] != "GET"]


def trusted(repo, *logins):
    for login in logins:
        repo.perms[login] = "write"
    return repo


# --------------------------------------------------------------------------------- picking


def test_picks_oldest_trusted_bug(js):
    repo = trusted(FakeRepo().issue(3).issue(5, created="2026-10-22T10:00:00Z"), "ari")
    reply = pick(js, repo)
    v = reply["value"]
    assert v["issue"]["number"] == 3 and v["filedBy"] == "ari" and v["idle"] is None
    assert v["issue"] == {"number": 3, "title": "Bug 3", "body": "body 3", "created_at": "2026-10-20T10:00:00Z",
                          "html_url": f"https://github.com/{REPO}/issues/3", "labels": ["bug"]}
    assert label_calls(reply["calls"]) == [("add", 3, "n8n-investigating")]
    assert reaction_calls(reply["calls"]) == [("POST", "3/reactions")]
    assert reply["calls"][-2]["body"] == {"content": "eyes"}


def test_skips_own_done_failed_rehearsal(js):
    repo = (FakeRepo().issue(1, labels=("bug", "n8n-investigated")).issue(2, labels=("bug", "n8n-investigation-failed"))
            .issue(3, labels=("bug", "rehearsal")).issue(4))
    v = pick(js, trusted(repo, "ari"))["value"]
    assert v["issue"]["number"] == 4


def test_ignores_kagents_labels(js):
    repo = trusted(FakeRepo().issue(3, labels=("bug", "investigated", "fix-proposed", "from-alert")), "ari")
    assert pick(js, repo)["value"]["issue"]["number"] == 3


def test_untrusted_labeller_is_skipped(js):
    repo = FakeRepo().issue(3, bug_by="stranger").issue(5, created="2026-10-22T10:00:00Z")
    repo.perms.update({"stranger": "read", "ari": "write"})
    assert pick(js, repo)["value"]["issue"]["number"] == 5


def test_trusted_bot_is_picked(js):
    # No permission route exists: a trusted bot must not need one.
    repo = FakeRepo().issue(3, author="arigsela-implementer[bot]", bug_by="arigsela-implementer[bot]")
    v = pick(js, repo)["value"]
    assert v["issue"]["number"] == 3 and v["filedBy"] == "arigsela-implementer[bot]"


def test_issues_before_not_before_are_skipped(js):
    repo = trusted(FakeRepo().issue(3, created="2026-09-30T23:59:59Z").issue(5, created="2026-10-22T10:00:00Z"), "ari")
    assert pick(js, repo)["value"]["issue"]["number"] == 5


def test_not_before_is_required(js):
    repo = trusted(FakeRepo().issue(3), "ari")
    with pytest.raises(JsError) as e:
        pick(js, repo, not_before=None)
    assert "notBefore" in e.value.message


def test_no_issue_returns_null(js):
    reply = pick(js, FakeRepo())
    assert reply["value"] == {"issue": None, "filedBy": None, "released": [], "exhausted": [], "idle": "no eligible issue"}
    assert label_calls(reply["calls"]) == [] and reaction_calls(reply["calls"]) == []


# ------------------------------------------------------------------------- one at a time


def test_one_investigation_at_a_time(js):
    repo = trusted(FakeRepo().issue(3).issue(5, labels=("bug", "n8n-investigating"),
                                               events=[labeled("n8n-investigating", 5, actor=ME)]), "ari")
    reply = pick(js, repo)
    assert reply["value"]["issue"] is None and reply["value"]["idle"] == "busy #5"
    assert label_calls(reply["calls"]) == [] and reaction_calls(reply["calls"]) == []


def test_claim_is_won_by_one_caller(js):
    repo = trusted(FakeRepo().issue(3), "ari")
    repo.claims[3] = {"status": 200, "body": {"id": 903, "content": "eyes", "user": {"login": ME}, "created_at": ago(0.2)}}
    reply = pick(js, repo)
    assert reply["value"]["issue"] is None and reply["value"]["idle"] == "claimed elsewhere #3"
    assert label_calls(reply["calls"]) == []

    repo.claims[3] = {"status": 201, "body": {"id": 903, "content": "eyes", "user": {"login": ME}, "created_at": ago(0)}}
    reply = pick(js, repo)
    assert reply["value"]["issue"]["number"] == 3 and label_calls(reply["calls"]) == [("add", 3, "n8n-investigating")]


def test_stale_claim_without_lock_is_retaken(js):
    # A crash between claiming and locking leaves our 👀 alone; after 20 minutes it is taken again.
    repo = trusted(FakeRepo().issue(3), "ari")
    old = {"id": 903, "content": "eyes", "user": {"login": ME}, "created_at": ago(25)}
    repo.claims[3] = [{"status": 200, "body": old}, {"status": 201, "body": {**old, "id": 904, "created_at": ago(0)}}]
    repo.reactions[3] = [old]
    reply = pick(js, repo)
    assert reply["value"]["issue"]["number"] == 3
    assert reaction_calls(reply["calls"]) == [("POST", "3/reactions"), ("DELETE", "3/reactions/903"), ("POST", "3/reactions")]


# ----------------------------------------------------------------------------- stale locks


def test_stale_lock_is_released_as_failed(js):
    repo = trusted(FakeRepo().issue(3).issue(5, labels=("bug", "n8n-investigating"),
                                               events=[labeled("n8n-investigating", 21, actor=ME)]), "ari")
    repo.reactions[5] = [{"id": 77, "content": "eyes", "user": {"login": ME}, "created_at": ago(21)}]
    reply = pick(js, repo)
    v = reply["value"]
    assert v["released"] == [5] and v["issue"]["number"] == 3
    assert label_calls(reply["calls"]) == [("remove", 5, "n8n-investigating"), ("add", 5, "n8n-investigation-failed"),
                                           ("add", 3, "n8n-investigating")]
    assert ("DELETE", "5/reactions/77") in reaction_calls(reply["calls"])

    repo.events[5] = [labeled("bug", 9000), labeled("n8n-investigating", 19, actor=ME)]
    reply = pick(js, repo)
    assert reply["value"]["released"] == [] and reply["value"]["idle"] == "busy #5"
    assert label_calls(reply["calls"]) == [] and reaction_calls(reply["calls"]) == []


def test_stale_lock_uses_the_latest_lock_event(js):
    lock = "n8n-investigating"
    repo = trusted(FakeRepo().issue(5, labels=("bug", lock), events=[
        labeled(lock, 60, actor=ME), labeled(lock, 55, actor=ME, action="unlabeled"), labeled(lock, 5, actor=ME)]), "ari")
    v = pick(js, repo)["value"]
    assert v["released"] == [] and v["idle"] == "busy #5"


def test_events_are_paginated(js):
    lock = "n8n-investigating"
    repo = trusted(FakeRepo().issue(5, labels=("bug", lock)), "ari")
    repo.event_pages[5] = ([labeled("bug", 9000)] + [labeled("x", 100)] * 98 + [labeled(lock, 60, actor=ME)],
                           [labeled(lock, 55, actor=ME, action="unlabeled"), labeled(lock, 5, actor=ME)])
    v = pick(js, repo)["value"]
    assert v["released"] == [] and v["idle"] == "busy #5"


def test_attempts_are_capped(js):
    lock = "n8n-investigating"
    tries = []
    for m in (90, 60, 30):
        tries += [labeled(lock, m, actor=ME), labeled(lock, m - 2, actor=ME, action="unlabeled")]
    repo = trusted(FakeRepo().issue(3, events=tries).issue(5, created="2026-10-22T10:00:00Z"), "ari")
    reply = pick(js, repo)
    v = reply["value"]
    assert v["exhausted"] == [3] and v["issue"]["number"] == 5
    assert label_calls(reply["calls"]) == [("add", 3, "n8n-investigation-failed"), ("add", 5, "n8n-investigating")]


def test_release_claim_deletes_only_ours(js):
    repo = FakeRepo().issue(5)
    repo.reactions[5] = [{"id": 11, "content": "eyes", "user": {"login": ME}},
                         {"id": 12, "content": "eyes", "user": {"login": "someone"}}]
    ctx = {"fake": "fakeHttp", "spec": {"routes": repo.routes()}}
    reply = js("pick", "releaseClaimVia", REPO, 5, ctx=ctx, full=True)
    assert reaction_calls(reply["calls"]) == [("DELETE", "5/reactions/11")]


def test_never_touches_kagent_labels(js):
    lock = "n8n-investigating"
    scenarios = [
        trusted(FakeRepo().issue(3, labels=("bug", "investigated", "fix-proposed", "from-alert")), "ari"),
        trusted(FakeRepo().issue(3).issue(5, labels=("bug", lock), events=[labeled(lock, 30, actor=ME)]), "ari"),
        trusted(FakeRepo().issue(3, events=[labeled(lock, 90, actor=ME)] * 3).issue(4), "ari"),
    ]
    for repo in scenarios:
        for verb, n, lab in label_calls(pick(js, repo)["calls"]):
            assert lab not in KAGENT_LABELS, (verb, n, lab)


def test_known_persistent_errors_make_pick_idle(js):
    # A missing, expired or under-scoped token (or a rate limit) recurs on every 2-minute run: the
    # picker idles on it instead of saving an error execution each time (720 a day).
    assert "401" in js("pick", "idleReason", {"name": "GitHubError", "status": 401, "message": "Bad credentials"})
    assert "403" in js("pick", "idleReason", {"name": "GitHubError", "status": 403, "message": "rate limit"})
    for e in ({"name": "GitHubError", "status": 500, "message": "boom"}, {"name": "GitHubError", "status": 0, "message": "ECONNRESET"},
              {"name": "Error", "message": "x"}, None):
        assert js("pick", "idleReason", e) is None, e
