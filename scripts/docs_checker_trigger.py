#!/usr/bin/env python3
"""Weekly docs check (§T.132, V102): run docs-checker over every doc, keep its memory between
runs, post the findings to Slack.

Runs as the CronJob docs-checker-trigger (base-apps/kagent/docs-checker-trigger.yaml), Mondays
14:00 UTC, as service account kagent/docs-checker-trigger. Standard library only, like the eval
gate; its code ConfigMap is generated (scripts/gen-docs-checker-trigger-configmap.py).

  1. Read ConfigMap docs-checker-memory: `memory.json` (docs-checker's memory snapshot; absent
     means none, so the agent starts fresh) and `deliveries.json` (the runs already claimed). A
     run id already there means an earlier pod of this Job got that far: nothing is sent, saved
     or posted again.
  2. Send docs-checker ONE A2A message whose only part is the data part
     {"scope": "all", "mode": "scheduled", "memory": <snapshot or null>}, through the
     controller's JSON-RPC gateway (POST /agents/kagent/docs-checker, the route the eval gate and
     the dispatcher use; the gateway speaks gRPC to the byo runtime) with the pod's projected
     token (audience kagent), so the mesh names the caller
     system:serviceaccount:kagent:docs-checker-trigger (V91). No text part: if the data part were
     ever dropped, the empty request is refused (no report: an alert below) instead of running
     as an interactive check, which would return an EMPTY memory snapshot. A task handed back
     before it settled is polled with GetTask until RUN_TIMEOUT.
  3. A COMPLETED task must carry the data parts docs-checker/findings, /memory, /slack and
     /report. Then, in ONE optimistic write (the resourceVersion read in step 1), save the
     returned snapshot (always: its run counter changes every run) and claim this run's
     delivery; log the full report (the Slack digest points at the job log); post the digest to
     n8n's agent-findings webhook (the dispatcher's path to #oncall-alerts, with the payload keys
     of dispatch.py's _post_finding); then mark the run posted.
     A snapshot that cannot be saved does not cost the report: one that is not a version-1
     snapshot with an integer run counter, one that would take the ConfigMap's data past its
     budget (1 MiB, the API server's cap, less MEMORY_MARGIN), or one the API server refuses
     (422) is dropped. The claim is then written alone (the old memory stays), the report is
     posted, and an alert says "memory not saved" (exit 0: the report was delivered).
  4. Anything else (FAILED, CANCELED, paused, no settled task by RUN_TIMEOUT, an A2A error, a
     completed task without those parts): memory.json is NOT touched. The run is claimed as an
     alert and a visible failure is posted (n8n renders ":x: Investigation: Weekly docs check
     ... Failed: <reason>"), never an empty report.

Writes (C#47): every PUT is a read-modify-write of the ConfigMap as read, carrying its
resourceVersion, so it never lands on a version it did not see. On 409 the ConfigMap is read
again and the write rebuilt on it, at most WRITE_ATTEMPTS writes: a memory.json that changed in
between was saved by another run and is kept, not overwritten; a claim that finds its run id
already recorded stops (another pod of this run got there). The object's metadata (Argo CD's
tracking annotation, labels) and any other keys pass through untouched.

Delivery is at most once per run id (the Job name): the claim is saved BEFORE the post. If the
post fails, or the pod dies between the claim and the post, that week's message is lost, never
doubled: the digest and the report stay in the job log (Loki), the Job ends Failed, and the
findings come back next week (docs-checker carries open findings in its memory). When the
ConfigMap cannot be written at all, a report is not posted (nothing would stop a retry posting
it again) and an alert is; a failure alert is posted even when its claim could not be saved (a
visible failure beats a silent one). Anything unexpected (an exception this code did not
foresee, a missing service-account token) still ends in an alert (V102).

Exit 0 = the report was delivered (memory saved or not), or already delivered by an earlier pod
of this run; 1 = anything else.

Environment: RUN_ID (the Job name; the CronJob sets it from the pod label
batch.kubernetes.io/job-name), KAGENT_A2A_BASE, KAGENT_TOKEN_FILE, AGENT (kagent/docs-checker),
N8N_WEBHOOK_BASE, RUN_TIMEOUT (seconds, 1200), NAMESPACE (kagent), MEMORY_CONFIGMAP
(docs-checker-memory), KUBE_API, KUBE_TOKEN_FILE, KUBE_CA_FILE.
"""
from __future__ import annotations

import copy
import datetime as _dt
import importlib.util
import json
import os
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

AGENT = "kagent/docs-checker"
ALERTNAME = "Weekly docs check"            # docs-checker's own digest starts with these words too
RULE = "docs-checker-weekly"
NAMESPACE = "kagent"
MEMORY_CONFIGMAP = "docs-checker-memory"
MEMORY_KEY = "memory.json"
DELIVERIES_KEY = "deliveries.json"
KEEP_DELIVERIES = 10                       # run ids remembered; a Job's retry comes within minutes
WRITE_ATTEMPTS = 3
# The API server refuses a ConfigMap whose data values add up to more than 1 MiB (HTTP 422).
# memory.json shares that budget with deliveries.json and any other key; a write that would come
# within MEMORY_MARGIN of the cap is not attempted with the new snapshot.
CONFIGMAP_LIMIT = 1024 * 1024
MEMORY_MARGIN = 16 * 1024
MEMORY_BUDGET = CONFIGMAP_LIMIT - MEMORY_MARGIN
SNAPSHOT_VERSION = 1                       # docs-checker memory.py SCHEMA_VERSION
RUN_TIMEOUT = 1200.0                       # docs-checker stops itself at 15 min; the Job dies at 30
N8N_BASE = "http://n8n.n8n.svc.cluster.local:5678/webhook"
SA_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

# The completed task's data parts (docs-checker executor.py, completed_parts) and their types.
FINDINGS = "docs-checker/findings"
MEMORY = "docs-checker/memory"
SLACK = "docs-checker/slack"
REPORT = "docs-checker/report"
REQUIRED_PARTS = {FINDINGS: list, MEMORY: dict, SLACK: str, REPORT: dict}

_KEEP = object()                           # save(memory=_KEEP): leave memory.json as it is


class KubeError(RuntimeError):
    def __init__(self, status: int, path: str):
        super().__init__(f"HTTP {status} for {path}")
        self.status, self.path = status, path


class TooLarge(RuntimeError):
    """The new snapshot would take the ConfigMap's data past MEMORY_BUDGET."""

    def __init__(self, size: int):
        super().__init__(f"{size} bytes of ConfigMap data, over the {MEMORY_BUDGET}-byte budget")
        self.size = size


class AlreadyClaimed(RuntimeError):
    """Another pod of this run recorded its delivery first."""

    def __init__(self, entry: dict):
        super().__init__(f"already claimed: {entry}")
        self.entry = entry


def _json_transport(url, payload, headers, timeout, *, method="GET", context=None):
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


class Kube:
    """Just enough of the API: GET and PUT JSON with the pod's service account."""

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


# ------------------------------------------------------------------ the memory ConfigMap

def encode(snapshot) -> str:
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))


def encode_ledger(ledger: dict) -> str:
    """The deliveries ledger, newest KEEP_DELIVERIES runs only (by claim time, then run id)."""
    newest = sorted(ledger.items(), key=lambda kv: (str(kv[1].get("at") or ""), kv[0]))[-KEEP_DELIVERIES:]
    return json.dumps(dict(newest), sort_keys=True)


def data_bytes(data: dict) -> int:
    """What the API server counts against its 1 MiB cap (the values, in bytes), plus the keys."""
    return sum(len(k.encode()) + len(str(v).encode()) for k, v in data.items())


def snapshot_problem(snapshot) -> str | None:
    """Why a returned snapshot must not be saved, or None. docs-checker's own reader drops a
    snapshot that is not this shape, so saving one would only cost the next run its memory.
    Numbers can arrive as JSON doubles; a whole-number run counter is an integer."""
    if not isinstance(snapshot, dict):
        return "the snapshot is not an object"
    if snapshot.get("version") != SNAPSHOT_VERSION:
        return f"snapshot version {snapshot.get('version')!r}, expected {SNAPSHOT_VERSION}"
    run = snapshot.get("run")
    whole = isinstance(run, int) or (isinstance(run, float) and run.is_integer())
    if not whole or isinstance(run, bool):
        return f"run counter {run!r} is not an integer"
    return None


def run_counter(snapshot) -> int | None:
    return int(snapshot["run"]) if snapshot_problem(snapshot) is None else None


@dataclass
class Store:
    """The memory ConfigMap as read, kept whole: a write modifies this object, nothing else."""

    obj: dict

    @property
    def data(self) -> dict:
        return self.obj.get("data") or {}

    @property
    def memory_text(self) -> str | None:
        return self.data.get(MEMORY_KEY)

    def memory(self, log=None) -> Any:
        """The stored snapshot; None when there is none or it is not JSON (the agent starts fresh,
        and the snapshot it returns replaces the garbled one)."""
        text = self.memory_text
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            if log:
                log(warning=f"{MEMORY_KEY} is not JSON: sent as null, so docs-checker starts fresh")
            return None

    def deliveries(self, log=None) -> dict:
        text = self.data.get(DELIVERIES_KEY)
        if not text:
            return {}
        try:
            ledger = json.loads(text)
        except ValueError:
            ledger = None
        if not isinstance(ledger, dict):
            if log:
                log(warning=f"{DELIVERIES_KEY} is not a JSON object: ignored")
            return {}
        return {k: v for k, v in ledger.items() if isinstance(v, dict)}


def save(kube, path: str, store: Store, run_id: str, entry: dict, *, memory=_KEEP, claim: bool, log) -> Store:
    """Record this run's delivery `entry` (and, unless _KEEP, the memory snapshot) in the ConfigMap,
    optimistically (module docstring, "Writes"). Returns the ConfigMap as written. Raises TooLarge,
    before any PUT, when the new snapshot would take the data past MEMORY_BUDGET."""
    base = store.memory_text
    current = store
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        obj = copy.deepcopy(current.obj)
        if not (obj.get("metadata") or {}).get("resourceVersion"):
            raise KubeError(0, f"{path} (no resourceVersion to write against)")
        data = dict(obj.get("data") or {})
        writes_memory = False
        if memory is not _KEEP:
            if data.get(MEMORY_KEY) == base:
                data[MEMORY_KEY] = encode(memory)
                writes_memory = True
            else:
                log(warning=f"{MEMORY_KEY} changed since it was read: another run saved its snapshot; "
                            "kept it, this run's snapshot is not saved")
        ledger = current.deliveries()
        ledger[run_id] = entry
        data[DELIVERIES_KEY] = encode_ledger(ledger)
        if writes_memory and data_bytes(data) > MEMORY_BUDGET:
            raise TooLarge(data_bytes(data))
        obj["data"] = data
        try:
            return Store(kube.put(path, obj))
        except KubeError as err:
            if err.status != 409 or attempt == WRITE_ATTEMPTS:
                raise
            log(warning=f"conflict writing {path} (attempt {attempt}/{WRITE_ATTEMPTS}): re-read and retry")
        current = Store(kube.get(path))
        if claim and run_id in current.deliveries():
            raise AlreadyClaimed(current.deliveries()[run_id])
    raise AssertionError("unreachable")


# ------------------------------------------------------------------ the agent

@dataclass
class Outcome:
    state: str | None = None
    session: str | None = None
    task_id: str | None = None
    parts: dict = field(default_factory=dict)
    report: str | None = None      # the report Markdown (the artifact's text)
    error: str | None = None       # why there is nothing to deliver; None = a complete report


def build_request(memory) -> dict:
    """A blocking SendMessage (HITL extension listed, as on every kagent_a2a send) whose ONLY part
    is the scheduled whole-repo request."""
    payload = a2a.build_send_request("")
    payload["params"]["message"]["parts"] = [{"data": {"scope": "all", "mode": "scheduled", "memory": memory}}]
    return payload


def send_request(endpoint: str, payload: dict, *, token_file: str, timeout: float, transport=None,
                 sleep=time.sleep, clock=time.monotonic) -> dict:
    """POST one JSON-RPC request as the pod's identity (the token re-read per send, V92) and return
    its `result`. kagent_a2a.send_message builds text parts only; its retry rule is reused: a
    KAGENT_SEND_NOT_ACCEPTED rejection (input provably not dispatched) is resent byte-identical
    until a2a.RETRY_DEADLINE, anything else raises (the input may already be running)."""
    post = transport or a2a.http_post
    body = json.dumps(payload).encode()
    headers = a2a.request_headers(None, a2a.read_token_file(token_file))
    give_up = clock() + a2a.RETRY_DEADLINE
    while True:
        reply = post(endpoint, body, headers, timeout)
        if not isinstance(reply, dict):
            raise a2a.A2AError(f"not a JSON-RPC response: {reply!r:.200}")
        error = reply.get("error")
        if not error:
            result = reply.get("result")
            if not isinstance(result, dict):
                raise a2a.A2AError("JSON-RPC response has no result")
            return result
        wait = a2a._send_not_accepted(error) if isinstance(error, dict) else None
        if wait is None or clock() + wait > give_up:
            code = error.get("code") if isinstance(error, dict) else "?"
            msg = error.get("message", error) if isinstance(error, dict) else error
            raise a2a.A2AError(f"A2A error {code}: {msg}")
        sleep(wait)


def wait_task(endpoint: str, task_id: str, *, token_file: str, timeout: float) -> dict:
    return a2a.wait_for_task(endpoint, task_id, user=None, token_file=token_file, timeout=timeout)


def artifact_parts(task: dict) -> tuple[dict, str | None]:
    """The required data parts found in the task's artifacts, and their text (the report)."""
    found, texts = {}, []
    for art in task.get("artifacts") or []:
        for part in (art.get("parts") or []) if isinstance(art, dict) else []:
            if not isinstance(part, dict):
                continue
            data = part.get("data")
            if isinstance(data, dict):
                found.update({k: data[k] for k in REQUIRED_PARTS if k in data})
            elif isinstance(part.get("text"), str) and part["text"]:
                texts.append(part["text"])
    return found, ("\n".join(texts) or None)


def call_agent(endpoint: str, memory, *, send, wait, token_file: str, timeout: float,
               clock=time.monotonic) -> Outcome:
    deadline = clock() + timeout
    try:
        result = send(endpoint, build_request(memory), token_file=token_file, timeout=timeout)
        reply = a2a.parse_result(result)
        if reply.kind == "task" and reply.task_id and not reply.settled:
            remaining = deadline - clock()
            if remaining > 0:
                result = wait(endpoint, reply.task_id, token_file=token_file, timeout=remaining)
                reply = a2a.parse_result(result)
    except (a2a.A2AError, OSError, ValueError) as err:   # TimeoutError and URLError are OSErrors
        return Outcome(error=f"the A2A call failed: {str(err)[:300]}")
    out = Outcome(state=reply.state, session=reply.context_id, task_id=reply.task_id)
    reason = (reply.status_text or "").strip()[:300]
    if reply.kind != "task":
        out.error = "docs-checker answered with a message, not a task"
    elif not reply.settled:
        out.error = f"no result after {timeout:.0f} s (task {reply.task_id} still {reply.state})"
    elif reply.failed:
        out.error = f"the task ended {reply.state}: {reason or 'no reason given'}"
    elif reply.waiting:
        out.error = f"the task paused for input, which a scheduled run never does: {reason or '?'}"
    else:
        out.parts, out.report = artifact_parts(result["task"])
        bad = [k for k, kind in REQUIRED_PARTS.items()
               if not isinstance(out.parts.get(k), kind) or (kind is str and not out.parts[k].strip())]
        if bad:
            said = (out.report or "").strip().replace("\n", " ")[:200]
            out.error = (f"the task completed without a report (missing {', '.join(bad)})"
                         + (f": {said}" if said else ""))
    return out


# ------------------------------------------------------------------ delivery

def finding(*, run_id: str, state: str | None, session: str | None, text: str | None = None,
            error: str | None = None) -> dict:
    """The agent-findings payload, with exactly the keys dispatch.py's _post_finding posts (n8n's
    Format Finding node reads alertname, labels, agent, text, error and ui; a non-empty error is
    rendered ":x: ... Failed: <error>")."""
    return {"rule": RULE, "alertname": ALERTNAME, "labels": {"alertname": ALERTNAME, "run": run_id},
            "agent": AGENT.split("/", 1)[1], "session": session, "state": state, "text": text,
            "ui": None, "error": error, "verdict": None}


def post_json(url: str, obj: dict, timeout: float = 30.0) -> None:
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def run(env, *, post, log, **deps) -> int:
    """One weekly run (module docstring). Whatever goes wrong, the end is visible: an exception
    nothing below foresaw is logged with its traceback and alerted (V102)."""
    try:
        return _run(env, post=post, log=log, **deps)
    except Exception as err:  # noqa: BLE001 - every failure must reach Slack, not just the log
        return crashed(env, err, post=post, log=log)


def crashed(env, err: BaseException, *, post, log) -> int:
    """Log and alert an unexpected failure. Nothing is known about how far the run got, so the
    alert says where to look; it is not claimed (the ConfigMap may be what failed)."""
    run_id = (env.get("RUN_ID") or "").strip()
    reason = f"unexpected {type(err).__name__}: {str(err)[:300]}"
    log({"event": "docs-checker-trigger", "run": run_id or None, "error": reason,
         "trace": "".join(traceback.format_exception(err))[-2000:]})
    where = (f" Job log: kubectl -n {env.get('NAMESPACE', NAMESPACE)} logs job/{run_id}" if run_id else "")
    url = env.get("N8N_WEBHOOK_BASE", N8N_BASE).rstrip("/") + "/agent-findings"
    try:
        post(url, finding(run_id=run_id, state=None, session=None,
                          error=f"the trigger failed: {reason}.{where}"))
    except Exception as perr:  # noqa: BLE001 - nothing left to try; the log has both
        log({"event": "docs-checker-trigger", "run": run_id or None, "error": f"alert not posted: {perr}"})
    return 1


def _run(env, *, kube, send, wait, post, log, now, clock=time.monotonic) -> int:
    run_id = (env.get("RUN_ID") or "").strip()
    ns = env.get("NAMESPACE", NAMESPACE)
    name = env.get("MEMORY_CONFIGMAP", MEMORY_CONFIGMAP)
    path = f"/api/v1/namespaces/{ns}/configmaps/{name}"
    url = env.get("N8N_WEBHOOK_BASE", N8N_BASE).rstrip("/") + "/agent-findings"

    def line(**kw):
        log({"event": "docs-checker-trigger", "run": run_id or None, **kw})

    def alert(error: str, *, state=None, session=None) -> bool:
        try:
            post(url, finding(run_id=run_id, state=state, session=session, error=error))
        except (OSError, ValueError) as err:
            line(error=f"alert not posted: {str(err)[:200]}", alert=error)
            return False
        line(posted="alert", alert=error)
        return True

    if not run_id:
        msg = "RUN_ID is empty (the CronJob sets it from the Job name); docs-checker was not run"
        line(error=msg)
        alert(msg)
        return 1
    try:
        store = Store(kube.get(path))
    except KubeError as err:
        msg = f"cannot read ConfigMap {ns}/{name}: {err}; docs-checker was not run"
        line(error=msg)
        alert(f"{msg}. Job log: kubectl -n {ns} logs job/{run_id}")
        return 1
    done = store.deliveries(line).get(run_id)
    if done is not None:
        if done.get("posted"):
            line(skipped="already delivered", kind=done.get("kind"))
            return 0 if done.get("kind") == "report" else 1
        line(error="an earlier pod of this run claimed its delivery but did not confirm the post: "
                   "not posting again (at most once per run); its log has the report", kind=done.get("kind"))
        return 1

    memory = store.memory(line)
    endpoint = a2a.resolve_endpoint(env.get("KAGENT_A2A_BASE", a2a.IN_CLUSTER_BASE), env.get("AGENT", AGENT))
    timeout = float(env.get("RUN_TIMEOUT", RUN_TIMEOUT))
    line(started=endpoint, memory_run=memory.get("run") if isinstance(memory, dict) else None)
    out = call_agent(endpoint, memory, send=send, wait=wait,
                     token_file=env.get("KAGENT_TOKEN_FILE", "/var/run/kagent/token"), timeout=timeout, clock=clock)
    where = f"Job log: kubectl -n {ns} logs job/{run_id}"

    if out.error:
        entry = {"kind": "alert", "posted": False, "at": now(), "task": out.task_id}
        line(error=out.error, state=out.state, session=out.session, task=out.task_id, report=out.report)
        try:
            claimed = save(kube, path, store, run_id, entry, claim=True, log=line)   # memory untouched
        except AlreadyClaimed as c:
            line(skipped="claimed by another pod of this run", kind=c.entry.get("kind"))
            return 1
        except KubeError as err:
            line(warning=f"delivery not recorded ({err}): posting the alert anyway")
            claimed = None
        posted = alert(f"{out.error}. Memory not updated. {where}"
                       + (f" (session {out.session})" if out.session else ""),
                       state=out.state, session=out.session)
        if posted and claimed is not None:
            _mark(kube, path, claimed, run_id, entry, line)
        return 1

    entry = {"kind": "report", "posted": False, "at": now(), "task": out.task_id}
    snapshot = out.parts[MEMORY]
    unsaved = snapshot_problem(snapshot)       # why the new snapshot is not saved, or None
    if unsaved is None:
        stored = run_counter(memory)
        expected = 1 if stored is None else stored + 1
        if run_counter(snapshot) != expected:
            line(warning=f"memory run counter {snapshot.get('run')} after stored {stored} (expected "
                         f"{expected}): docs-checker did not use the stored snapshot (invalid, oversize or "
                         "another schema) and started fresh")
    try:
        if unsaved is None:
            try:
                claimed = save(kube, path, store, run_id, entry, memory=snapshot, claim=True, log=line)
            except TooLarge as big:
                unsaved = f"{big.size} bytes of ConfigMap data, over the {MEMORY_BUDGET}-byte budget"
            except KubeError as err:
                if err.status != 422:
                    raise
                unsaved = f"the API server refused it ({err}; {len(encode(snapshot).encode())} bytes)"
        if unsaved is not None:
            # The report is complete; only the memory is lost. Claim alone, keep the old memory.
            line(warning=f"memory not saved: {unsaved}; the stored memory is kept, the report still goes out")
            claimed = save(kube, path, store, run_id, entry, claim=True, log=line)
    except AlreadyClaimed as c:
        line(skipped="claimed by another pod of this run", kind=c.entry.get("kind"))
        return 0 if c.entry.get("posted") and c.entry.get("kind") == "report" else 1
    except KubeError as err:
        msg = f"the delivery could not be recorded ({err}), so the report is not posted"
        line(error=msg, report=out.report, slack=out.parts[SLACK])
        alert(f"{msg}; it is in the job log. Memory not updated. {where}", state=out.state, session=out.session)
        return 1
    # The full report: the digest's footer sends readers here (and to the A2A task).
    line(report=out.report, task=out.task_id, session=out.session, findings=len(out.parts[FINDINGS]),
         memory_run=snapshot.get("run") if isinstance(snapshot, dict) else None, memory_saved=unsaved is None)
    try:
        post(url, finding(run_id=run_id, state=out.state, session=out.session, text=out.parts[SLACK]))
    except (OSError, ValueError) as err:
        line(error=f"report not posted: {str(err)[:200]}; the delivery is claimed, so no retry posts it "
                   "(at most once per run): the report is in this log", slack=out.parts[SLACK])
        return 1
    if unsaved is not None:
        alert(f"memory not saved: {unsaved}. The report was posted; the previous memory is kept, so the "
              f"next run re-checks what this one learned. Reset if it repeats (kagent runbook). {where}",
              state=out.state, session=out.session)
    _mark(kube, path, claimed, run_id, entry, line)
    line(posted="report")
    return 0


def _mark(kube, path, store: Store, run_id: str, entry: dict, line) -> None:
    """Record that the post went out (memory untouched). Losing this write only makes a retry
    stop at "not confirmed" instead of "already delivered": it never posts twice."""
    try:
        save(kube, path, store, run_id, {**entry, "posted": True}, claim=False, log=line)
    except KubeError as err:
        line(warning=f"posted, but not marked posted: {err}")


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def log_line(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True), flush=True)


def main(env=None, *, post=post_json, log=log_line) -> int:
    env = os.environ if env is None else env
    try:
        kube = Kube(env.get("KUBE_API", "https://kubernetes.default.svc"),
                    Path(env.get("KUBE_TOKEN_FILE", SA_TOKEN)).read_text().strip(),
                    env.get("KUBE_CA_FILE", SA_CA))
    except Exception as err:  # noqa: BLE001 - no token or CA: still a visible failure (V102)
        return crashed(env, err, post=post, log=log)
    return run(env, kube=kube, send=send_request, wait=wait_task, post=post, log=log, now=utc_now)


if __name__ == "__main__":
    raise SystemExit(main())
