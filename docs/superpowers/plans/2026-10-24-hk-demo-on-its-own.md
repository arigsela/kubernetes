# hk demo "On its own" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second tab on the hk demo page that replays an alert the platform fixed by itself (Grafana → dispatcher → implementer → PR → Slack → a human), plus a demo workload and a Start button that make a real run on demand.

**Architecture:** The dispatcher's log lines gain the join keys the page needs, and never the answer text. A new stdlib service, `alert-demo`, holds 92% of its memory limit on request, so the real "Container near memory limit" alert fires. hk-demo reads the dispatcher's lines from Loki, the implementer's trace from Tempo, the memory series from Prometheus and the PR from GitHub. It renders ten boxes and a journey with the same evidence rules as the "Ask hk" tab.

**Tech Stack:**
- **kubernetes repo:** Python 3.12 stdlib for the dispatcher and alert-demo, run from generated ConfigMaps; Argo CD; Istio ambient.
- **hk-demo:** FastAPI, httpx, vanilla JS (no build).

**Spec:** `docs/superpowers/specs/2026-10-24-hk-demo-on-its-own-design.md`. Read it first; this plan argues from it.

**Repos:**
- **K** = `~/git/kubernetes` (`arigsela/kubernetes`). Rebase merges only. Conventional Commits with a scope. Every PR lists its validation (AGENTS.md).
- **H** = `~/git/hk-demo` (`arigsela/hk-demo`). Run `uv run pytest` and `uv run ruff check .`. A release is a `vX.Y.Z` tag on main (README "Releasing").

## Global Constraints

- **Evidence:** a ✓ only from this run's own log lines, spans or metric samples; everything else is ⚙ (§V.107). A step or box with no evidence says so in words.
- **Masking:** every string the page receives passes `mask_text` / `box_to_json`, and log lines are cut at 600 characters.
- **Answer text:** the dispatcher never logs it (dispatcher design 2026-10-12, `tests/dispatch/test_dispatch.py`: `assert all("text" not in x for x in fake.logged)` stays). The page shows the PR's title and body instead.
- **n8n payload:** the dispatcher's payload is unchanged. V102's test compares docs-checker-trigger's payload keys with it.
- **Loki:**
  - substring filters before any regex, and windows of minutes around a run;
  - the dispatcher's 7-day rows are read at most every 10 s (`ROWS_TTL = 10.0`).
- **GitHub:** unauthenticated `https://api.github.com`, repo `arigsela/kubernetes`, author `arigsela-implementer[bot]`. The PR list and single PRs are cached 300 s and the open count 60 s. The limit is 60 requests an hour per egress IP, shared with the dispatcher.
- **hk-demo:** no cluster API writes, no new RBAC. Start is an HTTP POST to `http://alert-demo.alert-demo.svc.cluster.local:8080/fill`.
- **alert-demo:**
  - Python 3.12 stdlib only;
  - fills until the container holds a fixed `FILL_MIB = 236`, set by env in the manifest next to the 256Mi limit, of which it is 92%; it never tracks the limit (spec, "Why a fixed size");
  - holds for `HOLD_SECONDS = 1800`, then releases;
  - has no liveness probe;
  - resources: memory limit 256Mi, request 64Mi; cpu 100m limit, 10m request;
  - pod label `app.kubernetes.io/name: alert-demo` (admission `require-labels`).
- **Demo timing:** the alert is expected about `DEMO_FIRE_AFTER = 16 min` after the fill (15 m `for`, 1 m evaluation, 30 s `group_wait`). The dispatcher repeats the same pod only after `REPEAT_AFTER = 6 h` and skips at `OPEN_PRS_MAX = 3` open implementer PRs.
- **Names:** app and namespace `alert-demo`; tab label "On its own". Box ids, titles and groups:

  | Group | Box id: title |
  |---|---|
  | `trigger` | `alert`: "Grafana alert"; `dispatcher`: "Dispatcher" |
  | `platform` | `identity`: "Identity check"; `kagent`: "kagent · implementer"; `sandbox`: "Sandbox" |
  | `outside` | `model`: "Model gateway"; `tools`: "Tools · GitHub" |
  | `outcome` | `pr`: "Pull request"; `slack`: "Slack"; `human`: "Human decision" |

## Review Focus

1. **A run opened in the seconds after its finding, before Tempo has the trace.** The alert and dispatcher boxes fill at once. The trace boxes wait, polling every 3 s for at most 120 s, then say the trace was not found. (Task 7 test `test_events_wait_for_a_late_trace`.)
2. **GitHub down or rate-limited (403, `x-ratelimit-remaining: 0`).** The list still renders with `pr: null`. The PR and Human boxes say "GitHub did not answer". Start is allowed, with the open count shown as unknown, matching the dispatcher, which proceeds when its count fails. (Task 5 `test_rate_limited_is_unavailable`, Task 7 `test_start_allowed_when_github_is_down`.)
3. **The same pod alerting again after 6 h.** That makes two journeys, each joined to the finding between its own dispatch line and the next one. (Task 4 `test_two_investigations_of_one_fingerprint`.)
4. **A dispatch line whose finding has not arrived (the agent is still running).** It is listed as "investigating", with steps up to the dispatcher's decision and the trace boxes waiting. Nothing looks up a null session. (Task 4 `test_investigation_in_progress`, Task 7 `test_events_for_a_run_still_investigating`.)
5. **Start misuse:**
   - pressed twice: 409 "a demo run is already on since HH:MM UTC";
   - the workload is unreachable: 503;
   - the workload restarted mid-fill: status reads "idle", with no invented progress.

   (Task 7 `test_start_refusals`, `test_status_after_workload_restart`.)

---

### Task 1: The dispatcher's lines carry the page's join keys (K)

**Files:**
- Modify: `scripts/dispatch.py`. Change `_post_finding` (around l.573) and the remediate log line (around l.611), plus their callers at l.621 and l.678.
- Regenerate: `base-apps/dispatcher/code-configmap.yaml` and `deployment.yaml` `checksum/code` (`python3 scripts/gen-dispatcher-configmap.py --repo-root .`).
- Test: `tests/dispatch/test_dispatch.py`

**Interfaces:**
- Produces, for the hk-demo tasks: every `event: finding` line for an investigation or a remediation carries:
  - `fingerprint: str` (the alert's, as in its `dispatch` line);
  - `task_id: str | None`;
  - `verdict: str | None`;
  - `target: {"namespace": str|None, "pod": str|None, "container": str|None}`, taken from the alert labels.

  Every `event: remediate` line carries `fingerprint` and `session` (the investigation's session). None of these lines carries `text`.

- [ ] **Step 1: Write the failing tests** in `tests/dispatch/test_dispatch.py`, using the file's `Fake`, `make`, `alert()` and `completed_task()` helpers.
  - `test_finding_line_carries_join_keys_not_text`:
    - The `finding` line's `fingerprint` equals the `dispatch` line's.
    - `task_id` equals the reply's task id.
    - `verdict == "limit-too-low"` for an answer ending `verdict: limit-too-low`.
    - `target == {"namespace": "n8n", "pod": "n8n-0", "container": <alert's label>}`.
    - `"text" not in line`.
  - `test_remediate_line_carries_fingerprint_and_session`: through a rule with `remediate`, the `remediate` line has `session == "sess-1"` and the alert's `fingerprint`.
  - `test_n8n_payload_keys_unchanged`: the posted finding's keys equal the set before this change. Copy the literal key set from the current `investigate()` dict.
- [ ] **Step 2: Run** `python -m pytest tests/dispatch/ -q`. Expected: the three new tests FAIL; the rest pass.
- [ ] **Step 3: Implement.** Pass the keys through `_post_finding(finding, rule, **log_extra)` from `investigate` and `remediate`; the finding dict is unchanged. Add `fingerprint` and `session` to the remediate `self.log({...})`. Take `fingerprint` from `alert.get("fingerprint") or alertname`, as `decide()` does.
- [ ] **Step 4: Regenerate and run.**
  - `python3 scripts/gen-dispatcher-configmap.py --repo-root .`
  - `python -m pytest tests/dispatch/ tests/docs_checker_trigger/ -q`
  - `python3 scripts/gen-dispatcher-configmap.py --check --repo-root .`

  Expected: all pass, in sync.
- [ ] **Step 5: Commit, PR, merge.** Commit as `feat(dispatcher): finding and remediate lines carry fingerprint, task, verdict and target (§T.140)`. The PR body says the n8n payload and the no-answer-text rule are unchanged. After the merge, confirm one fresh dispatcher pod (`checksum/code` rolled).

### Task 2: The alert-demo service (K)

**Files:**
- Create: `scripts/alert_demo.py`
- Test: `tests/alert_demo/test_alert_demo.py`

**Interfaces:**
- Produces the HTTP contract hk-demo uses (Task 5), on port `PORT` (8080):
  - `GET /healthz`: 200 `{"ok": true}`.
  - `GET /status`: 200 with this shape:
    ```
    {"state": "idle"|"holding", "started": iso|None, "release_at": iso|None,
     "held_bytes": int, "target_bytes": int|None, "limit_bytes": int|None,
     "usage_bytes": int, "percent": float|None, "pod": str}
    ```
  - `POST /fill`: 202 with the status; 409 `{"error": "already holding since <iso>"}`; 409 `{"error": "no memory limit"}`; or 409 `{"error": "the fill would reach the memory limit"}` when `target >= limit`.
  - `POST /stop`: 200 with the status.
  - Anything else: 404.
- Python functions:
  - `read_cgroup(root: Path) -> tuple[int | None, int]`: `memory.max` (None for `max`) and `memory.current`.
  - `bytes_to_fill(target: int, usage: int) -> int`: `max(0, target - usage)`.
  - `class Filler(*, cgroup: Callable[[], tuple[int|None, int]], clock: Callable[[], float], timer_factory, hold: float, target: int, chunk: int = 4 << 20, log: Callable[[dict], None])`, with methods `fill() -> tuple[int, dict]`, `stop() -> dict` and `status() -> dict`. Here `target` is `FILL_MIB << 20`, and `percent` in the status is `usage / limit * 100`.
  - `make_server(filler: Filler, port: int) -> ThreadingHTTPServer`.

- [ ] **Step 1: Write failing tests.** Load the script by path, as `tests/dispatch/test_dispatch.py` loads `dispatch.py`. Use fake cgroup, clock and timer.
  - `test_bytes_to_fill`: `bytes_to_fill(236<<20, 20<<20) == 216<<20`, and `bytes_to_fill(100, 200) == 0`.
  - `test_fill_refuses_at_or_over_the_limit`: `target = 256<<20` with `limit = 256<<20` gives 409.
  - `test_fill_does_not_track_the_limit`: with limit 384Mi, a fill still holds about 236Mi in total.
  - `test_read_cgroup_max_is_none`: from a tmp dir.
  - `test_fill_holds_then_releases`:
    - `fill()` returns 202 and `status()["state"] == "holding"`;
    - `held_bytes` is the planned amount rounded down to whole chunks;
    - firing the fake timer gives `"idle"` and `held_bytes == 0`;
    - two log dicts are written with `event == "alert-demo"` and `state` `holding` then `idle`.
  - `test_second_fill_is_409` and `test_no_limit_is_409`.
  - `test_http_routes`: a real `make_server` on port 0, in a thread, exercising every route and a 404.
- [ ] **Step 2: Run** `python -m pytest tests/alert_demo/ -q`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** `scripts/alert_demo.py`.
  - **Allocation:** `bytearray(chunk)` per chunk, writing one byte every 4096 so the pages are resident. Keep the list on the `Filler`; release by dropping it.
  - **Release:** the timer comes from `timer_factory(hold, self.stop)`.
  - **Logs:** one JSON line per state change, through `log`. `main()` reads `PORT`, `FILL_MIB` and `HOLD_SECONDS` from the environment, logs with `print(json.dumps(...), flush=True)`, and `pod` = `socket.gethostname()`.
  - **Docstring:** say what it is: the hk demo's workload, which holds memory on purpose so that "Container near memory limit" fires.
- [ ] **Step 4: Run** `python -m pytest tests/alert_demo/ -q`. Expected: PASS.
- [ ] **Step 5: Commit** as `feat(alert-demo): a service that holds 92% of its memory limit on request (§T.140)`. It ships in Task 3's PR.

### Task 3: The alert-demo app in the cluster (K)

**Files:**
- Create:
  - `scripts/gen-alert-demo-configmap.py`, a copy of `gen-dispatcher-configmap.py`'s pattern with one source, `alert_demo.py`, writing `checksum/code`.
  - `base-apps/alert-demo.yaml`, the Application, as `base-apps/slack-bridge.yaml` (with `directory.exclude`).
  - In `base-apps/alert-demo/`:
    - `namespace.yaml` (ambient, `app.kubernetes.io/part-of: agent-guardrails`);
    - `serviceaccount.yaml` (`alert-demo`);
    - `deployment.yaml` (one replica, `Recreate`, the dispatcher's `python:3.12-slim@sha256:a6e34c59…` image, `command: ["python","-u","/opt/alert-demo/alert_demo.py"]`, readiness `httpGet /healthz`, **no** liveness probe and a comment saying why, the resources from Global Constraints, the dispatcher's `securityContext`, `automountServiceAccountToken: false`);
    - `service.yaml` (8080);
    - `code-configmap.yaml` (generated);
    - `network-policy.yaml`: ingress only from namespace `hk-demo`, pod `app: hk-demo`, on 8080 and 15008, with the same HBONE comment as the dispatcher's;
    - `authorization-policy.yaml`: ALLOW principal `cluster.local/ns/hk-demo/sa/hk-demo` to port 8080, with nothing else;
    - `docs.md`, `runbook.md`, `catalog-info.yaml`, following `templates/agent-docs/README.md`.
  - Tests: `tests/alert_demo/test_manifests.py` and `tests/alert_demo/test_gen_configmap.py`.
- Modify:
  - `.github/workflows/validate.yaml`: a job `alert-demo-validate` running `pytest tests/alert_demo/ -q` and the generator `--check`;
  - `AGENTS.md`: the suite table and the generator table;
  - the generated files: `base-apps/index.md`, `scripts/agent-docs-scope.txt`, `base-apps/alert-demo/mkdocs.yml` and `docs/`.

**Interfaces:**
- Produces Service `alert-demo.alert-demo.svc.cluster.local:8080`, reachable only from hk-demo's principal.

- [ ] **Step 1: Write failing manifest tests.**
  - `test_no_liveness_probe_and_limits`: the container has `readinessProbe`, has no `livenessProbe`, limits `memory: 256Mi`, request `64Mi`.
  - `test_checksum_matches_code`: the `checksum/code` annotation equals the sha256 the generator computes.
  - `test_only_hk_demo_reaches_it`:
    - the NetworkPolicy has one ingress rule, from `hk-demo`/`app: hk-demo`, on ports {8080, 15008};
    - the AuthorizationPolicy has one rule, with principals `["cluster.local/ns/hk-demo/sa/hk-demo"]` and ports `["8080"]`.
  - `test_generated_configmap_is_in_sync`: as the dispatcher's.
- [ ] **Step 2: Run** `python -m pytest tests/alert_demo/ -q`. Expected: the manifest tests FAIL.
- [ ] **Step 3: Write the manifests, generator and docs.**
  - **`docs.md`** says plainly: a demo workload for the hk demo page's "On its own" tab; idle unless hk-demo's Start asks; while a demo run is on, it holds `FILL_MIB` (236Mi) for 30 minutes, so the hk demo can show an alert handled end to end.
  - **The `FILL_MIB` env comment** says only what it is: "the memory a demo run holds (hk demo, On its own)".
  - **`runbook.md`:**
    - `POST /stop` through the hk demo, or roll the pod, to end a run early;
    - close the demo PR afterwards;
    - a merged demo PR raises the limit by half, so the next run stays under 90% until a PR puts the limit back.
- [ ] **Step 4: Validate.**
  - `python -m pytest tests/alert_demo/ tests/agent-docs/ tests/okf/ tests/techdocs/ tests/catalog-refs/ -q`
  - `python3 scripts/gen-okf.py --repo-root . --check`
  - `python3 scripts/gen-techdocs.py --repo-root . --check`
  - `python3 scripts/validate-agent-docs.py --repo-root .`
  - yamllint and kubeconform on `base-apps/alert-demo/*.yaml base-apps/alert-demo.yaml`

  Expected: all pass.
- [ ] **Step 5: Commit, PR (Tasks 2 + 3), merge.**
  - Commit as `feat(alert-demo): demo workload for the hk demo's On its own tab (§T.140)`. The PR body names the side effects (it never alerts unless started).
  - After the merge, check the pod: `kubectl -n alert-demo get pods`, and `kubectl -n alert-demo exec deploy/alert-demo -- python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/status').read())"`. The second must print `"state": "idle"`.

### Task 4: Journey records from the dispatcher's lines (H)

**Files:**
- Modify:
  - `src/hk_demo/models.py`: `Target`, `JourneyRecord`, `PullRequest`;
  - `src/hk_demo/sources.py`: new readers;
  - `tests/fakes.py`: `FakeObs` gains the new methods, and `waypoint_principal(start, end, source=...)`.
- Create: `src/hk_demo/journeys.py` (`merge_journeys`, constants)
- Test: `tests/test_journeys_merge.py`, `tests/test_sources.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class Target: namespace: str; pod: str; container: str
  @dataclass(frozen=True)
  class JourneyRecord:
      journey_id: str           # f"{fingerprint}-{int(fired.timestamp())}"
      rule: str; alertname: str; fingerprint: str
      fired: datetime           # the dispatch line with decision "investigate"
      open_prs: int | None
      target: Target | None
      session: str | None; task_id: str | None
      answered: datetime | None # the investigation's finding line
      state: str | None; error: str | None; verdict: str | None
      remediate: str | None     # the remediate line's decision
      restart_session: str | None; restart_state: str | None
      resolved: datetime | None
  @dataclass(frozen=True)
  class PullRequest:
      number: int; url: str; title: str; body: str; author: str; created: datetime
      state: str                # "open" | "closed"
      merged: datetime | None; merged_by: str | None; closed: datetime | None
      changed_files: int | None
  ```
- `journeys.JOURNEY_ID = re.compile(r"[0-9a-f]{8,32}-\d{9,11}")`, plus `ROWS_TTL`, `DEMO_FIRE_AFTER`, `REPEAT_AFTER` and `OPEN_PRS_MAX` as in Global Constraints.
- `merge_journeys(rows: list[tuple[datetime, dict]]) -> list[JourneyRecord]`, newest first.
- `Observability`:
  - `dispatcher_rows(now, days=7) -> list[tuple[datetime, dict]]`: three `_loki_range` reads with `strict=True`, `limit=500`, `backward`:
    - `{namespace="dispatcher", container="dispatcher"} |= "\"route\": \"agent:implementer\""`;
    - `… |= "\"event\": \"finding\""`;
    - `… |= "\"event\": \"remediate\""`.

    It parses JSON, returns the lines' own times, and raises `StoreUnavailable`.
  - `trace_by_conversation(session, start, end) -> str | None`: Tempo search `{span.gen_ai.conversation.id="<session>" && name=~"invoke_agent .*"}`, with `UUIDISH` validation.
  - `memory_ratio(target, start, end) -> list[tuple[datetime, float]]`: a Prometheus `query_range` with step 30s of `100 * max(container_memory_working_set_bytes{namespace=…,pod=…,container=…} / (container_spec_memory_limit_bytes{…} > 0))`. Validate each label against `[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?`.
  - `waypoint_principal(start, end, source="hk-demo/sa/hk-demo")`: `WAYPOINT_SELECTOR` takes `source`.

- [ ] **Step 1: Write failing tests.** Rows are `(datetime, dict)` in the dispatcher's JSON shape; each finding row carries the Task 1 keys.
  - `test_one_investigation`: dispatch (investigate, `open_prs: 0`), finding (session S, `TASK_STATE_COMPLETED`, verdict `limit-too-low`, target), remediate (`no-match`), dispatch (resolved). This gives one record:
    - `journey_id == f"{fp}-{int(t0.timestamp())}"`;
    - `session == S`, `remediate == "no-match"`, `resolved == t3`.
  - `test_two_investigations_of_one_fingerprint`: the same fingerprint at t0 and t0+7h gives two records, each with the finding that follows its own dispatch.
  - `test_investigation_in_progress`: only the dispatch line gives `session is None`, `answered is None`, `state is None`.
  - `test_skips_are_not_journeys`: a `skip-open-prs` dispatch gives nothing.
  - `test_lines_without_fingerprint_are_not_listed`: the pre-Task-1 finding shape gives an in-progress record only if a dispatch line exists. Otherwise nothing.
  - `test_restart_path`: remediate `remediate`, then a finding with `kind: remediation` and `state: TASK_STATE_INPUT_REQUIRED`, gives `restart_session` and `restart_state`.
  - In `tests/test_sources.py`, with httpx `MockTransport` as its existing tests do:
    - the three Loki queries are exactly the strings above, with no regex;
    - a Tempo search query for a session;
    - `memory_ratio` refuses a label with a quote;
    - `waypoint_principal(source="dispatcher/sa/dispatcher")` puts that principal in the selector.
- [ ] **Step 2: Run** `uv run pytest tests/test_journeys_merge.py tests/test_sources.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the models, the readers and `merge_journeys`. Join by fingerprint in time order: each investigate line takes the first matching line of each kind after it and before the next investigate line of the same fingerprint.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .`. Expected: all pass.
- [ ] **Step 5: Commit** as `feat: journey records from the dispatcher's log lines`.

### Task 5: GitHub and demo-workload clients (H)

**Files:**
- Create: `src/hk_demo/github.py`, `src/hk_demo/demo.py`
- Modify: `src/hk_demo/config.py`. Add the fields `alert_demo_url` (default `http://alert-demo.alert-demo.svc.cluster.local:8080`, env `ALERT_DEMO_URL`), `github_api` (`https://api.github.com`), `github_repo` (`arigsela/kubernetes`) and `implementer_login` (`arigsela-implementer[bot]`).
- Test: `tests/test_github.py`, `tests/test_demo.py`

**Interfaces:**
- `class GitHubUnavailable(Exception)`.
- `class GitHub(settings, http, mono=time.monotonic)`:
  - `async recent_prs() -> list[PullRequest]`: `GET /repos/{repo}/pulls?state=all&sort=created&direction=desc&per_page=50`, cached 300 s.
  - `async pr(number: int) -> PullRequest`: `GET /pulls/{n}` for `merged_by` and `changed_files`, cached 300 s.
  - `async open_count() -> int`: `GET /pulls?state=open&per_page=100`, counting `user.login == implementer_login`, cached 60 s.
  - Each raises `GitHubUnavailable` on a transport error, a non-200, or a 403 with `x-ratelimit-remaining: 0`.
  - Every request sends `Accept: application/vnd.github+json` and `User-Agent: hk-demo`, with a 10 s timeout.
- `match_pr(prs, login, start, end) -> PullRequest | None`: the earliest PR by `login` created in `[start, end]`.
- `class DemoUnavailable(Exception)`.
- `class DemoWorkload(settings, http)`:
  - `async status() -> dict`;
  - `async fill() -> tuple[int, dict]`, the workload's status code and body. Any transport error or non-JSON reply raises `DemoUnavailable`.

- [ ] **Step 1: Write failing tests** with `httpx.MockTransport` and a fake `mono`.
  - `test_recent_prs_parses_and_caches`: two calls within 300 s make one request; fields map as follows:
    - `number`, `html_url→url`, `title`, `body or ""`, `user.login→author`;
    - `created_at`, `merged_at` and `closed_at` parsed to UTC.
  - `test_rate_limited_is_unavailable`: a 403 with `x-ratelimit-remaining: 0` raises.
  - `test_open_count_counts_only_the_implementer`.
  - `test_match_pr_window`: only the implementer's PR inside the window, the earliest one.
  - `test_fill_passes_409_through` and `test_unreachable_workload_raises`.
- [ ] **Step 2: Run** `uv run pytest tests/test_github.py tests/test_demo.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement both modules and the settings.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** as `feat: GitHub and demo-workload clients`.

### Task 6: The journey's ten boxes and its steps (H)

**Files:**
- Create: `src/hk_demo/journey_boxes.py`; `tests/fixtures/trace-implementer-alert.json`, copied from `/private/tmp/claude-501/-Users-arisela-git-kubernetes/7661df1f-5d19-4b24-ba40-1b563c99cb62/scratchpad/trace-implementer-alert.json` (the 2026-10-23 run, trace `af58be68…`, already scanned for secrets).
- Modify:
  - `src/hk_demo/evidence.py`:
    - `BoxId` gains `alert`, `dispatcher`, `pr`, `slack`, `human`;
    - `Box.state` gains `"human"`;
    - `Box` gains `link_text: str = "Open in Grafana →"`;
    - `Evidence` gains `memory: list[tuple] | None = None`, `pr: PullRequest | None = None` and `github: Literal["pending","ok","unavailable"] = "pending"`.
  - `src/hk_demo/config_excerpts.yaml`: keys `alert`, `dispatcher`, `slack`, `human`, quoting `base-apps/logging/grafana-alerting.yaml` (the rule), `base-apps/dispatcher/dispatch-table.yaml` (the row and limits) and `base-apps/n8n/docs.md` (agent-findings).
- Test: `tests/test_journey_boxes.py`

**Interfaces:**
- Consumes: `JourneyRecord`, `PullRequest` (Task 4); `_identity`, `_kagent`, `_sandbox`, `_model`, `_tools` and `profile("implementer")` from `evidence.py`, unchanged.
- Produces:
  - `JOURNEY_BOX_IDS = ("alert","dispatcher","identity","kagent","sandbox","model","tools","pr","slack","human")`;
  - `build_journey_boxes(j: JourneyRecord, ev: Evidence, settings: Settings) -> list[Box]`;
  - `journey_steps(j: JourneyRecord, ev: Evidence) -> list[dict]`. Each step is `{"at": iso|None, "who": "Grafana"|"Dispatcher"|"Implementer"|"Slack"|"Human", "text": str, "boxes": [BoxId], "state": "ok"|"failed"|"waiting"|"human"}`.
  - `journey_summary(j, pr) -> dict`: one list row, `{journey_id, fired, alertname, rule, target: "ns/pod (container)"|None, verdict, state, pr: {number, state, merged}|None}`.

- [ ] **Step 1: Write failing tests** with the fixture trace, a finished `JourneyRecord` (open_prs 0, verdict `limit-too-low`, remediate `no-match`, resolved), a `PullRequest` (closed, not merged), a memory series peaking at 92.4, and the dispatcher waypoint line from the spec's evidence.
  - `test_ten_boxes_in_order`: the ids equal `JOURNEY_BOX_IDS` and the titles equal Global Constraints' table.
  - `test_alert_box`:
    - observed ✓ for the firing time and for "above 90% … peak 92%" (from `memory`);
    - observed ✓ for resolved;
    - a configured check naming the rule and "15 minutes";
    - a configured check saying Grafana keeps no alert state history here.
  - `test_dispatcher_box`: observed ✓ "0 of 3" open PRs; ✓ for the verdict with "no restart"; a configured limits check. An `error` makes the box `failed`.
  - `test_identity_box_names_the_dispatcher`: a check contains `system:serviceaccount:dispatcher:dispatcher` (from the trace's `a2a.request`).
  - `test_platform_boxes_reuse_ask_builders`: the `kagent`, `tools` and `model` checks equal what `_kagent`, `_tools` and `_model` return for the same `ev` and the implementer profile.
  - `test_pr_box_shows_title_and_body`:
    - the PR number and title are in the checks;
    - `logs` are the body's lines, masked;
    - `link_text == "Open the pull request →"` and `grafana_url == pr.url`.
  - `test_pr_box_github_unavailable`: an observed check reads "GitHub did not answer", with `ok None`.
  - `test_human_box_states`: open gives `state == "human"`; closed gives `done` with "Closed without merging"; merged gives "Merged by <login>".
  - `test_no_pr_verdict`: verdict `leak`, `restart_state` `TASK_STATE_INPUT_REQUIRED` and no PR give:
    - a `pr` check "No pull request: verdict leak";
    - a `human` box with `state == "human"` and text naming `dispatcher.arigsela.com/approvals`.
  - `test_steps_order_and_boxes`: whos in order `Grafana, Dispatcher, Implementer, Implementer, Implementer, Dispatcher, Slack, Grafana, Human`. The tool step lists `create_branch`, `create_or_update_file` and `create_pull_request` from the spans, and each step's `boxes` is a subset of `JOURNEY_BOX_IDS`.
  - `test_steps_in_progress`: no finding gives the last step `{"who": "Implementer", "state": "waiting"}`.
  - `test_canaries_never_reach_the_page`: put each `tests/canaries.py` value into the PR body and a log line; assert none appears in `json.dumps([box_to_json(b) for b in boxes] + steps)`.
- [ ] **Step 2: Run** `uv run pytest tests/test_journey_boxes.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `journey_boxes.py`.
  - **Reused boxes:** `identity`, `kagent`, `sandbox`, `model` and `tools` come from the existing builders with `profile("implementer")`. Override only `title` and `what`, since these explain the box in this story.
  - **New boxes:** `alert`, `dispatcher`, `pr`, `slack` and `human` follow the spec's evidence table. The memory series and its check exist only for rule `container-near-memory-limit`. A `pod-cpu-starved` run shows its rule's configured text and no series.
  - **Logs:** `alert` shows the dispatch lines; `dispatcher` the dispatch, finding and remediate lines; `slack` the finding line; `pr` the PR body. Rebuild each line as JSON from the row, with `separators=(", ", ": ")`.
  - **Links:** `grafana_url` is Grafana's alert rule view, `{grafana}/alerting/grafana/container-near-memory-limit/view`, for `alert`; the PR URL for `pr` and `human`; the Agent Platform dashboard (`_grafana`-style, window `fired`…`answered`) for the rest.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .`. Expected: PASS.
- [ ] **Step 5: Commit** as `feat: the journey's ten boxes and its steps`.

### Task 7: JourneyManager and the API (H)

**Files:**
- Modify:
  - `src/hk_demo/journeys.py`: `JourneyManager`, `DemoRefused`;
  - `src/hk_demo/runs.py`: `EventType` gains `"journey"`;
  - `src/hk_demo/routes.py`;
  - `src/hk_demo/app.py`: `app.state.journeys`, `deps["journeys"]`, `_real_journeys(settings)` sharing one `httpx.AsyncClient`;
  - `openapi.yaml`, `tests/test_openapi.py`.
- Test: `tests/test_journeys.py`, `tests/test_routes.py`

**Interfaces:**
- `class DemoRefused(Exception)`, with `status: int` and `reason: str`.
- `class JourneyManager(obs, github, demo, settings, *, clock, mono, sleep, log=print, poll_every=3.0, trace_cap=120.0)`:
  - `async list() -> list[dict]`: `journey_summary` rows, newest first. Rows are cached `ROWS_TTL`; the PR comes from `recent_prs()`, or `None` on `GitHubUnavailable`. It raises `StoreUnavailable`.
  - `async events(journey_id) -> AsyncIterator[ServerEvent]`, in this order:
    1. `journey` (summary and steps);
    2. `boxes` after each evidence stage, each followed by a `journey` event with updated steps;
    3. `done`.

    The stages, with window `fired - 1 min` … `(answered or now) + 2 min`:
    1. The memory series.
    2. Waypoint lines from `logs_with(WAYPOINT_LOGS, "Python-urllib", fired - 10s, (answered or now) + 2 min)`, keeping lines containing `"POST /agents/kagent/implementer "`, plus `waypoint_principal(..., source="dispatcher/sa/dispatcher")` and the totals.
    3. The trace, via `trace_by_conversation(session)`, polled every `poll_every` until `trace_cap` (only when `session`), then `get_trace`, `adapter_logs` by trace id, and `run_logs(trace_id, session, …)`.
    4. The PR, via `recent_prs` → `match_pr(prs, implementer_login, fired, (answered or now) + 1 min)` → `pr(number)`.

    An unknown id gives `failed` `{"reason": "this run is not in the dispatcher's log (7 days)"}` then `done`.
  - `async demo_status() -> dict`. It returns `{"workload": <workload status>|None, "reachable": bool, "open_prs": int|None, "last_investigated": iso|None, "can_start": bool, "reason": str|None, "alert_expected": iso|None}`, where `alert_expected` is the fill `started + DEMO_FIRE_AFTER`.
  - `async start_demo(email) -> dict`. It refuses, in this order:
    1. the workload is unreachable: `DemoRefused(503, "the demo workload did not answer")`;
    2. it is holding: 409 `"a demo run is already on since HH:MM UTC"`;
    3. `open_count() >= 3`: 409, naming the count and "close the demo pull requests first";
    4. a journey for `workload.pod` fired within `REPEAT_AFTER`: 409 `"the dispatcher looked at this pod at HH:MM UTC and repeats only after 6 h (HH:MM UTC); roll the pod or wait"`.

    `GitHubUnavailable` skips refusal 3 and sets `open_prs: None`. Otherwise it fills, logs `{"event": "hk-demo-journey-start", "email": email, "pod": …, "at": iso}` through `log`, and returns `demo_status()`.
- Routes, each 401 without a login, as `/api/ask`:
  - `GET /api/journeys`: 200 list, or 503 on `StoreUnavailable`.
  - `GET /api/journeys/demo`: 200.
  - `POST /api/journeys/demo`: 202, or `{"error": reason}` with the `DemoRefused` status.
  - `GET /api/journeys/{journey_id}/events`: SSE, as `/api/runs/{id}/events`; 404 when `JOURNEY_ID` does not match.

- [ ] **Step 1: Write failing tests** with fakes: a `FakeGitHub` (scripted PRs and an open count, or raises) and a `FakeDemo` (scripted status and fill, or raises) in `tests/fakes.py`, plus a `FakeObs` returning dispatcher rows and the fixture trace.
  - `test_list_rows_and_pr`.
  - `test_list_when_github_is_down`: `pr is None`.
  - `test_events_full_run`: the event types begin `journey, boxes, journey` and end `done`; the last `boxes` has all ten ids, and `kagent` is `done`.
  - `test_events_wait_for_a_late_trace`: `trace_by_conversation` is None twice, then an id. Sleeps are `poll_every`, and the boxes go from waiting to done.
  - `test_events_for_a_run_still_investigating`: no session, so `trace_by_conversation` is never called and the last step state is `waiting`.
  - `test_unknown_journey`.
  - `test_start_refusals`: each of the four refusals, with its status and its reason text.
  - `test_start_allowed_when_github_is_down`.
  - `test_start_logs_who`.
  - `test_status_after_workload_restart`: a status of `idle` after a start gives `can_start` true and no `alert_expected`.
  - In `tests/test_routes.py`:
    - 401s;
    - the 404 on a bad id;
    - 202, 409 and 503 mapping;
    - the SSE content type.
  - `tests/test_openapi.py`'s existing route and status checks cover the four new routes once `openapi.yaml` describes them.
- [ ] **Step 2: Run** `uv run pytest tests/test_journeys.py tests/test_routes.py tests/test_openapi.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the manager, the routes, the wiring and `openapi.yaml`: four paths, with event names and payload shapes.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .`. Expected: PASS.
- [ ] **Step 5: Commit** as `feat: the On its own API: journeys, their events, the demo trigger`.

### Task 8: The page (H)

**Files:**
- Create: `src/hk_demo/static/common.js`, `src/hk_demo/static/journeys.js`
- Modify:
  - `src/hk_demo/templates/index.html`;
  - `src/hk_demo/static/app.js`: use `common.js`, and scope every `.box` lookup to `#map`;
  - `src/hk_demo/static/app.css`;
  - `tests/test_page.py`, `tests/test_app_shell.py`.

**Interfaces:**
- `common.js` (loaded first, `defer`) defines the globals `$`, `el`, `clear`, `linkified(text)` and `renderBoxPanel(panel, box, tab, onTab, finished)`, moved from `app.js` with no change in behaviour. `renderBoxPanel` uses `box.link_text` (default "Open in Grafana →").
- `index.html`:
  - a `<nav class="views">` with `<button data-view="ask">Ask hk</button><button data-view="journeys">On its own</button>`;
  - the existing page inside `<div id="view-ask">`;
  - `<div id="view-journeys" hidden>`, containing:
    - a header with the spec's sentence;
    - `<select id="journey-pick">`;
    - `<button id="demo-start">Start a demo run</button>`;
    - `<p id="demo-status">`;
    - `<section id="jmap">` with groups `trigger`, `platform`, `outside` and `outcome` holding the ten `data-box` buttons in Global Constraints' order;
    - `<section id="jpanel">`;
    - `<ol id="journey">`.
- `journeys.js`:
  - **Tab switching:** remembered in `localStorage` key `hk-demo-view`, inside try/catch.
  - **Picking a run:** loads `/api/journeys`. Picking one opens `EventSource('/api/journeys/<id>/events')` and renders the map, panel and steps on each `journey` and `boxes` event.
  - **Steps:** clicking a step adds `lit` to its `boxes`. A PR URL in a step's text is a link (`linkified`).
  - **The Start button:** POSTs `/api/journeys/demo` and shows the `error` text on 409 or 503.
  - **Status line:** while `workload.state == "holding"` and the tab is visible, it polls `GET /api/journeys/demo` and `/api/journeys` every 15 s, and stops polling otherwise. It reads "Holding 92% of the limit since HH:MM; the alert is expected about HH:MM UTC".

- [ ] **Step 1: Update and add page tests.**
  - `test_page_has_eight_boxes_in_groups` is scoped to `id="map"`.
  - A new `test_journey_view_has_ten_boxes_in_groups` checks `id="jmap"` with the four groups and exact ids.
  - The elements `id="journey-pick"`, `id="demo-start"`, `id="demo-status"`, `id="jpanel"`, `id="journey"` and `data-view="journeys"` exist.
  - The shell loads `/static/common.js` before `app.js` and `journeys.js`.
  - The existing no-inline-script and no-`style=` tests still pass.
- [ ] **Step 2: Run** `uv run pytest tests/test_page.py tests/test_app_shell.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the template, the scripts and the CSS:
  - `.box.human` amber (`--amber`);
  - `.box.lit` an outline;
  - `.views` buttons styled as the ask form's;
  - the journey list styled as `.thread`;
  - phone width keeps working.
- [ ] **Step 4: Run** `uv run pytest -q`, then check it in a browser:
  - start `DEV_EMAIL=dev@example.com REQUIRE_EMAIL=false uv run uvicorn --factory hk_demo.app:create_app_from_env` with `LOKI_URL`, `TEMPO_URL` and `PROM_URL` pointed at port-forwards;
  - open both tabs;
  - pick a journey (one exists once Task 1 is live and an alert has fired, or after Task 11's rehearsal);
  - confirm the boxes and steps render and the "Ask hk" tab still works.
- [ ] **Step 5: Commit** as `feat: the On its own tab`.

### Task 9: Release hk-demo v0.4.0 (H)

**Files:** `pyproject.toml`, `openapi.yaml` (`info.version: 0.4.0`), and `README.md` (an "On its own" section: what it shows, the Start button, the settings `ALERT_DEMO_URL` and `GITHUB_*`).

- [ ] **Step 1:** Bump the versions and update the README. Run `uv run pytest -q && uv run ruff check . && uv run python scripts/vendor_sync.py --verify`. Expected: PASS.
- [ ] **Step 2:** Open the PR as `feat: the On its own tab: an alert the platform fixes end to end (v0.4.0)`, with the verification results in the body.
- [ ] **Step 3:** After the merge, tag `v0.4.0` on the merge commit (annotated, message `v0.4.0: the On its own tab`) and push it. Watch `release.yml` to success and record the `image: …:v0.4.0@sha256:…` line.

### Task 10: Ship it in the cluster (K)

**Files:**
- `base-apps/hk-demo/deployment.yaml`: the image is v0.4.0 tag@digest, and the env gains `ALERT_DEMO_URL`.
- `catalog/apis/hk-demo-openapi.yaml`: a copy of v0.4.0's `openapi.yaml` below the existing header.
- `base-apps/hk-demo/docs.md`: an "On its own" section linking the design, with the generated `docs/index.md`.
- `base-apps/hk-demo/runbook.md`: "Before a meeting":
  - rehearse at least 6 h before, or roll `alert-demo`;
  - press Start about 20 minutes before;
  - close the demo PR after;
  - a merged demo PR raises the limit.
- `SPEC.md`: row `T140`, citing the design and plan, and amending V107 with the second tab.

- [ ] **Step 1:** Make the edits. Then:
  - check the API copy with `diff <(cd ~/git/hk-demo && git show v0.4.0:openapi.yaml | tail -n +5) <(tail -n +6 catalog/apis/hk-demo-openapi.yaml)`;
  - run `python3 scripts/gen-techdocs.py --repo-root .`;
  - run the validators and `pytest tests/techdocs/ tests/catalog-refs/ tests/agent-docs/ -q`;
  - run yamllint and kubeconform on the Deployment.

  Expected: all pass.
- [ ] **Step 2:** Commit as `feat(hk-demo): v0.4.0, the On its own tab (§T.140)`, open the PR and merge. Confirm Argo CD shows hk-demo Synced and Healthy, with the pod on the new digest.

### Task 11: Rehearsal, end to end

- [ ] **Step 1:** On `hk-demo.arigsela.com`, open "On its own" and press Start. Expected: the status line reads "Holding … the alert is expected about HH:MM UTC".
- [ ] **Step 2:** After about 16 minutes: the Grafana alert is in `#oncall-alerts`, and the dispatcher's `dispatch` line has `investigate`.
- [ ] **Step 3:** After about 2 more minutes, the run is listed. Open it:
  - all ten boxes reach `done`, or `human` for Human decision;
  - the steps show the PR's title and body;
  - a `/api/journeys/<id>/events` body scan finds no email, IP or token (as §T.136 did).
- [ ] **Step 4:** Close the demo PR. Within 300 s of cache, the Human box says "Closed without merging". The workload releases at 30 minutes, and the alert-resolved step appears.
- [ ] **Step 5:** Record the run (journey id, PR number, times) in SPEC.md `T140` and mark it done. This is a docs-only PR.
- [ ] **If the run ends without a PR:** keep the page as it is (it shows that ending). Report the verdict and the PR-less journey to the owner, and stop. Do not reword the workload's docs or comments to get a PR; the spec forbids steering, and every retry waits 6 h or needs a pod roll.
