"""main() driven across MULTIPLE runs against a stateful fake world.

WHY THIS FILE EXISTS. Every main() test used to be single-shot: build one
situation, call main() once, assert. That is precisely why the annotation-keyed
design survived review - its failure cannot appear in a single run. It needs
two: the first moves DNS and opens a PR, the second (with the PR still
unmerged, because a human has to merge it) reads an annotation that has not
caught up and draws the wrong conclusion from it.

So `World` below persists the things that actually persist in production -
Route 53's contents, the policy on `main`, the open PRs, the branches - across
calls, and the tests drive several runs and assert on where DNS ended up. A
single-run test cannot express "the records are stranded"; these can.
"""
import hashlib
import json
import urllib.error

import pytest

A = "76.97.4.210"
B = "76.97.99.1"
C = "76.97.55.55"

MANAGED = "argocd.arigsela.com, grafana.arigsela.com"

POLICY_TEMPLATE = (
    "apiVersion: security.istio.io/v1\n"
    "kind: AuthorizationPolicy\n"
    "metadata:\n"
    "  name: gateway-allow\n"
    "  namespace: istio-ingress\n"
    "  annotations:\n"
    '    arigsela.com/wan-ip: "%s"\n'
    "spec:\n"
    "  rules:\n"
    "    - to:\n"
    "        - operation:\n"
    "            hosts:\n"
    "              - argocd.arigsela.com\n"
    "      from:\n"
    "        - source:\n"
    "            ipBlocks:\n"
    "              - %s/32\n"
)


def _policy(ip):
    return POLICY_TEMPLATE % (ip, ip)


def _http_error(code, msg):
    return urllib.error.HTTPError("https://api.github.com/", code, msg, {}, None)


class World:
    """Route 53, the policy on main, the branches and the open PRs - all of
    which outlive a single run of the job, exactly as in production."""

    def __init__(self, wan, declared, records):
        self.wan = wan
        self.main_policy = _policy(declared)
        self.records = dict(records)          # bare hostname -> single A value
        self.branches = {}                    # branch name -> committed policy text
        self.prs = []                         # [{"branch":..., "html_url":...}]
        self.notifications = []
        self.github_down = False
        self.aws_calls = []

    # ---- seams ---------------------------------------------------------
    def detect_wan_ip(self):
        return self.wan

    def aws_json(self, args):
        self.aws_calls.append(args[1])
        if args[:2] == ["route53", "list-resource-record-sets"]:
            return {"ResourceRecordSets": [
                {"Name": name + ".", "Type": "A", "TTL": 300,
                 "ResourceRecords": [{"Value": value}]}
                for name, value in sorted(self.records.items())
            ]}
        if args[:2] == ["route53", "change-resource-record-sets"]:
            batch = json.loads(args[args.index("--change-batch") + 1])
            for change in batch["Changes"]:
                assert change["Action"] == "UPSERT"
                rrs = change["ResourceRecordSet"]
                self.records[rrs["Name"].rstrip(".")] = rrs["ResourceRecords"][0]["Value"]
            return {"ChangeInfo": {"Status": "PENDING"}}
        raise AssertionError("unexpected aws args %r" % (args,))

    @staticmethod
    def _blob_sha(text):
        return hashlib.sha1(text.encode()).hexdigest()

    def _content_of(self, ref):
        if ref == "main":
            return self.main_policy
        return self.branches.get(ref, self.main_policy)

    def github(self, method, path, body=None):
        if self.github_down:
            raise urllib.error.URLError("github unreachable")
        if method == "GET" and path.startswith("/pulls?"):
            branch = path.split(":")[-1]
            return [pr for pr in self.prs if pr["branch"] == branch]
        if method == "GET" and path == "/git/ref/heads/main":
            return {"object": {"sha": "mainsha"}}
        if method == "POST" and path == "/git/refs":
            branch = body["ref"].split("refs/heads/")[1]
            if branch in self.branches:
                raise _http_error(422, "Reference already exists")
            self.branches[branch] = self.main_policy
            return {}
        if method == "GET" and path.startswith("/contents/"):
            ref = path.split("?ref=")[1]
            text = self._content_of(ref)
            import base64
            return {"content": base64.b64encode(text.encode()).decode(),
                    "sha": self._blob_sha(text)}
        if method == "PUT" and path.startswith("/contents/"):
            import base64
            branch = body["branch"]
            head = self._content_of(branch)
            if body["sha"] != self._blob_sha(head):
                # This is the real GitHub behaviour that produced the permanent
                # crash-loop: a stale blob SHA is a 409, not a silent overwrite.
                raise _http_error(409, "is at %s but expected %s"
                                  % (self._blob_sha(head), body["sha"]))
            self.branches[branch] = base64.b64decode(body["content"]).decode()
            return {}
        if method == "POST" and path == "/pulls":
            url = "https://github.com/arigsela/kubernetes/pull/%d" % (len(self.prs) + 1)
            self.prs.append({"branch": body["head"], "html_url": url})
            return {"html_url": url}
        raise AssertionError("unexpected github call: %s %s" % (method, path))

    def notify(self, payload, post=None):
        self.notifications.append(payload)

    # ---- convenience ---------------------------------------------------
    def merge_open_prs(self):
        """What the human does. Only then does main's annotation move."""
        for pr in self.prs:
            self.main_policy = self.branches[pr["branch"]]
        self.prs = []


@pytest.fixture
def world(reconcile, monkeypatch):
    def build(wan=A, declared=A, records=None):
        w = World(wan, declared,
                  records if records is not None
                  else {"argocd.arigsela.com": A, "grafana.arigsela.com": A})
        monkeypatch.setenv("MANAGED_HOSTNAMES", MANAGED)
        monkeypatch.setattr(reconcile, "detect_wan_ip", lambda: w.detect_wan_ip())
        monkeypatch.setattr(reconcile, "aws_json", w.aws_json)
        monkeypatch.setattr(reconcile, "github", w.github)
        monkeypatch.setattr(reconcile, "notify", w.notify)
        return w

    return build


# ---------------------------------------------------------------------------


def test_steady_state_writes_nothing_and_stays_silent(reconcile, world):
    """288 runs a day. It still LISTS the zone every run - that is the point of
    reconciling rather than change-detecting - but it must not write, must not
    open a PR, and must not notify."""
    w = world()

    assert reconcile.main() == 0

    assert w.aws_calls == ["list-resource-record-sets"]
    assert w.prs == []
    assert w.notifications == []


def test_a_rotation_moves_dns_and_opens_exactly_one_pr(reconcile, world):
    w = world()
    assert reconcile.main() == 0

    w.wan = B
    assert reconcile.main() == 0

    assert w.records == {"argocd.arigsela.com": B, "grafana.arigsela.com": B}
    assert len(w.prs) == 1
    assert w.prs[0]["branch"] == "automation/wan-ip-%s" % B
    assert len(w.notifications) == 1
    assert w.notifications[0]["new"] == B


def test_an_unmerged_pr_is_found_again_and_not_re_notified(reconcile, world):
    """Between rotation and merge this runs every 5 minutes with DNS already
    fixed and the PR already open. Notifying on each of those trains the
    operator to ignore the notification."""
    w = world()
    w.wan = B
    reconcile.main()
    w.notifications.clear()

    for _ in range(3):
        assert reconcile.main() == 0

    assert len(w.prs) == 1, "one rotation, one PR - no matter how many runs"
    assert w.notifications == []


def test_dns_follows_a_flap_back_to_the_previous_address(reconcile, world):
    """THE REGRESSION THIS FILE WAS WRITTEN FOR.

    A -> B (records move to B, PR opened, human has not merged) -> back to A.
    On that third run the old code compared the detected address against the
    ANNOTATION, which still said A because the PR was still open, concluded
    `detected == declared`, printed "in sync, nothing to do" and returned. All
    21 records stayed on B - a dead address - permanently and silently, since
    every subsequent run drew the same conclusion.

    The DNS half must not consult the annotation at all.
    """
    w = world()
    assert reconcile.main() == 0                      # run 1: steady state on A

    w.wan = B
    assert reconcile.main() == 0                      # run 2: rotation to B
    assert set(w.records.values()) == {B}

    w.wan = A
    assert reconcile.main() == 0                      # run 3: flap back to A

    assert w.records == {"argocd.arigsela.com": A, "grafana.arigsela.com": A}, (
        "records stranded on the address the job moved them to - this is the "
        "silent permanent DNS breakage the restructure exists to prevent")
    # The allow-list is genuinely back in sync (main still declares A), so there
    # is nothing new to propose - only the now-stale B PR from run 2 remains.
    assert [pr["branch"] for pr in w.prs] == ["automation/wan-ip-%s" % B]


def test_a_second_rotation_before_the_first_pr_merges(reconcile, world):
    """A -> B -> C with nothing merged in between. DNS must land on C, and the
    allow-list must get its own PR for C rather than being blocked by B's."""
    w = world()
    w.wan = B
    reconcile.main()

    w.wan = C
    assert reconcile.main() == 0

    assert set(w.records.values()) == {C}
    assert sorted(pr["branch"] for pr in w.prs) == [
        "automation/wan-ip-%s" % C, "automation/wan-ip-%s" % B]
    # And a further run is a no-op rather than a third PR.
    w.notifications.clear()
    assert reconcile.main() == 0
    assert len(w.prs) == 2
    assert w.notifications == []


def test_merging_the_pr_returns_the_system_to_steady_state(reconcile, world):
    w = world()
    w.wan = B
    reconcile.main()
    w.merge_open_prs()
    w.notifications.clear()

    assert reconcile.main() == 0

    assert set(w.records.values()) == {B}
    assert w.prs == []
    assert w.notifications == []
    assert "change-resource-record-sets" not in w.aws_calls[-1:]


def test_dns_is_still_reconciled_when_github_is_unreachable(reconcile, world, capsys):
    """A GitHub outage or an expired PAT must not be able to hold the automatic
    half hostage. Route 53 needs nothing from GitHub, so it completes first;
    only then can the allow-list half fail."""
    w = world()
    w.wan = B
    w.github_down = True

    with pytest.raises(urllib.error.URLError):
        reconcile.main()

    assert set(w.records.values()) == {B}, "DNS must be fixed before GitHub is touched"
    out = capsys.readouterr().out
    assert "allow-list PR could" in out and "NOT" in out


def test_a_crash_notifies_before_re_raising(reconcile, world):
    """Without this the only failure signal is a non-zero pod exit in a
    namespace nobody watches."""
    w = world()
    w.wan = B
    w.github_down = True

    with pytest.raises(urllib.error.URLError):
        reconcile.main()

    assert len(w.notifications) == 1
    payload = w.notifications[0]
    assert payload["event"] == "wan-ip-monitor-failed"
    assert payload["error_type"] == "URLError"
    assert "github unreachable" in payload["error"]


def test_a_failing_notification_never_masks_the_original_exception(reconcile, world, monkeypatch):
    """notify() is best effort. If the webhook is down too, the run must still
    fail with the real cause, not with the notifier's."""
    w = world()
    w.wan = B
    w.github_down = True
    monkeypatch.setattr(reconcile, "notify", reconcile.notify)  # the real one

    def dead_post(payload):
        raise OSError("n8n is down as well")

    monkeypatch.setattr(reconcile, "post_json", dead_post)

    with pytest.raises(urllib.error.URLError):
        reconcile.main()


def test_detection_failure_notifies_and_touches_nothing(reconcile, world, monkeypatch):
    w = world()

    def no_ip():
        raise RuntimeError("no source returned a usable public IPv4")

    monkeypatch.setattr(reconcile, "detect_wan_ip", no_ip)

    with pytest.raises(RuntimeError):
        reconcile.main()

    assert w.aws_calls == [], "nothing may be written from an unknown address"
    assert w.notifications[0]["error_type"] == "RuntimeError"


def test_the_branch_409_loop_cannot_strand_the_pr(reconcile, world):
    """End-to-end version of the 409 crash-loop: a branch survives from a
    closed-not-merged PR, then the ISP hands the same address back. The World's
    PUT enforces GitHub's real blob-SHA precondition, so a run that committed
    main's SHA onto that branch would raise 409 here - every time, forever."""
    w = world()
    w.wan = B
    reconcile.main()                       # opens the PR for B
    branch = "automation/wan-ip-%s" % B
    w.prs = []                             # human closed it without merging
    assert branch in w.branches            # ...but the branch survived

    w.wan = A
    reconcile.main()                       # back to A: DNS follows
    w.wan = B                              # ...and the ISP recycles B
    assert reconcile.main() == 0

    assert set(w.records.values()) == {B}
    assert [pr["branch"] for pr in w.prs] == [branch], (
        "the recycled address must still get a PR; the 409 left it uncreated "
        "forever, and the existing-PR short-circuit never engaged because "
        "there was no PR to find")
