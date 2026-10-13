#!/usr/bin/env python3
"""Evaluation gate (ADP harness · evaluation, E3): score the live agents after a sync.

Runs as an Argo CD PostSync hook of the kagent-secrets Application
(base-apps/kagent/eval-gate-job.yaml). For every agent with a corpus:
  1. hash the agent's live definition (Agent, its AgentTemplate, Harness, ModelConfig specs);
     unchanged since the last scored run -> skip;
  2. capture one answer per corpus question over in-cluster A2A as service account
     eval-gate (projected token, audience kagent);
  3. score with the rubric and whatever judge the keys allow (none, jev, claude, cascade);
  4. regression = any security-refusal failed, or pass rate below the stored baseline;
  5. one JSON line (Loki -> Grafana "Agent eval regression"), a commit status on the synced
     revision when GITHUB_TOKEN is set, and the baseline/hash saved in ConfigMap eval-gate-state.
Exit 0 on a regression (the sync succeeded; the status and the alert are the gate), exit 1 only
when scoring was impossible (surface unreadable, every capture failed).

Environment: KAGENT_A2A_BASE, KAGENT_TOKEN_FILE, CORPUS_DIR (corpus__<agent>.json or
<agent>.yaml files), KUBE_API (default https://kubernetes.default.svc), the pod's SA token/CA,
ARGOCD_NAMESPACE (argo-cd), ARGOCD_APP (kagent-secrets), GITHUB_REPO, GITHUB_TOKEN,
ANTHROPIC_API_KEY, TYPESAFE_API_KEY, QUESTION_TIMEOUT.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
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

KINDS = ["agenttemplates", "agents", "harnesses", "modelconfigs"]
KAGENT_API = "/apis/api.kagent.dev/v1alpha3/namespaces/kagent"
STATE_CM = "/api/v1/namespaces/kagent/configmaps/eval-gate-state"
SA_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
GITHUB_API = "https://api.github.com"


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
        out[kind] = sorted(({"name": i["metadata"]["name"], "spec": i.get("spec") or {}} for i in items),
                           key=lambda o: o["name"])
    return out


def _ref(spec: dict, key: str) -> str | None:
    r = spec.get(key)
    return r.get("name") if isinstance(r, dict) else r


def surface_hash(surf: dict[str, list[dict]], agent: str) -> str:
    """The pieces that define ONE agent: its Agent, the AgentTemplate, Harness and ModelConfig
    it references. A missing reference includes everything of that kind (conservative)."""
    by = {kind: {o["name"]: o["spec"] for o in surf.get(kind, [])} for kind in KINDS}
    agent_spec = by["agents"].get(agent, {})
    template = _ref(agent_spec, "templateRef") or agent
    harness = _ref(agent_spec, "harnessRef")
    model = _ref(agent_spec, "modelConfigRef") or _ref(by["agenttemplates"].get(template, {}), "modelConfigRef")
    pieces = {"agent": agent_spec, "template": by["agenttemplates"].get(template),
              "harness": by["harnesses"].get(harness) if harness else by["harnesses"],
              "model": by["modelconfigs"].get(model) if model else by["modelconfigs"]}
    return hashlib.sha256(json.dumps(pieces, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ state

@dataclass
class State:
    hashes: dict = field(default_factory=dict)
    baselines: dict = field(default_factory=dict)
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
                   exists=True, resource_version=(cm.get("metadata") or {}).get("resourceVersion"))

    def save(self, kube) -> None:
        obj = {"apiVersion": "v1", "kind": "ConfigMap",
               "metadata": {"name": "eval-gate-state", "namespace": "kagent",
                            "labels": {"app": "eval-gate"}},
               "data": {"state.json": json.dumps({"hashes": self.hashes, "baselines": self.baselines},
                                                 sort_keys=True)}}
        if self.exists:
            if self.resource_version:
                obj["metadata"]["resourceVersion"] = self.resource_version
            kube.put(STATE_CM, obj)
        else:
            kube.post("/api/v1/namespaces/kagent/configmaps", obj)
            self.exists = True


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


def load_corpora(corpus_dir: Path) -> dict[str, dict]:
    """corpus__<agent>.json (what the generator writes) or <agent>.yaml (the repo); keyed by agent."""
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
            out[p.stem.removeprefix("corpus__")] = doc
    return out


# ------------------------------------------------------------------ run

def run(env, *, kube, send, judge, judge_name: str, status_post, log, corpus_dir, corpora=None) -> int:
    corpora = corpora if corpora is not None else load_corpora(corpus_dir)
    try:
        surf = surface(kube)
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
    for agent, corpus in corpora.items():
        h = surface_hash(surf, agent)
        if state.hashes.get(agent) == h:
            log({"event": "eval-gate", "agent": agent, "hash": h, "revision": rev, "skipped": "unchanged"})
            continue
        entries = corpus["entries"]
        endpoint = a2a.resolve_endpoint(base, corpus.get("agent") or f"kagent/{agent}")
        answers = capture_mod.capture(entries, endpoint, send=send, timeout=timeout,
                                      token_file=token_file, log=log)
        if all(v is None for v in answers.values()):
            log({"event": "eval-gate", "agent": agent, "hash": h, "revision": rev,
                 "error": "every capture failed; nothing to score"})
            rc = 1
            continue
        by_id = {e["id"]: {**e, "_agent": corpus.get("agent")} for e in entries}
        results = score_eval.run(by_id, {k: v for k, v in answers.items() if v is not None}, judge)
        d = decide(results, state.baselines.get(agent))
        line = {"event": "eval-gate", "agent": agent, "hash": h, "revision": rev, "judge": judge_name, **d}
        log(line)
        state.hashes[agent] = h
        state.baselines[agent] = d["baseline"]
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
