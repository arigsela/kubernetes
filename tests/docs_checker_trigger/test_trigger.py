# tests/docs_checker_trigger/test_trigger.py
"""scripts/docs_checker_trigger.py: memory in, A2A data part out, memory saved optimistically,
the Slack digest posted at most once per run id, a visible alert on every failure (§T.132, V102).
No network, no cluster: the API server, the agent and n8n are fakes."""
import copy
import json

import pytest

from conftest import load_script

tr = load_script("docs_checker_trigger", "scripts/docs_checker_trigger.py")
a2a = tr.a2a

CM_PATH = "/api/v1/namespaces/kagent/configmaps/docs-checker-memory"
SNAPSHOT_IN = {"version": 1, "run": 3, "docs": {"base-apps/vault/docs.md": {"hash": "aa", "run": 3}},
               "decisions": {}}
SNAPSHOT_OUT = {"version": 1, "run": 4, "docs": {"base-apps/vault/docs.md": {"hash": "bb", "run": 4}},
                "decisions": {"k1": {"last_run": 4}}}
SLACK = "Weekly docs check at 0123456789ab: 1 findings, 0 need a look, 0 still open\nSpend: $0.41"
REPORT_MD = "# Weekly docs check (run `task-1`)\n\nCommit `0123456789ab`. 57 docs; 1 findings.\n"
ENV = {"RUN_ID": "docs-checker-trigger-29345678", "KAGENT_A2A_BASE": "http://a2a:8083",
       "KAGENT_TOKEN_FILE": "/var/run/kagent/token", "N8N_WEBHOOK_BASE": "http://n8n:5678/webhook",
       "RUN_TIMEOUT": "1200"}
WEBHOOK = "http://n8n:5678/webhook/agent-findings"


def encoded(snapshot) -> str:
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))


# ------------------------------------------------------------------ fakes

class FakeKube:
    """One ConfigMap with the API server's optimistic concurrency: a PUT must carry the current
    resourceVersion (else 409), and a successful PUT bumps it. `before_put` runs before each
    PUT is judged: a concurrent writer."""

    def __init__(self, data=None, rv=41, before_put=None, get_error=None):
        self.obj = {"apiVersion": "v1", "kind": "ConfigMap",
                    "metadata": {"name": "docs-checker-memory", "namespace": "kagent",
                                 "resourceVersion": str(rv), "labels": {"app": "docs-checker-trigger"},
                                 "annotations": {"argocd.argoproj.io/tracking-id": "kagent-secrets:/ConfigMap:kagent/docs-checker-memory"}}}
        if data is not None:
            self.obj["data"] = dict(data)
        self.puts, self.gets, self.events = [], 0, []
        self.before_put = before_put
        self.get_error = get_error

    def concurrent_write(self, **data):
        """Another writer saved the ConfigMap: new data keys, new resourceVersion."""
        self.obj.setdefault("data", {}).update(data)
        self._bump()

    def _bump(self):
        self.obj["metadata"]["resourceVersion"] = str(int(self.obj["metadata"]["resourceVersion"]) + 1)

    def get(self, path):
        assert path == CM_PATH
        if self.get_error:
            raise tr.KubeError(self.get_error, path)
        self.gets += 1
        self.events.append("get")
        return copy.deepcopy(self.obj)

    def put(self, path, obj):
        assert path == CM_PATH
        self.puts.append(copy.deepcopy(obj))
        self.events.append("put")
        if self.before_put:
            self.before_put(self, len(self.puts))
        if obj["metadata"].get("resourceVersion") != self.obj["metadata"]["resourceVersion"]:
            raise tr.KubeError(409, path)
        self.obj = copy.deepcopy(obj)
        self._bump()
        return copy.deepcopy(self.obj)

    @property
    def data(self):
        return self.obj.get("data") or {}

    def deliveries(self):
        return json.loads(self.data.get("deliveries.json") or "{}")


def task(state="TASK_STATE_COMPLETED", *, memory=SNAPSHOT_OUT, drop=(), status_text=None, artifacts=True,
         task_id="task-1", session="sess-1"):
    """A task as the controller's JSON-RPC gateway returns it, shaped like docs-checker's
    completed_parts (executor.py): the report as text, then the data parts."""
    parts = [{"text": REPORT_MD},
             {"data": {"docs-checker/findings": [{"doc": "base-apps/vault/docs.md", "line_start": 12}]}},
             {"data": {"docs-checker/memory": memory}},
             {"data": {"docs-checker/slack": SLACK}},
             {"data": {"docs-checker/report": {"run_id": task_id, "counts": {"findings": 1}}}},
             {"data": {"name": "get_file_contents", "args": {"path": "base-apps/vault/docs.md"}},
              "metadata": {"kagent.dev/a2a/part-type": "function_call"}},
             {"data": {"docs-checker/cost-run": {"cash_usd": 0.41}}}]
    parts = [p for p in parts if not (set(p.get("data") or {}) & set(drop))]
    body = {"id": task_id, "contextId": session, "status": {"state": state}}
    if status_text:
        body["status"]["message"] = {"parts": [{"text": status_text}]}
    if artifacts and state == "TASK_STATE_COMPLETED":
        body["artifacts"] = [{"artifactId": "a1", "name": "report", "parts": parts}]
    return {"task": body}


class FakeAgent:
    def __init__(self, reply=None, error=None, later=None):
        self.reply, self.error, self.later = reply if reply is not None else task(), error, later
        self.sent, self.waited = [], []

    def send(self, endpoint, payload, *, token_file, timeout):
        self.sent.append({"endpoint": endpoint, "payload": payload, "token_file": token_file, "timeout": timeout})
        if self.error:
            raise self.error
        return self.reply

    def wait(self, endpoint, task_id, *, token_file, timeout):
        self.waited.append({"task_id": task_id, "timeout": timeout})
        return self.later if self.later is not None else self.reply


class FakePost:
    def __init__(self, error=None, kube=None):
        self.error, self.kube = error, kube
        self.posted = []

    def __call__(self, url, obj, timeout=30.0):
        if self.kube is not None:
            self.kube.events.append("post")
        self.posted.append((url, copy.deepcopy(obj)))
        if self.error:
            raise self.error


def trigger(kube, agent=None, post=None, env=None, clock=None):
    agent = agent or FakeAgent()
    post = post if post is not None else FakePost(kube=kube)
    logs = []
    rc = tr.run(env or ENV, kube=kube, send=agent.send, wait=agent.wait, post=post, log=logs.append,
                now=lambda: "2026-10-19T14:00:05+00:00", clock=clock or (lambda: 0.0))
    return rc, agent, post, logs


# ------------------------------------------------------------------ the request

def test_request_is_one_scheduled_all_data_part_carrying_the_stored_memory():
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN)})
    rc, agent, _, _ = trigger(kube)
    assert rc == 0
    sent = agent.sent[0]
    assert sent["endpoint"] == "http://a2a:8083/agents/kagent/docs-checker"
    assert sent["token_file"] == "/var/run/kagent/token", "its own identity: the projected token (V91, V92)"
    msg = sent["payload"]["params"]["message"]
    assert sent["payload"]["method"] == "SendMessage"
    # ONLY the data part: a text part would let a dropped data part run as an interactive check
    # that returns an empty memory snapshot; without one it is refused and alerts instead.
    assert msg["parts"] == [{"data": {"scope": "all", "mode": "scheduled", "memory": SNAPSHOT_IN}}]
    assert msg["role"] == "ROLE_USER" and not msg.get("contextId") and not msg.get("taskId")
    assert sent["payload"]["params"]["configuration"] == {"blocking": True}


def test_an_empty_configmap_sends_null_memory_and_the_agent_starts_fresh():
    kube = FakeKube(data=None)                     # what git creates: no data at all
    rc, agent, _, _ = trigger(kube)
    assert rc == 0
    assert agent.sent[0]["payload"]["params"]["message"]["parts"][0]["data"]["memory"] is None
    assert kube.data["memory.json"] == encoded(SNAPSHOT_OUT)


def test_garbled_memory_is_sent_as_null_and_replaced_by_the_returned_snapshot():
    kube = FakeKube({"memory.json": "{not json"})
    rc, agent, _, logs = trigger(kube)
    assert rc == 0
    assert agent.sent[0]["payload"]["params"]["message"]["parts"][0]["data"]["memory"] is None
    assert kube.data["memory.json"] == encoded(SNAPSHOT_OUT)
    assert any("memory.json" in str(x.get("warning")) for x in logs)


def test_send_request_presents_the_token_never_a_user_id_and_resends_only_a_proven_rejection(tmp_path):
    token = tmp_path / "token"
    token.write_text("projected-token\n")
    calls = []
    rejected = {"jsonrpc": "2.0", "id": "1", "error": {
        "code": a2a.CODE_UNSUPPORTED_OPERATION, "message": "busy",
        "data": [{"metadata": {"reason": a2a.REASON_SEND_NOT_ACCEPTED, "retryAfterMs": 100}}]}}
    answers = [rejected, {"jsonrpc": "2.0", "id": "1", "result": task()}]

    def post(url, payload, headers, timeout):
        calls.append((url, payload, headers, timeout))
        return answers.pop(0)

    payload = tr.build_request(SNAPSHOT_IN)
    result = tr.send_request("http://a2a/agents/kagent/docs-checker", payload, token_file=str(token),
                             timeout=1200, transport=post, sleep=lambda s: None, clock=lambda: 0.0)
    assert result == task()
    assert len(calls) == 2 and calls[0][1] == calls[1][1], "the resend is byte-identical (same messageId)"
    headers = calls[0][2]
    assert headers["Authorization"] == "Bearer projected-token" and "X-User-Id" not in headers
    assert calls[0][3] == 1200
    with pytest.raises(a2a.A2AError):
        tr.send_request("http://a2a/x", payload, token_file=str(token), timeout=5, sleep=lambda s: None,
                        transport=lambda *a: {"error": {"code": -32603, "message": "internal"}}, clock=lambda: 0.0)


def test_a_task_handed_back_before_it_settled_is_polled_until_the_deadline():
    working = task("TASK_STATE_WORKING", artifacts=False)
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN)})
    times = iter([0.0, 30.0])
    rc, agent, post, _ = trigger(kube, FakeAgent(reply=working, later=task()), clock=lambda: next(times))
    assert rc == 0
    assert agent.waited == [{"task_id": "task-1", "timeout": 1170.0}]
    assert kube.data["memory.json"] == encoded(SNAPSHOT_OUT) and len(post.posted) == 1


# ------------------------------------------------------------------ success

def test_success_saves_memory_posts_the_digest_and_logs_the_full_report():
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN)})
    rc, _, post, logs = trigger(kube)
    assert rc == 0
    url, payload = post.posted[0]
    assert url == WEBHOOK
    assert payload["alertname"] == "Weekly docs check" and payload["agent"] == "docs-checker"
    assert payload["text"] == SLACK and payload["error"] is None
    assert payload["session"] == "sess-1" and payload["state"] == "TASK_STATE_COMPLETED"
    assert kube.data["memory.json"] == encoded(SNAPSHOT_OUT)
    assert kube.deliveries()[ENV["RUN_ID"]] == {"kind": "report", "posted": True, "task": "task-1",
                                                "at": "2026-10-19T14:00:05+00:00"}
    report = [x for x in logs if x.get("report")]
    assert report and report[0]["report"] == REPORT_MD, "the digest says: full report in the job log"
    assert all(x["event"] == "docs-checker-trigger" and x["run"] == ENV["RUN_ID"] for x in logs)


def test_memory_is_written_back_even_when_the_run_found_nothing():
    """The run counter changes every run (C#21): always written, findings or not."""
    out = {**SNAPSHOT_OUT, "run": 9}
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN)})
    rc, _, _, _ = trigger(kube, FakeAgent(reply=task(memory=out)))
    assert rc == 0 and kube.data["memory.json"] == encoded(out)


# ------------------------------------------------------------------ optimistic memory writes (C#47)

def test_memory_update_uses_resource_version():
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN), "other": "kept"}, rv=41)
    rc, _, _, _ = trigger(kube)
    assert rc == 0
    claim, mark = kube.puts
    # Every write is a read-modify-write of the object AS READ, against the version it saw.
    assert claim["metadata"]["resourceVersion"] == "41"
    assert mark["metadata"]["resourceVersion"] == "42", "the second write is against the first's result"
    for put in kube.puts:
        assert put["metadata"]["annotations"] == kube.obj["metadata"]["annotations"], "Argo's tracking id kept"
        assert put["metadata"]["labels"] == {"app": "docs-checker-trigger"}
        assert put["data"]["other"] == "kept"
    assert claim["data"]["memory.json"] == encoded(SNAPSHOT_OUT)
    assert mark["data"]["memory.json"] == encoded(SNAPSHOT_OUT), "marking posted leaves memory alone"


def test_a_conflict_is_re_read_and_retried_on_the_new_version_never_blind():
    def argo_touched_metadata(k, n):
        if n == 1:
            k.obj["metadata"]["labels"]["touched"] = "yes"
            k._bump()                                       # 41 -> 42 under the trigger
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN)}, rv=41, before_put=argo_touched_metadata)
    rc, _, post, _ = trigger(kube)
    assert rc == 0
    assert [p["metadata"]["resourceVersion"] for p in kube.puts] == ["41", "42", "43"]
    assert kube.puts[1]["metadata"]["labels"]["touched"] == "yes", "rebuilt on the re-read object"
    assert kube.data["memory.json"] == encoded(SNAPSHOT_OUT), "memory was unchanged under us: ours is saved"
    assert len(post.posted) == 1


def test_a_conflict_with_a_snapshot_another_run_saved_keeps_theirs():
    theirs = encoded({**SNAPSHOT_OUT, "run": 4, "docs": {}})

    def other_run_saved(k, n):
        if n == 1:
            k.concurrent_write(**{"memory.json": theirs})
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN)}, before_put=other_run_saved)
    rc, _, post, logs = trigger(kube)
    assert rc == 0
    assert kube.data["memory.json"] == theirs, "never overwrite a snapshot this run did not read"
    assert kube.deliveries()[ENV["RUN_ID"]]["posted"] is True and len(post.posted) == 1
    assert any("another run" in str(x.get("warning")) for x in logs)


def test_conflicts_are_bounded_and_end_in_a_visible_alert_not_a_report():
    def always_conflict(k, n):
        k._bump()
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN)}, before_put=always_conflict)
    rc, _, post, _ = trigger(kube)
    assert rc == 1
    assert len(kube.puts) == tr.WRITE_ATTEMPTS, "bounded"
    assert all(p["metadata"].get("resourceVersion") for p in kube.puts), "never without a version"
    (url, payload), = post.posted
    assert payload["error"] and "memory" in payload["error"] and payload["text"] is None


def test_the_tool_never_writes_without_a_resource_version():
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN)})
    del kube.obj["metadata"]["resourceVersion"]
    store = tr.Store(kube.get(CM_PATH))
    with pytest.raises(tr.KubeError):
        tr.save(kube, CM_PATH, store, "r1", {"kind": "report", "posted": False}, memory=SNAPSHOT_OUT,
                claim=True, log=lambda **kw: None)
    assert kube.puts == []


# ------------------------------------------------------------------ failures (C#47)

FAILURES = {
    "failed": dict(reply=task("TASK_STATE_FAILED", status_text="docs-checker failed: not a branch name or commit")),
    "agent-timeout": dict(reply=task("TASK_STATE_FAILED", status_text="docs-checker failed: timeout")),
    "canceled": dict(reply=task("TASK_STATE_CANCELED")),
    "paused": dict(reply=task("TASK_STATE_INPUT_REQUIRED", status_text="Approval is required")),
    "never-settled": dict(reply=task("TASK_STATE_WORKING", artifacts=False),
                          later=task("TASK_STATE_WORKING", artifacts=False)),
    "no-artifact": dict(reply=task(artifacts=False)),
    "refused-no-report": dict(reply=task(drop=("docs-checker/slack", "docs-checker/report"))),
    "no-memory-part": dict(reply=task(drop=("docs-checker/memory",))),
    "a2a-error": dict(error=a2a.A2AError("HTTP 403 from http://a2a: RBAC: access denied")),
    "client-timeout": dict(error=TimeoutError("timed out")),
    "connection": dict(error=OSError("connection refused")),
}


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_failure_posts_a_visible_alert(case):
    stored = {"memory.json": encoded(SNAPSHOT_IN)}
    kube = FakeKube(stored)
    rc, _, post, logs = trigger(kube, FakeAgent(**FAILURES[case]))
    assert rc == 1
    (url, payload), = post.posted
    assert url == WEBHOOK
    # Visible: n8n renders a non-empty `error` as ":x: ... Failed: <error>". Never an empty report.
    assert payload["error"] and payload["text"] is None
    assert "Memory not updated" in payload["error"] and ENV["RUN_ID"] in payload["error"]
    assert payload["alertname"] == "Weekly docs check"
    # Memory is NOT touched: byte-identical, whatever the agent returned.
    assert kube.data["memory.json"] == stored["memory.json"]
    assert all(p["data"]["memory.json"] == stored["memory.json"] for p in kube.puts)
    assert kube.deliveries()[ENV["RUN_ID"]]["kind"] == "alert"
    assert any(x.get("posted") == "alert" for x in logs)


def test_an_unreadable_memory_configmap_alerts_before_any_agent_call():
    kube = FakeKube(get_error=403)
    rc, agent, post, _ = trigger(kube)
    assert rc == 1 and agent.sent == []
    (_, payload), = post.posted
    assert "docs-checker-memory" in payload["error"] and payload["text"] is None


def test_a_missing_run_id_alerts_and_runs_nothing():
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN)})
    rc, agent, post, _ = trigger(kube, env={**ENV, "RUN_ID": ""})
    assert rc == 1 and agent.sent == [] and kube.puts == []
    (_, payload), = post.posted
    assert "RUN_ID" in payload["error"]


# ------------------------------------------------------------------ at most once per run id

def test_delivery_is_idempotent_per_run_id():
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN)})
    rc1, _, post1, _ = trigger(kube)
    assert rc1 == 0 and len(post1.posted) == 1
    # A retried pod of the SAME Job (same RUN_ID): nothing is sent, saved or posted again.
    puts = len(kube.puts)
    rc2, agent2, post2, logs2 = trigger(kube)
    assert rc2 == 0 and agent2.sent == [] and post2.posted == [] and len(kube.puts) == puts
    assert any(x.get("skipped") == "already delivered" for x in logs2)
    # The next week's Job is another run id: it runs and posts.
    rc3, agent3, post3, _ = trigger(kube, env={**ENV, "RUN_ID": "docs-checker-trigger-29355758"})
    assert rc3 == 0 and len(agent3.sent) == 1 and len(post3.posted) == 1


def test_the_claim_is_saved_before_the_post_so_a_failed_post_is_never_repeated():
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN)})
    rc1, _, post1, logs1 = trigger(kube, post=FakePost(error=OSError("n8n down"), kube=kube))
    assert rc1 == 1
    assert kube.events.index("put") < kube.events.index("post"), "claim (with memory) first, then post"
    assert kube.data["memory.json"] == encoded(SNAPSHOT_OUT), "the run completed: its memory is kept"
    assert kube.deliveries()[ENV["RUN_ID"]] == {"kind": "report", "posted": False, "task": "task-1",
                                                "at": "2026-10-19T14:00:05+00:00"}
    assert any(x.get("slack") == SLACK for x in logs1), "the digest stays in the job log"
    # The Job's retry pod: claimed but unconfirmed -> at most once: no second post, no second run.
    rc2, agent2, post2, logs2 = trigger(kube)
    assert rc2 == 1 and agent2.sent == [] and post2.posted == []
    assert any("not posting again" in str(x.get("error")) for x in logs2)


def test_a_failure_alert_is_posted_once_per_run_id_too():
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN)})
    failing = dict(reply=task("TASK_STATE_FAILED", status_text="docs-checker failed: timeout"))
    rc1, _, post1, _ = trigger(kube, FakeAgent(**failing))
    rc2, agent2, post2, _ = trigger(kube, FakeAgent(**failing))
    assert (rc1, rc2) == (1, 1)
    assert len(post1.posted) == 1 and post2.posted == [] and agent2.sent == [], "no second paid run, no second alert"
    assert kube.data["memory.json"] == encoded(SNAPSHOT_IN)


def test_a_claim_lost_to_another_pod_of_the_same_run_posts_nothing():
    def other_pod_claimed(k, n):
        if n == 1:
            k.concurrent_write(**{"deliveries.json": json.dumps(
                {ENV["RUN_ID"]: {"kind": "report", "posted": True, "task": "task-0", "at": "x"}})})
    kube = FakeKube({"memory.json": encoded(SNAPSHOT_IN)}, before_put=other_pod_claimed)
    rc, _, post, _ = trigger(kube)
    assert rc == 0 and post.posted == [] and len(kube.puts) == 1


def test_the_delivery_ledger_keeps_only_the_newest_runs():
    ledger = {f"run-{i:02d}": {"kind": "report", "posted": True, "at": f"2026-01-{i:02d}T00:00:00+00:00"}
              for i in range(1, 15)}
    kept = json.loads(tr.encode_ledger(ledger))
    assert sorted(kept) == [f"run-{i:02d}" for i in range(15 - tr.KEEP_DELIVERIES, 15)]


# ------------------------------------------------------------------ the Slack payload

def _dispatch_finding_keys() -> set:
    """The keys dispatch.py's _post_finding posts for an investigation, read by RUNNING the
    dispatcher (fake agent, fake post): a key added or renamed there fails the test below."""
    dp = load_script("dispatch", "scripts/dispatch.py")
    table = dp.load_table(json.dumps({"version": 1, "limits": {"investigations_per_hour": 5, "repeat_after_hours": 6},
                                      "rules": [{"id": "cpu", "source": "grafana", "match": {"alertname": "X"},
                                                 "route": "agent:k8s-reader", "prompt": "look", "why": "test"}]}))
    cfg = dp.Config(table_path="/dev/null", a2a_base="http://a2a:8083", n8n_base="http://n8n/webhook",
                    user="dispatcher", timeout=5.0)
    posted = []
    reply = {"task": {"id": "t", "contextId": "c", "status": {"state": a2a.STATE_COMPLETED},
                      "artifacts": [{"parts": [{"text": "answer"}]}]}}
    d = dp.Dispatcher(table, cfg, send=lambda *a, **k: reply, post=lambda url, obj, timeout=10.0: posted.append((url, obj)),
                      log=lambda o: None, run_async=False)
    d.investigate({"labels": {"alertname": "X", "namespace": "n", "pod": "p"}, "annotations": {}}, table["rules"][0])
    (url, finding), = posted
    assert url.endswith("/agent-findings"), "the same webhook the trigger posts to"
    return set(finding)


def test_slack_payload_matches_dispatch_post_finding_keys():
    keys = _dispatch_finding_keys()
    assert {"alertname", "labels", "agent", "text", "error", "ui"} <= keys, "what n8n's Format Finding node reads"
    assert set(tr.finding(run_id="r", state="s", session="c", text="t")) == keys
    assert set(tr.finding(run_id="r", state=None, session=None, error="e")) == keys
    # And what run() actually posts, report and alert alike.
    _, _, post, _ = trigger(FakeKube({"memory.json": encoded(SNAPSHOT_IN)}))
    _, _, alert, _ = trigger(FakeKube(), FakeAgent(**FAILURES["failed"]))
    assert set(post.posted[0][1]) == keys and set(alert.posted[0][1]) == keys
