# tests/n8n_bug_investigator/world.py
"""One fake Taskboard world, served two ways (§T.152).

kagent's trigger gathers facts with the core over `gh` and `git` clones; the n8n POC gathers
the same facts over the GitHub REST and GraphQL APIs. A World describes the repos once and
serves both: `runner()` answers the core's gh/git commands (with the docs written to real
directories, which the core walks), `routes()`/`graphql()` answer lib/gather.js through fakeHttp.
Bot logins follow gh's rules, checked against arigsela/taskboard on 2026-10-28: an issue or
PR author shows as `app/<slug>`, a commenter or reviewer as `<slug>`; REST says `<slug>[bot]`.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

API = "https://api.github.com"
REPO = "arigsela/taskboard"
DEPLOY_REPO = "arigsela/kubernetes"
DEPLOY_PATH = "base-apps/taskboard"
NAMESPACE = "taskboard"
CONFIGMAP = f"{DEPLOY_PATH}/code-configmap.yaml"


def sha(tag: str) -> str:
    return hashlib.sha1(tag.encode()).hexdigest()


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def user(login: str, bot: bool = False) -> dict:
    return {"login": login, "bot": bot}


def gh_top(u):     # gh's author of an issue or PR
    return {"login": f"app/{u['login']}" if u["bot"] else u["login"]}


def gh_inner(u):   # gh's author of a comment or review
    return {"login": u["login"]}


def rest(u):       # REST user
    return {"login": f"{u['login']}[bot]" if u["bot"] else u["login"], "type": "Bot" if u["bot"] else "User"}


def gql(u):        # GraphQL actor
    return {"__typename": "Bot" if u["bot"] else "User", "login": u["login"]}


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def enc(path: str) -> str:
    return "/".join(quote(seg, safe="") for seg in path.split("/"))


class World:
    def __init__(self, now: datetime, issue: int = 41):
        self.now = now
        self.issue = issue
        self.app_head = sha("app-head")
        self.deploy_head = sha("deploy-head")
        self.app_commits: list[tuple[str, datetime]] = []    # main, oldest first
        self.ticket: dict = {}
        self.prs: list[dict] = []          # gh pr list order (created, newest first)
        self.deploys: list[dict] = []      # newest first
        self.bugs: list[dict] = []         # gh issue list order (created, newest first)
        self.app_docs: dict[str, str] = {}
        self.deploy_docs: dict[str, str] = {}
        self.fail: set[str] = set()        # "past_bugs", "deploys"

    # ------------------------------------------------------------------------- building

    def commit(self, tag: str, when: datetime) -> str:
        s = sha(tag)
        self.app_commits.append((s, when))
        self.app_commits.sort(key=lambda c: c[1])
        return s

    def contains(self, source: str, commit: str) -> bool:
        """commit is an ancestor of (or is) source on the linear main history; source may be short."""
        order = [c for c, _ in self.app_commits]
        src = next((i for i, c in enumerate(order) if c.startswith(source)), None)
        com = next((i for i, c in enumerate(order) if c == commit), None)
        return src is not None and com is not None and com <= src

    # -------------------------------------------------------------- the core: gh and git

    def runner(self, CommandError):
        def run(cmd):
            cmd = list(cmd)
            if cmd[0] == "git":
                return self._git(cmd[3:], CommandError)
            if cmd[0] == "gh":
                return self._gh(cmd[1:], CommandError)
            raise CommandError(f"unexpected command {cmd[:3]}")
        return run

    def _git(self, args, CommandError):
        if args == ["rev-parse", "HEAD"]:
            return "?"  # replaced per directory by write_dirs' runner wrapper
        if args[0] == "log":
            if "deploys" in self.fail:
                raise CommandError("git log: exit 128")
            return "".join(f"{d['deploy_commit']}\x1f{d['committed_at_offset']}\x1f{d['subject']}\n" for d in self.deploys)
        if args[0] == "show":
            commit = args[1].split(":")[0]
            d = next(x for x in self.deploys if x["deploy_commit"] == commit)
            return d["blob"]
        if args[:2] == ["merge-base", "--is-ancestor"]:
            if self.contains(args[3], args[2]):
                return ""
            raise CommandError("merge-base: exit 1")
        raise CommandError(f"unexpected git {args[:2]}")

    def _gh(self, args, CommandError):
        if args[:2] == ["issue", "view"] and args[-1] == "number,title,body,author,createdAt,comments,labels,state,url":
            t = self.ticket
            return json.dumps({"number": t["number"], "title": t["title"], "body": t["body"], "author": gh_top(t["author"]),
                               "createdAt": t["created_at"], "state": "OPEN", "url": t["url"],
                               "labels": [{"name": x} for x in t["labels"]],
                               "comments": [{"author": gh_inner(c["author"]), "createdAt": c["created_at"], "body": c["body"]}
                                            for c in t["comments"]]})
        if args[:2] == ["pr", "list"]:
            return json.dumps([{"number": p["number"], "title": p["title"], "mergedAt": p["merged_at"], "author": gh_top(p["author"]),
                                "body": p["body"], "files": [{"path": f, "additions": 1, "deletions": 0} for f in p["files"]],
                                "url": p["url"], "mergeCommit": {"oid": p["merge_commit"]} if p["merge_commit"] else None,
                                "labels": [{"name": x} for x in p["labels"]]} for p in self.prs])
        if args[:2] == ["pr", "view"]:
            p = next(x for x in self.prs if x["number"] == int(args[2]))
            return json.dumps({"reviews": [{"author": gh_inner(r["author"]), "state": r["state"], "body": r["body"]} for r in p["reviews"]],
                               "comments": [{"author": gh_inner(c["author"]), "body": c["body"]} for c in p["comments"]]})
        if args[0] == "api" and "/pulls/" in args[1]:
            p = next(x for x in self.prs if x["number"] == int(args[1].split("/")[4]))
            return json.dumps([{"user": rest(c["author"]), "path": c["path"], "line": c["line"], "body": c["body"]}
                               for c in p["review_comments"]])
        if args[:2] == ["issue", "list"]:
            if "past_bugs" in self.fail:
                raise CommandError("gh issue list: exit 1: HTTP 502")
            return json.dumps([{"number": b["number"], "title": b["title"], "body": b["body"], "closedAt": b["closed_at"],
                                "labels": [{"name": x} for x in b["labels"]], "url": b["url"]} for b in self.bugs])
        if args[:2] == ["issue", "view"] and args[-1] == "closedByPullRequestsReferences":
            b = next(x for x in self.bugs if x["number"] == int(args[2]))
            return json.dumps({"closedByPullRequestsReferences": b["fixed_by"]})
        raise CommandError(f"unexpected gh {args[:3]}")

    def write_dirs(self, root: Path, core_run):
        """The app and deploy checkouts the core reads docs from, and a runner that knows each HEAD."""
        app, deploy = root / "app", root / "deploy"
        for base, files in ((app, self.app_docs), (deploy, self.deploy_docs)):
            base.mkdir(parents=True, exist_ok=True)
            for path, text in files.items():
                (base / path).parent.mkdir(parents=True, exist_ok=True)
                (base / path).write_text(text)
        (deploy / DEPLOY_PATH).mkdir(parents=True, exist_ok=True)

        def run(cmd):
            if list(cmd[3:]) == ["rev-parse", "HEAD"]:
                return (self.app_head if cmd[2] == str(app) else self.deploy_head) + "\n"
            return core_run(cmd)
        return str(app), str(deploy), run

    # ------------------------------------------------------------------- the POC: REST

    def since(self, days: int = 14) -> datetime:
        return self.now - timedelta(days=days)

    def base(self, days: int = 14):
        until = self.since(days) - timedelta(days=1)
        older = [c for c, when in self.app_commits if when <= until]
        return (older[-1] if older else None), iso(until)

    def routes(self, days: int = 14) -> dict:
        R, D = f"{API}/repos/{REPO}", f"{API}/repos/{DEPLOY_REPO}"
        t = self.ticket
        routes = {
            f"GET {R}/commits/main": {"body": {"sha": self.app_head}},
            f"GET {D}/commits/main": {"body": {"sha": self.deploy_head}},
            f"GET {R}/issues/{t['number']}": {"body": {
                "number": t["number"], "title": t["title"], "body": t["body"], "user": rest(t["author"]),
                "created_at": t["created_at"], "state": "open", "html_url": t["url"], "labels": [{"name": x} for x in t["labels"]]}},
            f"GET {R}/issues/{t['number']}/comments?per_page=100": {"body": [
                {"user": rest(c["author"]), "created_at": c["created_at"], "body": c["body"]} for c in t["comments"]]},
        }
        listing = f"GET {D}/commits?path={quote(CONFIGMAP, safe='')}&sha=main&per_page=100"
        routes[listing] = ({"status": 500, "body": {"message": "Server Error"}} if "deploys" in self.fail else {"body": [
            {"sha": d["deploy_commit"], "commit": {"committer": {"date": d["committed_at"]}, "message": d["message"]}}
            for d in self.deploys]})
        for d in self.deploys:
            routes[f"GET {D}/contents/{enc(CONFIGMAP)}?ref={d['deploy_commit']}"] = {"body": {
                "type": "file", "encoding": "base64", "content": b64(d["blob"])}}
        base, until = self.base(days)
        routes[f"GET {R}/commits?sha=main&until={quote(until, safe='')}&per_page=1"] = {"body": [{"sha": base}] if base else []}
        order = [c for c, _ in self.app_commits]
        for d in self.deploys:
            src = d.get("source_commit")
            if not src or not base:
                continue
            i = next(k for k, c in enumerate(order) if c.startswith(src))
            j = order.index(base)
            routes[f"GET {R}/compare/{base}...{src}?per_page=100"] = {"body": {
                "status": "ahead", "commits": [{"sha": c} for c in order[j + 1:i + 1]]}}
        for p in self.prs:
            routes[f"GET {R}/pulls/{p['number']}/comments?per_page=100"] = {"body": [
                {"user": rest(c["author"]), "path": c["path"], "line": c["line"], "body": c["body"]} for c in p["review_comments"]]}
        for repo, head, files, top in ((REPO, self.app_head, self.app_docs, "docs"), (DEPLOY_REPO, self.deploy_head, self.deploy_docs, DEPLOY_PATH)):
            self._content_routes(routes, repo, head, files, top)
        return routes

    def _content_routes(self, routes, repo, head, files, top):
        base = f"{API}/repos/{repo}/contents"
        dirs: dict[str, list] = {}
        for path in files:
            if not path.startswith(top + "/"):
                continue
            parts = path.split("/")
            for k in range(len(top.split("/")), len(parts)):
                parent, name = "/".join(parts[:k]), parts[k]
                kind = "file" if k == len(parts) - 1 else "dir"
                entry = {"type": kind, "name": name, "path": "/".join(parts[:k + 1])}
                if entry not in dirs.setdefault(parent, []):
                    dirs[parent].append(entry)
            routes[f"GET {base}/{enc(path)}?ref={head}"] = {"body": {"type": "file", "encoding": "base64", "content": b64(files[path])}}
        if not dirs:
            routes[f"GET {base}/{enc(top)}?ref={head}"] = {"status": 404, "body": {"message": "Not Found"}}
        for parent, entries in dirs.items():
            routes[f"GET {base}/{enc(parent)}?ref={head}"] = {"body": entries}

    def graphql(self) -> list:
        prs = {"data": {"repository": {"pullRequests": {"nodes": [{
            "number": p["number"], "title": p["title"], "mergedAt": p["merged_at"], "url": p["url"], "body": p["body"],
            "author": gql(p["author"]), "files": {"nodes": [{"path": f} for f in p["files"]]},
            "mergeCommit": {"oid": p["merge_commit"]} if p["merge_commit"] else None,
            "labels": {"nodes": [{"name": x} for x in p["labels"]]},
            "reviews": {"nodes": [{"author": gql(r["author"]), "state": r["state"], "body": r["body"]} for r in p["reviews"]]},
            "comments": {"nodes": [{"author": gql(c["author"]), "body": c["body"]} for c in p["comments"]]},
        } for p in self.prs]}}}}
        bugs = ({"data": None, "errors": [{"message": "Something went wrong"}]} if "past_bugs" in self.fail else
                {"data": {"repository": {"issues": {"nodes": [{
                    "number": b["number"], "title": b["title"], "body": b["body"], "closedAt": b["closed_at"], "url": b["url"],
                    "labels": {"nodes": [{"name": x} for x in b["labels"]]},
                    "closedByPullRequestsReferences": {"nodes": b["fixed_by"]},
                } for b in self.bugs]}}}})
        return [{"match": "pullRequests(", "body": prs}, {"match": "issues(", "body": bugs}]

    def http(self, days: int = 14) -> dict:
        return {"fake": "fakeHttp", "spec": {"routes": self.routes(days), "graphql": self.graphql()}}


# ------------------------------------------------------------------------------- worlds

ARI = user("arigsela")
BOT = user("arigsela-implementer", bot=True)
KAGENT = user("arigsela-bug-investigator", bot=True)


def rich_world(now: datetime) -> World:
    """A ticket with every kind of comment, five PRs (two filtered out), four deploys, bugs, docs."""
    w = World(now)
    d = lambda days: now - timedelta(days=days)  # noqa: E731
    for k in range(30):
        w.commit(f"filler-{k}", d(40 - k))
    c46 = w.commit("pr-46", d(10))
    c47 = w.commit("pr-47", d(20.5))
    c48 = w.commit("pr-48", d(5))
    c49 = w.commit("pr-49", d(3))
    c50 = w.commit("pr-50", d(1))
    w.ticket = {
        "number": 41, "title": "Tasks due before a date come back empty", "url": f"https://github.com/{REPO}/issues/41",
        "author": BOT, "created_at": iso(d(0.5)),
        "labels": ["bug", "from-alert", "investigated", "fix-proposed", "n8n-investigating"],
        "body": "GET /tasks?due_before=2026-10-30 returns [] since this morning.\n\n```\n500s on /tasks\n```",
        "comments": [
            {"author": ARI, "created_at": iso(d(0.4)), "body": "Seen it too | from the UI."},
            {"author": BOT, "created_at": iso(d(0.3)), "body": "Alert repeated at 10:42."},
            {"author": KAGENT, "created_at": iso(d(0.2)), "body": "<!-- bug-investigator mode=kagent -->\n## 🔎 Bug investigation — kagent"},
            {"author": KAGENT, "created_at": iso(d(0.1)), "body": "<!-- bug-investigator fix -->\nOpened #42."},
        ],
    }
    pr = lambda n, author, merged, commit, labels=(), **kw: {  # noqa: E731
        "number": n, "title": f"PR {n} title", "url": f"https://github.com/{REPO}/pull/{n}", "author": author,
        "merged_at": iso(merged), "merge_commit": commit, "labels": list(labels), "body": f"Body of PR {n}.",
        "files": kw.get("files", [f"server/routes/tasks{n}.js"]), "reviews": kw.get("reviews", []),
        "comments": kw.get("comments", []), "review_comments": kw.get("review_comments", [])}
    w.prs = [
        pr(50, BOT, d(1), c50, files=[f"server/f{k}.js" for k in range(30)],
           reviews=[{"author": ARI, "state": "APPROVED", "body": "LGTM,\nship it"}, {"author": ARI, "state": "COMMENTED", "body": ""}],
           comments=[{"author": BOT, "body": "Fixes #40"}],
           review_comments=[{"author": BOT, "path": "server/f1.js", "line": 12, "body": "renamed due_at here"},
                            {"author": ARI, "path": "server/f2.js", "line": None, "body": "outdated"}]),
        pr(49, ARI, d(3), c49, comments=[{"author": ARI, "body": "x" * 400}]),
        pr(48, ARI, d(5), c48, labels=["rehearsal"]),
        pr(47, ARI, d(20.5), c47),
        pr(46, ARI, d(10), c46),
    ]
    cm = lambda src: (f"apiVersion: v1\nkind: ConfigMap\nmetadata:\n  annotations:\n    taskboard.arigsela.com/source-commit: {src}\n"  # noqa: E731
                      if src else "apiVersion: v1\nkind: ConfigMap\n")
    dep = lambda tag, when, src, subject: {  # noqa: E731
        "deploy_commit": sha(tag), "committed_at": iso(when), "committed_at_offset": (when - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S-04:00"),
        "subject": subject, "message": subject + "\n\nbody line", "source_commit": src, "blob": cm(src)}
    w.deploys = [
        dep("deploy-4", d(1.5), c49[:7], "deploy(taskboard): d7b37f2 | short sha"),
        dep("deploy-3", d(2.5), c49, "deploy(taskboard): " + "y" * 100),
        dep("deploy-2", d(9), c46, "deploy(taskboard): 46"),
        dep("deploy-1", d(12), None, "deploy(taskboard): before annotations"),
    ]
    w.bugs = [
        {"number": 39, "title": "Archive button 500s", "body": "Clicking archive\nreturns 500.", "closed_at": iso(d(6)),
         "url": f"https://github.com/{REPO}/issues/39", "labels": ["bug"],
         "fixed_by": [{"number": 38, "title": "fix archive", "url": f"https://github.com/{REPO}/pull/38"}]},
        {"number": 37, "title": "Rehearsal: due date", "body": "r", "closed_at": iso(d(7)), "url": f"https://github.com/{REPO}/issues/37",
         "labels": ["bug", "rehearsal"], "fixed_by": []},
        {"number": 30, "title": "No fix linked", "body": "b" * 300, "closed_at": iso(d(8)), "url": f"https://github.com/{REPO}/issues/30",
         "labels": ["bug"], "fixed_by": []},
    ]
    w.app_docs = {
        "docs/rules.md": "# Task rules\n\n- **P4** Due dates are stored in `due_at`.\n| C3 | Archive keeps comments |\n### S1: Sessions expire\nnot a rule P4x\n",
        "docs/z-last.md": "no title here\n`P10` backticked rule\n",
        "docs/api/routes.md": "# Routes\r\n- P7: GET /tasks filters\r\n",
        "docs/node_modules/skip.md": "# skipped\n- P1 x\n",
        "docs/notes.txt": "- P2 not markdown\n",
        "README.md": "# not under docs\n",
    }
    w.deploy_docs = {
        f"{DEPLOY_PATH}/docs.md": "---\ntitle: x\n---\n# Taskboard\n- **D1** deploys go through Argo CD\n",
        f"{DEPLOY_PATH}/docs/index.md": "# Index\n",
        f"{DEPLOY_PATH}/deployment.yaml": "kind: Deployment\n",
    }
    return w


def sparse_world(now: datetime) -> World:
    """A ticket alone: no deploys, no PRs, no docs, and the past-bugs read fails."""
    w = World(now)
    w.commit("only", now - timedelta(days=40))
    w.ticket = {"number": 41, "title": "Something broke", "url": f"https://github.com/{REPO}/issues/41", "author": ARI,
                "created_at": iso(now - timedelta(hours=2)), "labels": ["bug"], "body": "", "comments": []}
    w.fail.add("past_bugs")
    return w


def budget_world(now: datetime, prs: int = 21, deploys: int = 6) -> World:
    """Today's volume on arigsela/taskboard (2026-10-28): 21 merged PRs in 14 days, 6 deploys."""
    w = rich_world(now)
    w.prs, w.deploys = [], []
    for k in range(prs):
        c = w.commit(f"budget-pr-{k}", now - timedelta(days=13 - k * 0.6))
        w.prs.append({"number": 100 + k, "title": f"PR {100 + k}", "url": f"https://github.com/{REPO}/pull/{100 + k}", "author": ARI,
                      "merged_at": iso(now - timedelta(days=13 - k * 0.6)), "merge_commit": c, "labels": [], "body": "b",
                      "files": ["f.js"], "reviews": [], "comments": [], "review_comments": []})
    order = [c for c, _ in w.app_commits]
    for k in range(deploys):
        src = order[-1 - k * 3]
        when = now - timedelta(days=k * 2 + 0.1)
        w.deploys.append({"deploy_commit": sha(f"budget-deploy-{k}"), "committed_at": iso(when),
                          "committed_at_offset": when.strftime("%Y-%m-%dT%H:%M:%S+00:00"), "subject": f"deploy {k}",
                          "message": f"deploy {k}", "source_commit": src,
                          "blob": f"metadata:\n  annotations:\n    taskboard.arigsela.com/source-commit: {src}\n"})
    w.prs.reverse()  # gh order: newest first
    return w
