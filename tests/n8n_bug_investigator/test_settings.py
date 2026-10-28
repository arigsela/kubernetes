# tests/n8n_bug_investigator/test_settings.py
"""Every Taskboard-specific value in lib/ is a setting (§T.152, the handover package).

The defaults are this homelab's values, so the other suites (and the live POC) see no change;
these tests set each one to something else and check the code follows it.
"""
from __future__ import annotations

import json

import test_compare as TC
import test_pick as TP
import test_verify as TV
import world as W
from test_gather import CFG as GATHER_CFG

API = "https://api.github.com"
LABELS = {"bug": "defect", "lock": "ai-investigating", "done": "ai-investigated", "failed": "ai-investigation-failed",
          "skip": "no-ai"}
BOT = "helper[bot]"


class Repo(TP.FakeRepo):
    """test_pick's fake repo with another bug label and lock label."""

    def issue(self, n, labels=None, bug_by="ari", events=(), **kw):
        lead = [TP.labeled(LABELS["bug"], 60 * 24 * 5, actor=bug_by)] if bug_by else []
        return super().issue(n, labels=labels or (LABELS["bug"],), bug_by=None, events=lead + list(events), **kw)

    def routes(self):
        r = super().routes()
        old = f"GET {TP.R}/issues?labels=bug&state=open&sort=created&direction=asc&per_page=100"
        r[old.replace("labels=bug", f"labels={LABELS['bug']}")] = r.pop(old)
        for i in self.issues:
            r[f"DELETE {TP.R}/issues/{i['number']}/labels/{LABELS['lock']}"] = {"body": []}
        return r


def pick(js, repo, **extra):
    ctx = {"fake": "fakeHttp", "spec": {"routes": repo.routes()}}
    return js("pick", "pickVia", TP.REPO, TP.NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), TP.NOT_BEFORE, extra, ctx=ctx, full=True)


# ------------------------------------------------------------------------------------- pick


def test_pick_follows_the_configured_labels_and_bots(js):
    repo = (Repo().issue(1, labels=(LABELS["bug"], LABELS["done"])).issue(2, labels=(LABELS["bug"], LABELS["skip"]))
            .issue(3, labels=(LABELS["bug"], "n8n-investigated"), author=BOT, bug_by=BOT))
    reply = pick(js, repo, labels=LABELS, trustedBots=[BOT])
    assert reply["value"]["issue"]["number"] == 3 and reply["value"]["filedBy"] == BOT
    assert TP.label_calls(reply["calls"]) == [("add", 3, LABELS["lock"])]


def test_pick_trusts_only_the_configured_bots(js):
    # No permission route: the default bot is not trusted once trustedBots is set, so it is skipped.
    repo = Repo().issue(3, author="arigsela-implementer[bot]", bug_by="arigsela-implementer[bot]")
    assert pick(js, repo, labels=LABELS, trustedBots=[BOT])["value"]["issue"] is None


def test_pick_releases_a_stale_configured_lock(js):
    stale = TP.labeled(LABELS["lock"], 30)
    repo = TP.trusted(Repo().issue(3, labels=(LABELS["bug"], LABELS["lock"]), events=[stale]), "ari")
    reply = pick(js, repo, labels=LABELS, trustedBots=[])
    assert reply["value"]["released"] == [3]
    assert TP.label_calls(reply["calls"])[:2] == [("remove", 3, LABELS["lock"]), ("add", 3, LABELS["failed"])]


# ----------------------------------------------------------------------------------- finish


def test_finish_uses_the_configured_labels(js):
    item = {"issue": {"number": 4}, "failed": False}
    assert js("finish", "outcome", item, LABELS) == {"add": [LABELS["done"]], "remove": [LABELS["lock"]], "outcome": "investigated"}
    spent = {"issue": {"number": 4}, "failed": True, "failure": {"stage": "post", "spent": True}}
    assert js("finish", "outcome", spent, LABELS)["add"] == [LABELS["failed"]]


# ----------------------------------------------------------------------------------- render

COMMENT = {"mode": "acme", "header": "🐞 Bug triage by our agent", "footer": "acme/bug-bot"}


def test_comment_wording_is_configurable(core, js, frozen_now, tmp_path):
    v = TC.verified(core, tmp_path, frozen_now)
    body = js("render", "renderComment", v, {"model": "m", "stats": {}, "trail": None, "contaminated": [], "comment": COMMENT})
    assert body.startswith("<!-- bug-investigator mode=acme -->\n## 🐞 Bug triage by our agent\n")
    assert "<sub>acme/bug-bot · " in body and "n8nagent" not in body and "POC" not in body


def test_post_finds_its_own_comment_by_the_configured_mode(js):
    comments = f"GET {API}/repos/{W.REPO}/issues/41/comments?per_page=100"
    mine = [{"id": 4, "user": {"login": TP.ME}, "body": "<!-- bug-investigator mode=n8nagent -->\nother"},
            {"id": 5, "user": {"login": TP.ME}, "body": "<!-- bug-investigator mode=acme -->\nold"}]
    routes = {f"GET {API}/user": {"body": {"login": TP.ME}}, comments: {"body": mine},
              f"PATCH {API}/repos/{W.REPO}/issues/comments/5": {"body": {"id": 5}}}
    reply = js("render", "postVia", W.REPO, 41, "new", "acme", ctx={"fake": "fakeHttp", "spec": {"routes": routes}}, full=True)
    assert reply["value"] == "edited"
    assert [(c["method"], c["url"]) for c in reply["calls"] if c["method"] != "GET"] == [("PATCH", f"{API}/repos/{W.REPO}/issues/comments/5")]


def test_watched_branches_are_configurable(js):
    steps = [{"action": {"tool": "github_list_pull_requests", "toolInput": {"branch": "fix/bug-12"}}, "observation": "see fix/bug-3"},
             {"action": {"tool": "x", "toolInput": {}}, "observation": "agent/taskboard-issue-37"}]
    assert js("render", "sawKagentWork", steps, "fix/bug-(\\d+)") == ["fix/bug-3", "fix/bug-12"]
    assert js("render", "sawKagentWork", steps, None) == []


def test_trail_strips_the_configured_tool_names(js):
    steps = [{"action": {"tool": "datadog_search_logs", "toolInput": {"query": "service:checkout"}}, "observation": "[]"}]
    assert js("render", "trailFromSteps", steps, ["search_logs"])["steps"][0]["tool"] == "search_logs"
    assert js("render", "trailFromSteps", steps)["steps"][0]["tool"] == "datadog_search_logs"


# ----------------------------------------------------------------------------------- gather


def test_gather_without_a_deploy_repo_or_telemetry(js, frozen_now):
    w = W.rich_world(frozen_now)
    cfg = {**GATHER_CFG, "deployRepo": None, "deployPath": None, "telemetryUrl": None}
    reply = js("gather", "gatherVia", cfg, w.issue, W.iso(frozen_now), {}, ctx=w.http(), full=True)
    f = reply["value"]
    assert f["deploys"] == [] and f["telemetry"] == {} and f["deploy_repo"] is None
    assert not [c for c in reply["calls"] if W.DEPLOY_REPO in c["url"]]
    assert not [p for p in f["problems"] if p.startswith("deploy")]
    md = js("gather", "toMarkdown", f)
    assert "## Deploy timeline" not in md and "## Telemetry pointers" not in md
    assert "not found in any deploy" not in md and "live since" not in md


def test_gather_follows_the_configured_labels_and_docs(js, frozen_now):
    w = W.rich_world(frozen_now)
    cfg = {**GATHER_CFG, "labels": LABELS, "docsDir": "handbook"}
    reply = js("gather", "gatherVia", cfg, w.issue, W.iso(frozen_now), {}, ctx=w.http(), full=True)
    bugs = next(c for c in reply["calls"] if c["url"].endswith("/graphql") and "issues(" in c["body"]["query"])
    assert bugs["body"]["variables"]["labels"] == [LABELS["bug"]]
    urls = [c["url"] for c in reply["calls"]]
    assert any(f"/repos/{W.REPO}/contents/handbook?" in u for u in urls)
    assert not any(f"/repos/{W.REPO}/contents/docs?" in u for u in urls)


def test_gather_drops_the_configured_own_labels(js, frozen_now):
    w = W.rich_world(frozen_now)
    w.ticket["labels"] = ["bug", "ai-investigating", "keep-me"]
    cfg = {**GATHER_CFG, "labels": LABELS}
    f = js("gather", "gatherVia", cfg, w.issue, W.iso(frozen_now), {}, ctx=w.http())
    assert f["ticket"]["labels"] == ["bug", "keep-me"]


def test_telemetry_notes_replace_the_pointers(js, frozen_now):
    w = W.rich_world(frozen_now)
    notes = ["Datadog service `checkout`, env `prod`.", "Logs: `service:checkout status:error`."]
    f = js("gather", "gatherVia", {**GATHER_CFG, "telemetryNotes": notes}, w.issue, W.iso(frozen_now), {}, ctx=w.http())
    assert f["telemetry"] == {"notes": notes}
    md = js("gather", "toMarkdown", f)
    assert "## Telemetry pointers\n\n- Datadog service `checkout`, env `prod`.\n- Logs: `service:checkout status:error`." in md


# ----------------------------------------------------------------------------------- verify

DATASOURCES = {"loki": "L1", "prometheus": "P1"}
TEL = "http://telemetry.test/mcp"


def query_findings():
    f = TV.full_findings()
    f["evidence"] = [TV.ev("E1", "logql", TV.LOGQL)]
    f["likely_cause"] = None
    f["docs_rule"] = None
    f["similar_past_bugs"] = []
    return f


def test_verify_uses_the_configured_telemetry(js, frozen_now):
    init = {"headers": {"content-type": "text/event-stream", "mcp-session-id": "s-1"},
            "body": 'data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18"}}\n\n'}
    routes = {f"POST {TEL}": [init, {"status": 202}, TV._mcp_reply(2, json.dumps([{"line": "a"}]))], f"DELETE {TEL}": {"status": 202}}
    opts = {"telemetryUrl": TEL, "grafanaUrl": "https://grafana.acme.test", "datasources": DATASOURCES}
    reply = js("verify", "verifyRealWith", TV.CFG, TV.FACTS, W.iso(frozen_now), opts, query_findings(), ctx=TV.http(routes), full=True)
    call = next(c for c in reply["calls"] if (c["body"] or {}).get("method") == "tools/call")
    assert call["body"]["params"]["arguments"]["datasourceUid"] == "L1"
    link = reply["value"]["evidence"][0]["verified"]["link"]
    assert link.startswith("https://grafana.acme.test/explore?") and "L1" in link and "loki" in link


def test_verify_without_telemetry_drops_queries(js, frozen_now):
    opts = {"telemetryUrl": None}
    reply = js("verify", "verifyRealWith", TV.CFG, TV.FACTS, W.iso(frozen_now), opts, query_findings(), ctx=TV.http({}), full=True)
    assert reply["calls"] == []
    assert reply["value"]["verification"]["dropped"][0]["reason"] == "Loki/Prometheus query failed: no telemetry server is configured"


def test_verify_without_a_deploy_repo_reads_only_the_app(js, frozen_now):
    cfg = {**TV.CFG, "deployRepo": None, "deployPath": None}
    facts = {**TV.FACTS, "deploy_head": None}
    reply = js("verify", "ctxCall", cfg, facts, W.iso(frozen_now), "commit", "deploy", "a" * 40, ctx=TV.http({}), full=True)
    assert reply["value"] is None and reply["calls"] == []
