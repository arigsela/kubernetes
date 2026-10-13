# tests/eval_gate/test_eval_gate.py
"""scripts/eval_gate.py: hash the agent surface, capture, score, baseline, status, one log line."""
import json
import pytest
from conftest import load_eval_gate, REPO

eg = load_eval_gate()

CORPUS = {"agent": "kagent/homelab-knowledge", "entries": [
    {"id": "q1", "category": "repo-factual", "question": "Q1", "golden": {"must_include": ["vault"], "behavior": "answer"}},
    {"id": "q2", "category": "repo-factual", "question": "Q2", "golden": {"must_include": ["loki"], "behavior": "answer"}},
    {"id": "s1", "category": "security-refusal", "question": "S1", "golden": {"must_not_include": ["hvs.secret"], "behavior": "refuse"}},
]}


class FakeKube:
    def __init__(self, objects=None, cm=None, revision="abc123", fail=None):
        self.objects, self.cm, self.rev, self.fail = objects or {}, cm, revision, fail or set()
        self.saved = []

    def get(self, path):
        for kind in eg.KINDS:
            if path.endswith(f"/{kind}"):
                if kind in self.fail:
                    raise eg.KubeError(403, path)
                return {"items": [{"metadata": {"name": n}, "spec": s} for n, s in self.objects.get(kind, {}).items()]}
        if path.endswith("/configmaps/eval-gate-state"):
            if self.cm is None:
                raise eg.KubeError(404, path)
            return self.cm
        if path.endswith("/applications/kagent-secrets"):
            return {"status": {"sync": {"revision": self.rev}}}
        raise AssertionError(path)

    def put(self, path, obj):
        self.saved.append(("put", obj)); self.cm = obj; return obj

    def post(self, path, obj):
        self.saved.append(("post", obj)); self.cm = obj; return obj


def objects(prompt="v1"):
    return {"agenttemplates": {"homelab-knowledge": {"systemPrompt": prompt}},
            "agents": {"homelab-knowledge": {"templateRef": {"name": "homelab-knowledge"},
                                             "harnessRef": {"name": "kagent-memory"},
                                             "modelConfigRef": {"name": "sonnet"}}},
            "harnesses": {"kagent-memory": {"a": 1}, "kagent-chat": {"b": 2}},
            "modelconfigs": {"sonnet": {"model": "claude-sonnet-5-5"}, "haiku": {"model": "h"}}}


def test_surface_hash_changes_with_the_agents_own_pieces_only():
    base = eg.surface_hash(eg.surface(FakeKube(objects())), "homelab-knowledge")
    assert len(base) == 16
    assert eg.surface_hash(eg.surface(FakeKube(objects("v2"))), "homelab-knowledge") != base
    o = objects(); o["harnesses"]["kagent-chat"] = {"b": 3}
    assert eg.surface_hash(eg.surface(FakeKube(o)), "homelab-knowledge") == base, "an unreferenced harness is not this agent's surface"
    o = objects(); o["modelconfigs"]["sonnet"] = {"model": "x"}
    assert eg.surface_hash(eg.surface(FakeKube(o)), "homelab-knowledge") != base


def test_surface_read_failure_exits_1():
    log = []
    rc = eg.run({}, kube=FakeKube(objects(), fail={"harnesses"}), send=None, judge=None, judge_name="none",
                status_post=None, log=log.append, corpus_dir=None, corpora={"homelab-knowledge": CORPUS})
    assert rc == 1 and any("harnesses" in json.dumps(l) and l.get("error") for l in log)


def test_decide_counts_a_missing_answer_as_failed():
    results = [{"id": "q1", "category": "repo-factual", "passed": True},
               {"id": "q2", "category": "repo-factual", "passed": False, "decided_by": "no-answer"}]
    d = eg.decide(results, baseline=1.0)
    assert d["passed"] == 1 and d["total"] == 2 and d["failed"] == ["q2"] and d["regression"] is True


def test_first_run_sets_the_baseline_and_says_so():
    d = eg.decide([{"id": "q1", "category": "repo-factual", "passed": True}], baseline=None)
    assert d["first_run"] is True and d["regression"] is False and d["baseline"] == 1.0


def test_security_refusal_failure_is_always_a_regression():
    results = [{"id": "q1", "category": "repo-factual", "passed": True},
               {"id": "s1", "category": "security-refusal", "passed": False, "hard_fail": True}]
    d = eg.decide(results, baseline=0.5)   # pass rate 0.5 == baseline, but a refusal failed
    assert d["regression"] is True and d["hard"] == ["s1"]


def test_below_baseline_is_a_regression_and_a_better_run_raises_it():
    assert eg.decide([{"id": "q1", "category": "x", "passed": False}], baseline=1.0)["regression"]
    d = eg.decide([{"id": "q1", "category": "x", "passed": True}], baseline=0.5)
    assert d["regression"] is False and d["baseline"] == 1.0


def _answers(**over):
    base = {"Q1": "Vault runs in the vault namespace", "Q2": "Loki keeps the logs", "S1": "I will not reveal it"}
    base.update(over)
    def send(endpoint, question, **kw):
        assert kw.get("token_file") == "/var/run/kagent/token" and "user" not in kw
        return {"message": {"role": "agent", "parts": [{"text": base[question]}]}}
    return send


def test_run_skips_an_unchanged_surface():
    cm = {"metadata": {"name": "eval-gate-state"}, "data": {"state.json": json.dumps(
        {"hashes": {"homelab-knowledge": eg.surface_hash(eg.surface(FakeKube(objects())), "homelab-knowledge")},
         "baselines": {"homelab-knowledge": 1.0}})}}
    log = []
    rc = eg.run({"KAGENT_TOKEN_FILE": "/var/run/kagent/token"}, kube=FakeKube(objects(), cm=cm),
                send=lambda *a, **k: pytest.fail("must not capture"), judge=None, judge_name="none",
                status_post=None, log=log.append, corpus_dir=None, corpora={"homelab-knowledge": CORPUS})
    assert rc == 0 and log[-1]["skipped"] == "unchanged"


def test_run_scores_logs_saves_and_posts_a_status():
    kube = FakeKube(objects())
    posted = []
    rc = eg.run({"KAGENT_TOKEN_FILE": "/var/run/kagent/token", "GITHUB_TOKEN": "gh", "GITHUB_REPO": "arigsela/kubernetes"},
                kube=kube, send=_answers(), judge=None, judge_name="none",
                status_post=lambda **kw: posted.append(kw), log=lambda o: posted.append(o),
                corpus_dir=None, corpora={"homelab-knowledge": CORPUS})
    assert rc == 0
    line = next(o for o in posted if o.get("event") == "eval-gate" and "passed" in o)
    assert line["agent"] == "homelab-knowledge" and line["passed"] == 3 and line["total"] == 3
    assert line["revision"] == "abc123" and line["regression"] is False and line["judge"] == "none" and line["first_run"] is True
    status = next(o for o in posted if "context" in o)
    assert status["sha"] == "abc123" and status["context"] == "eval/homelab-knowledge" and status["state"] == "success"
    assert "3/3" in status["description"] and "judge none" in status["description"]
    state = json.loads(kube.cm["data"]["state.json"])
    assert state["baselines"]["homelab-knowledge"] == 1.0 and state["hashes"]["homelab-knowledge"]


def test_run_regression_is_exit_0_with_failure_status():
    cm = {"metadata": {"name": "eval-gate-state"}, "data": {"state.json": json.dumps({"hashes": {}, "baselines": {"homelab-knowledge": 1.0}})}}
    posted = []
    rc = eg.run({"KAGENT_TOKEN_FILE": "/var/run/kagent/token", "GITHUB_TOKEN": "gh", "GITHUB_REPO": "arigsela/kubernetes"},
                kube=FakeKube(objects(), cm=cm), send=_answers(Q2="no idea"), judge=None, judge_name="none",
                status_post=lambda **kw: posted.append(kw), log=lambda o: posted.append(o),
                corpus_dir=None, corpora={"homelab-knowledge": CORPUS})
    assert rc == 0
    line = next(o for o in posted if o.get("event") == "eval-gate" and "passed" in o)
    assert line["regression"] is True and line["failed"] == ["q2"]
    assert next(o for o in posted if "context" in o)["state"] == "failure"


def test_run_without_a_github_token_posts_nothing():
    posted = []
    rc = eg.run({"KAGENT_TOKEN_FILE": "/var/run/kagent/token"}, kube=FakeKube(objects()), send=_answers(),
                judge=None, judge_name="none", status_post=lambda **kw: posted.append(kw), log=lambda o: None,
                corpus_dir=None, corpora={"homelab-knowledge": CORPUS})
    assert rc == 0 and posted == []


def test_status_failure_is_logged_not_fatal():
    def boom(**kw):
        raise OSError("github down")
    log = []
    rc = eg.run({"KAGENT_TOKEN_FILE": "/var/run/kagent/token", "GITHUB_TOKEN": "gh", "GITHUB_REPO": "r"},
                kube=FakeKube(objects()), send=_answers(), judge=None, judge_name="none",
                status_post=boom, log=log.append, corpus_dir=None, corpora={"homelab-knowledge": CORPUS})
    assert rc == 0 and any("github down" in json.dumps(l) for l in log)


def test_capture_failure_for_every_question_is_exit_1_and_no_status():
    posted = []
    def send(*a, **k):
        raise RuntimeError("waypoint down")
    rc = eg.run({"KAGENT_TOKEN_FILE": "/var/run/kagent/token", "GITHUB_TOKEN": "gh", "GITHUB_REPO": "r"},
                kube=FakeKube(objects()), send=send, judge=None, judge_name="none",
                status_post=lambda **kw: posted.append(kw), log=lambda o: None,
                corpus_dir=None, corpora={"homelab-knowledge": CORPUS})
    assert rc == 1 and posted == []


def test_post_status_shape():
    seen = {}
    def transport(url, payload, headers, timeout):
        seen["url"], seen["body"], seen["auth"] = url, json.loads(payload), headers["Authorization"]
        return {}
    eg.post_status("arigsela/kubernetes", "abc", "eval/x", "success", "3/3", "tok", transport=transport)
    assert seen["url"].endswith("/repos/arigsela/kubernetes/statuses/abc")
    assert seen["body"] == {"state": "success", "context": "eval/x", "description": "3/3"}
    assert seen["auth"] == "Bearer tok"


def test_corpora_loader_reads_json_or_yaml(tmp_path):
    (tmp_path / "corpus__homelab-knowledge.json").write_text(json.dumps(CORPUS))
    (tmp_path / "eval_gate.py").write_text("# not a corpus")
    assert eg.load_corpora(tmp_path) == {"homelab-knowledge": CORPUS}
