#!/usr/bin/env python3
"""Evaluation gate (ADP harness · evaluation, E3; V93, V99): score the live agents after a sync.

Runs as an Argo CD PostSync hook of the kagent-secrets Application
(base-apps/kagent/eval-gate-job.yaml). For EVERY Agent in the kagent namespace:
  1. hash the agent's live definition (Agent, its AgentTemplate, Harness, ModelConfig specs);
     unchanged since the last scored run -> skip;
  2. assemble its corpus: tests/eval-corpus/<agent>.yaml (if any) plus the entries of
     _shared.yaml (agent "*") that apply to it — an entry with `requires_tools` only when the
     AgentTemplate binds one of those tools; `adversarial` entries as a deterministic sample
     (seed = agent + hash, size from the file), `canary` entries always;
  3. capture one answer per entry over in-cluster A2A as service account eval-gate (projected
     token, audience kagent), with the tool calls the agent made; a task that paused for an
     approval is scored on its PENDING calls and then rejected (the gate never approves);
  4. score with the rubric (must_include/must_not_include, expect_tools/forbid_tools) and
     whatever judge the keys allow (none, jev, claude, cascade);
  5. regression = any hard fail (a leak, a forbidden tool call, a security refusal) or a pass
     rate below the stored baseline; a changed corpus set resets the baseline (logged);
  6. one JSON line (Loki -> Grafana "Agent eval regression"), a commit status on the synced
     revision when GITHUB_TOKEN is set, the baseline/hash saved in ConfigMap eval-gate-state,
     and release.json (held = the last scored run regressed) written to ConfigMap eval-release
     in the dispatcher and slack-bridge namespaces, which those pods read as a file before
     opening a session (scripts/eval_release.py).
Exit 0 on a regression (the sync succeeded; the status, the alert and the hold are the gate),
exit 1 only when scoring was impossible (surface unreadable, every capture failed).

Environment: KAGENT_A2A_BASE, KAGENT_TOKEN_FILE, CORPUS_DIR (corpus__<agent>.json or
<agent>.yaml files), KUBE_API (default https://kubernetes.default.svc), the pod's SA token/CA,
ARGOCD_NAMESPACE (argo-cd), ARGOCD_APP (kagent-secrets), GITHUB_REPO, GITHUB_TOKEN,
ANTHROPIC_API_KEY, TYPESAFE_API_KEY, QUESTION_TIMEOUT, AGENT_READY_TIMEOUT.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import importlib.util
import json
import os
import random
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_sibling(stem: str):
    """Import a script beside this one by path: scripts/ keeps dashes, the ConfigMap underscores."""
    for name in (f"{stem}.py", f"{stem.replace('_', '-')}.py"):
        p = HERE / name
        if p.exists():
            spec = importlib.util.spec_from_file_location(stem, p)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[stem] = mod
            spec.loader.exec_module(mod)
            return mod
    raise ImportError(f"{stem}: no sibling script found beside {HERE}")


a2a = load_sibling("kagent_a2a")
score_eval = load_sibling("score_eval")
capture_mod = load_sibling("capture_eval_answers")
eval_release = load_sibling("eval_release")

KINDS = ["agenttemplates", "agents", "harnesses", "modelconfigs"]
KAGENT_API = "/apis/api.kagent.dev/v1alpha3/namespaces/kagent"
STATE_CM = "/api/v1/namespaces/kagent/configmaps/eval-gate-state"
SA_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
GITHUB_API = "https://api.github.com"
SHARED = "*"                      # the corpus key of _shared.yaml: applies to every agent


class KubeError(RuntimeError):
    def __init__(self, status: int, path: str):
        super().__init__(f"HTTP {status} for {path}")
        self.status, self.path = status, path


def _json_transport(url, payload, headers, timeout, *, method="GET", context=None):
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


class Kube:
    """Just enough of the API: GET, PUT, POST JSON with the pod's service account."""

    def __init__(self, base: str, token: str, ca_path: str | None = None, transport=None):
        self.base, self.token = base.rstrip("/"), token
        self.ctx = ssl.create_default_context(cafile=ca_path) if ca_path else None
        self.transport = transport

    def _call(self, method: str, path: str, obj=None) -> dict:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        payload = None
        if obj is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(obj).encode()
        try:
            if self.transport:
                return self.transport(method, self.base + path, payload, headers)
            return _json_transport(self.base + path, payload, headers, 30, method=method, context=self.ctx)
        except urllib.error.HTTPError as err:
            raise KubeError(err.code, path) from err
        except OSError as err:           # URLError, timeouts, resets: status 0, never a traceback
            raise KubeError(0, f"{path} ({err})") from err

    def get(self, path):
        return self._call("GET", path)

    def put(self, path, obj):
        return self._call("PUT", path, obj)

    def post(self, path, obj):
        return self._call("POST", path, obj)


# ------------------------------------------------------------------ the agent surface

def surface(kube) -> dict[str, list[dict]]:
    out = {}
    for kind in KINDS:
        items = kube.get(f"{KAGENT_API}/{kind}").get("items") or []
        out[kind] = sorted(({"name": i["metadata"]["name"], "spec": i.get("spec") or {},
                             "generation": (i.get("metadata") or {}).get("generation"),
                             "observed": (i.get("status") or {}).get("observedGeneration")} for i in items),
                           key=lambda o: o["name"])
    return out


def agents_behind(surf: dict, agents) -> list[str]:
    """Agents whose controller has not yet observed the applied generation (when it reports one)."""
    by = {o["name"]: o for o in surf.get("agents", [])}
    return [a for a in agents
            if by.get(a) and by[a].get("generation") is not None and by[a].get("observed") is not None
            and by[a]["observed"] < by[a]["generation"]]


def _ref(spec: dict, key: str) -> str | None:
    r = spec.get(key)
    return r.get("name") if isinstance(r, dict) else r


def _template_ref(agent_spec: dict) -> str | None:
    """The AgentTemplate an Agent references: alpha14's spec.template.ref.name (kagent#3080);
    alpha11's spec.templateRef.name is still read so a stored surface from before the bump hashes the same way."""
    ref = ((agent_spec.get("template") or {}).get("ref") or {})
    return ref.get("name") or _ref(agent_spec, "templateRef")


def _template_of(surf: dict, agent: str) -> dict:
    by = {o["name"]: o["spec"] for o in surf.get("agenttemplates", [])}
    agent_spec = next((o["spec"] for o in surf.get("agents", []) if o["name"] == agent), {})
    return by.get(_template_ref(agent_spec) or agent) or {}


def template_tools(surf: dict, agent: str) -> set[str]:
    """Every tool name the agent's AgentTemplate binds (across its MCP bindings), plus those of
    its sub-agents' templates one hop down: a 1.0 sub-agent takes the conversation over, so
    its tools are reachable from this agent."""
    out: set[str] = set()
    seen: set[str] = set()

    def walk(template: dict, depth: int) -> None:
        for binding in template.get("tools") or []:
            mcp = binding.get("mcp") if isinstance(binding, dict) else None
            if isinstance(mcp, dict):
                out.update(t for t in (mcp.get("tools") or []) if isinstance(t, str))
        if depth:
            by = {o["name"]: o["spec"] for o in surf.get("agenttemplates", [])}
            for sub in template.get("subAgents") or []:
                name = _ref(sub, "name") if isinstance(sub, dict) else sub
                name = name if isinstance(name, str) else (sub.get("name") if isinstance(sub, dict) else None)
                if name and name not in seen and name in by:
                    seen.add(name)
                    walk(by[name], depth - 1)

    walk(_template_of(surf, agent), 1)
    return out


def surface_hash(surf: dict[str, list[dict]], agent: str) -> str:
    """The pieces that define ONE agent: its Agent, the AgentTemplate, Harness and ModelConfig
    it references. A missing reference includes everything of that kind (conservative)."""
    by = {kind: {o["name"]: o["spec"] for o in surf.get(kind, [])} for kind in KINDS}
    agent_spec = by["agents"].get(agent, {})
    template = _template_ref(agent_spec) or agent
    harness = _ref(agent_spec, "harnessRef")
    model = _ref(agent_spec, "modelConfigRef") or _ref(by["agenttemplates"].get(template, {}), "modelConfigRef")
    pieces = {"agent": agent_spec, "template": by["agenttemplates"].get(template),
              "harness": by["harnesses"].get(harness) if harness else by["harnesses"],
              "model": by["modelconfigs"].get(model) if model else by["modelconfigs"]}
    return hashlib.sha256(json.dumps(pieces, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ the corpus per agent

def applicable_shared(shared: dict, agent: str, tools: set[str], h: str) -> list[dict]:
    """The _shared.yaml entries this agent gets this run (V99).

    `requires_tools` scopes an entry to agents binding one of those tools (a canary question
    about pod logs is pointless for an agent with no log tool). The file's `sample` category
    is drawn as a deterministic sample — same agent, same definition, same questions, so a
    re-run is comparable and a change is scored on fresh prompts; every other entry always
    runs."""
    sample = shared.get("sample") or {}
    always, pool = [], []
    for e in shared.get("entries") or []:
        req = e.get("requires_tools")
        if req and not (set(req) & tools):
            continue
        (pool if sample and e.get("category") == sample.get("category") else always).append(e)
    if sample and pool:
        k = min(int(sample.get("size") or len(pool)), len(pool))
        rng = random.Random(f"{agent}:{h}")       # a str seed is stable across processes
        pool = sorted(rng.sample(pool, k), key=lambda e: e["id"])
    return always + pool


def corpus_hash(entries: list[dict]) -> str:
    """Identity of the question SET an agent was scored on: a different set resets the baseline."""
    return hashlib.sha256(json.dumps(sorted(e["id"] for e in entries)).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ state

@dataclass
class State:
    hashes: dict = field(default_factory=dict)
    baselines: dict = field(default_factory=dict)
    corpus: dict = field(default_factory=dict)      # agent -> corpus_hash it was last scored on
    release: dict = field(default_factory=dict)     # agent -> {held, hash, reason, scored_at}
    exists: bool = False
    resource_version: str | None = None

    @classmethod
    def load(cls, kube) -> "State":
        try:
            cm = kube.get(STATE_CM)
        except KubeError as err:
            if err.status == 404:
                return cls()
            raise
        data = json.loads((cm.get("data") or {}).get("state.json") or "{}")
        return cls(hashes=data.get("hashes") or {}, baselines=data.get("baselines") or {},
                   corpus=data.get("corpus") or {}, release=data.get("release") or {},
                   exists=True, resource_version=(cm.get("metadata") or {}).get("resourceVersion"))

    def save(self, kube) -> None:
        obj = {"apiVersion": "v1", "kind": "ConfigMap",
               "metadata": {"name": "eval-gate-state", "namespace": "kagent",
                            "labels": {"app": "eval-gate"}},
               "data": {"state.json": json.dumps({"hashes": self.hashes, "baselines": self.baselines,
                                                  "corpus": self.corpus, "release": self.release},
                                                 sort_keys=True)}}
        if self.exists:
            if self.resource_version:
                obj["metadata"]["resourceVersion"] = self.resource_version
            kube.put(STATE_CM, obj)
        else:
            kube.post("/api/v1/namespaces/kagent/configmaps", obj)
            self.exists = True


def write_release(kube, release: dict, *, revision: str | None, now: str, log) -> None:
    """release.json into ConfigMap eval-release of each consumer namespace (V99). A failure is a
    warning: the gate's own state carries the holds and the next run writes them again."""
    text = eval_release.render(release, revision=revision, written_at=now)
    for ns in eval_release.CONSUMER_NAMESPACES:
        path = f"/api/v1/namespaces/{ns}/configmaps/{eval_release.CONFIGMAP}"
        obj = {"apiVersion": "v1", "kind": "ConfigMap",
               "metadata": {"name": eval_release.CONFIGMAP, "namespace": ns, "labels": {"app": "eval-gate"}},
               "data": {eval_release.KEY: text}}
        try:
            try:
                current = kube.get(path)
            except KubeError as err:
                if err.status != 404:
                    raise
                kube.post(f"/api/v1/namespaces/{ns}/configmaps", obj)
                continue
            rv = (current.get("metadata") or {}).get("resourceVersion")
            if rv:
                obj["metadata"]["resourceVersion"] = rv
            kube.put(path, obj)
        except KubeError as err:
            log({"event": "eval-gate", "warning": f"release.json not written to {ns}: {err}"})


# ------------------------------------------------------------------ decision

def decide(results: list[dict], baseline: float | None) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    rate = (passed / total) if total else 0.0
    failed = sorted(r["id"] for r in results if not r.get("passed"))
    hard = sorted(r["id"] for r in results
                  if not r.get("passed") and (r.get("category") == "security-refusal" or r.get("hard_fail")))
    first = baseline is None
    regression = bool(hard) or (not first and rate < baseline)
    new_baseline = rate if first else max(baseline, rate)
    return {"passed": passed, "total": total, "pass_rate": round(rate, 4), "regression": regression,
            "failed": failed, "hard": hard, "baseline": round(new_baseline, 4), "first_run": first}


# ------------------------------------------------------------------ outputs

def revision(kube, namespace="argo-cd", app="kagent-secrets") -> str | None:
    try:
        obj = kube.get(f"/apis/argoproj.io/v1alpha1/namespaces/{namespace}/applications/{app}")
    except KubeError:
        return None
    return ((obj.get("status") or {}).get("sync") or {}).get("revision")


def post_status(repo: str, sha: str, context: str, state: str, description: str, token: str,
                transport=None) -> None:
    body = {"state": state, "context": context, "description": description[:140]}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "Content-Type": "application/json", "User-Agent": "eval-gate"}
    post = transport or (lambda url, payload, headers, timeout:
                         _json_transport(url, payload, headers, timeout, method="POST"))
    post(f"{GITHUB_API}/repos/{repo}/statuses/{sha}", json.dumps(body).encode(), headers, 30)


def agent_ref(corpus: dict, agent: str) -> str:
    """namespace/name for resolve_endpoint: the corpus names the agent bare (`homelab-knowledge`)."""
    ref = corpus.get("agent") or agent
    return ref if "/" in ref and ref != SHARED else f"kagent/{agent if ref == SHARED else ref}"


def load_corpora(corpus_dir: Path) -> dict[str, dict]:
    """corpus__<agent>.json (what the generator writes) or <agent>.yaml (the repo); keyed by
    agent, with _shared.yaml under the key "*"."""
    out = {}
    for p in sorted(Path(corpus_dir).iterdir()):
        if p.suffix == ".json":
            doc = json.loads(p.read_text())
        elif p.suffix in (".yaml", ".yml"):
            import yaml  # lazy: only on the laptop
            doc = yaml.safe_load(p.read_text())
        else:
            continue
        if isinstance(doc, dict) and doc.get("entries"):
            stem = p.stem.removeprefix("corpus__")
            out[SHARED if doc.get("agent") == SHARED or stem == "_shared" else stem] = doc
    return out


def targets(surf: dict, corpora: dict) -> list[str]:
    """Which agents this run scores: every Agent in the namespace once a shared corpus exists,
    else only the agents with their own corpus."""
    own = {a for a in corpora if a != SHARED}
    if SHARED in corpora:
        own |= {o["name"] for o in surf.get("agents", [])}
    return sorted(own)


# ------------------------------------------------------------------ run

def run(env, *, kube, send, judge, judge_name: str, status_post, log, corpus_dir, corpora=None,
        sleep=None, clock=None, now=None) -> int:
    import time
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    now = now or (lambda: _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat())
    corpora = corpora if corpora is not None else load_corpora(corpus_dir)
    shared = corpora.get(SHARED)
    try:
        surf = surface(kube)
        agents = targets(surf, corpora)
        # PostSync fires when the CRs are applied, before the runtime rolls the new definition.
        # Wait, bounded, for the controller to observe the applied generation of each agent we
        # are about to score (kinds without the field pass straight through).
        deadline = clock() + float(env.get("AGENT_READY_TIMEOUT", "180"))
        while agents_behind(surf, agents) and clock() < deadline:
            sleep(5)
            surf = surface(kube)
        behind = agents_behind(surf, agents)
        if behind:
            log({"event": "eval-gate", "warning": f"still rolling after the wait: {', '.join(behind)}"})
    except KubeError as err:
        log({"event": "eval-gate", "error": f"cannot read the agent surface: {err}"})
        return 1
    try:
        state = State.load(kube)
    except KubeError as err:
        log({"event": "eval-gate", "error": f"cannot read eval-gate-state: {err}"})
        return 1
    rev = revision(kube, env.get("ARGOCD_NAMESPACE", "argo-cd"), env.get("ARGOCD_APP", "kagent-secrets"))
    base = env.get("KAGENT_A2A_BASE", a2a.IN_CLUSTER_BASE)
    token_file = env.get("KAGENT_TOKEN_FILE", "/var/run/kagent/token")
    timeout = float(env.get("QUESTION_TIMEOUT", "180"))
    rc = 0
    for agent in agents:
        corpus = corpora.get(agent) or {"agent": agent, "entries": []}
        h = surface_hash(surf, agent)
        if state.hashes.get(agent) == h:
            log({"event": "eval-gate", "agent": agent, "hash": h, "revision": rev, "skipped": "unchanged"})
            continue
        own = list(corpus["entries"])
        extra = applicable_shared(shared, agent, template_tools(surf, agent), h) if shared else []
        entries = own + extra
        if not entries:
            log({"event": "eval-gate", "agent": agent, "hash": h, "revision": rev, "skipped": "no applicable entries"})
            continue
        ch = corpus_hash(entries)
        baseline = state.baselines.get(agent)
        # A baseline is comparable only to the question set it was measured on. A stored set
        # that differs, or NO stored set (a baseline from before corpus hashes existed), means
        # the old rate is not comparable: start a new baseline. §B.32: treating "no stored set"
        # as "same set" held homelab-knowledge on 13/21 against a 1.0 measured on 8 questions.
        reset = baseline is not None and state.corpus.get(agent) != ch
        if reset:
            baseline = None
        endpoint = a2a.resolve_endpoint(base, agent_ref(corpus, agent))

        def reject(reply, _ep=endpoint):
            return capture_mod.reject_pending(reply, _ep, send=send, timeout=timeout, token_file=token_file)

        captured = capture_mod.capture_full(entries, endpoint, send=send, timeout=timeout,
                                            token_file=token_file, log=log, reject=reject)
        if all(v["answer"] is None and not v["tool_calls"] for v in captured.values()):
            log({"event": "eval-gate", "agent": agent, "hash": h, "revision": rev,
                 "error": "every capture failed; nothing to score"})
            rc = 1
            continue
        by_id = {e["id"]: {**e, "_agent": agent} for e in entries}
        # A turn with calls but no text (a rejected pause whose closing text did not arrive)
        # is scored on its calls: "" keeps the rubric running; None would be "no answer".
        answers = {k: (v["answer"] if v["answer"] is not None else "")
                   for k, v in captured.items() if v["answer"] is not None or v["tool_calls"]}
        results = score_eval.run(by_id, answers, judge, tool_calls={k: v["tool_calls"] for k, v in captured.items()})
        d = decide(results, baseline)
        # The answers must belong to the definition we hashed: re-read, and if it moved during
        # capture record nothing, so the next sync scores the new definition.
        try:
            h_after = surface_hash(surface(kube), agent)
        except KubeError as err:
            h_after = None
            log({"event": "eval-gate", "agent": agent, "warning": f"cannot re-read the surface: {err}"})
        moved = h_after is not None and h_after != h
        rejected = sum(1 for v in captured.values() if v["pending"])
        polled = sum(1 for v in captured.values() if v.get("polled"))
        # Why each failed entry failed, from the log alone (§B.35): who decided, the task
        # state the capture saw, and the rubric's reasons (short strings; never the answer).
        detail = {r["id"]: {"by": r.get("decided_by"),
                            "state": (captured.get(r["id"]) or {}).get("state"),
                            "reasons": [str(x)[:120] for x in (r.get("reasons") or [])][:3]}
                  for r in results if not r.get("passed")}
        line = {"event": "eval-gate", "agent": agent, "hash": h, "revision": rev, "judge": judge_name,
                "surface_moved": moved, "entries": len(entries), "shared": len(extra), "corpus_hash": ch,
                "baseline_reset": reset, "pauses_rejected": rejected, "polled": polled,
                "held": d["regression"], **d, "failed_detail": detail}
        log(line)
        if moved:
            continue
        state.hashes[agent] = h
        state.baselines[agent] = d["baseline"]
        state.corpus[agent] = ch
        reason = None
        if d["regression"]:
            reason = (f"eval regression on {h} (rev {rev or '?'}): "
                      + (f"hard {', '.join(d['hard'])}" if d["hard"] else
                         f"pass rate {d['pass_rate']:.2f} < baseline {state.baselines[agent]:.2f}"))
        state.release[agent] = {"held": d["regression"], "hash": h, "reason": reason, "scored_at": now()}
        if env.get("GITHUB_TOKEN") and env.get("GITHUB_REPO") and rev:
            desc = (f"{d['passed']}/{d['total']} (baseline {d['baseline']:.2f}, judge {judge_name})"
                    + (f" failed: {', '.join(d['failed'])}" if d["failed"] else ""))
            try:
                status_post(repo=env["GITHUB_REPO"], sha=rev, context=f"eval/{agent}",
                            state="failure" if d["regression"] else "success",
                            description=desc, token=env["GITHUB_TOKEN"])
            except (OSError, ValueError, RuntimeError) as err:
                log({"event": "eval-gate", "agent": agent, "warning": f"status not posted: {err}"})
    try:
        state.save(kube)
    except KubeError as err:
        log({"event": "eval-gate", "warning": f"state not saved: {err}"})
    write_release(kube, state.release, revision=rev, now=now(), log=log)
    return rc


def main() -> int:
    env = os.environ
    kube = Kube(env.get("KUBE_API", "https://kubernetes.default.svc"),
                Path(env.get("KUBE_TOKEN_FILE", SA_TOKEN)).read_text().strip(),
                env.get("KUBE_CA_FILE", SA_CA))
    judge, judge_name = score_eval.select_judge_from_env(env)
    return run(env, kube=kube, send=capture_mod.send_message, judge=judge, judge_name=judge_name,
               status_post=post_status, log=lambda o: print(json.dumps(o, sort_keys=True), flush=True),
               corpus_dir=Path(env.get("CORPUS_DIR", "/opt/eval-gate")))


if __name__ == "__main__":
    raise SystemExit(main())
