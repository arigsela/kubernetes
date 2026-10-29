# Bug investigator: reproduce UI bugs in a browser (§T.153) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `kagent/bug-investigator` reproduces a Taskboard UI bug in a real browser, the issue comment shows a verified screenshot of the bug, and the fix PR shows screenshots of the same steps working on the fixed code; then the same for the n8n POC.

**Architecture:** One new tool, `replay_ui_steps`, on a new stdio MCP server `taskboard-browser` (Playwright + headless Chromium, behind agentgateway like `taskboard-telemetry`). Each call starts the code that is live (a copy of the deployed commit) on a fresh seeded database, runs a list of steps written in one stable format, and returns per-step results, the page's accessibility snapshot, the app's log lines and screenshots. The agent cites a reproduction as evidence kind `ui`; the trigger's verify replays it and keeps it only if the same step fails; the screenshots go to a branch of arigsela/taskboard and into the comment. The fix PR carries the steps as `ui-tests/repros/issue-<N>.json`; Taskboard's `ui-check` workflow replays them on the PR with the same engine and posts the "after" screenshots.

**Tech Stack:** Python 3.12 (stdlib; Playwright 1.64.0 only in the engine), `mcr.microsoft.com/playwright/python:v1.64.0-noble`, agentgateway v1.6.0 (stdio → Streamable HTTP), kagent 1.0.0-alpha14 (`api.kagent.dev/v1alpha3`), GitHub Actions, GitHub REST (Git Data API), n8n 2.45.5 (last phase).

**Spec:** the **Design** section below (owner decisions 2026-10-29: kagent first, then n8n; browser tools classified `read`, pinned to one server ("option 1"); screenshots of the bug on the issue and of the fixed action on the PR ("option A"); everything else deferred to the builder).

---

## Design

### Why

The investigator answers from code, PRs, deploys, logs and metrics. A bug that lives only in the page
(a missing button, a wrong view) leaves nothing in Loki or Prometheus, so today it ends `unclear`.
The owner wants the agent to reproduce such a bug and to show "screenshots of the fixed action".

### Decisions

1. **Our own server, one tool.** Not Microsoft's Playwright MCP: its clicks name per-snapshot element
   refs that no checker can replay. `replay_ui_steps` takes a list of steps in one format (below); the
   agent explores with it, verify replays the agent's cited steps with it, and CI replays the same
   steps on the fix branch. The tool takes no URL: the server starts the app itself and aborts every
   browser request to any other origin.
2. **Class `read`, pinned (owner, option 1).** The tool clicks and types, but only inside a throwaway
   copy that one call starts and deletes. New taxonomy key `server-pinned-tools`
   (`{"replay_ui_steps": "taskboard-browser"}`): the generated admission policy and
   `validate-agent-capability.py` deny binding a pinned tool from any other server.
3. **What is replayed: the live code.** `gen-taskboard-configmap.py` writes a second ConfigMap,
   `kagent/taskboard-browser-code`, with the same files plus `.source-commit`, in the same commit as
   the deploy. The server reports that commit with every result.
4. **Image without a build.** `mcr.microsoft.com/playwright/python:v1.64.0-noble@sha256:684df257a1fa0ef3fa288239819984008f560e8f8db5b635ee9e2a50cbcd2e23`
   (index; Python 3.12.3, Chromium headless shell 1248) has no `playwright` package. An init
   container installs exactly four hash-pinned wheels into `/deps` from PyPI
   (`browser-requirements.txt`, shipped in the core ConfigMap), as the trigger downloads `gh` at a
   pinned sha256. No ECR repository, no Terraform.
5. **Never fail the agent.** A tool server down at `initialize` fails every agent bound to it. The
   server imports Playwright and launches Chromium only inside a tool call; a launch failure is a tool
   result with `isError: true`. Rollout: the server ships unbound (PR K1) and is checked with a real
   replay through a port-forward; the binding ships after (PR K2).
6. **Screenshots on GitHub.** PNGs are committed to the orphan branch `bug-investigator-evidence` of
   arigsela/taskboard through the Git Data API (`evidence.py`) and linked as
   `https://github.com/arigsela/taskboard/blob/<commit>/<path>?raw=true` (renders for anyone who can
   read the repo). Writers: the trigger (its token gains `contents: write`; the code writes only that
   ref) and the ui-check post job (`GITHUB_TOKEN`). Residual, recorded in SPEC: Taskboard is private
   on GitHub Free, so `main` has no branch protection and a contents-write token could push to it;
   deploys still need a kubernetes PR a person merges.
7. **Option A.** The investigation comment shows the reproduced bug (the failing step's screenshot
   inline, the rest folded). The fix PR gets a ui-check comment with the same steps on the branch.
8. **Fix PRs.** The trigger's fix request carries the verified steps; the implementer adds them as
   `ui-tests/repros/issue-<N>.json`. `ui-check` replays every repro on every PR (a growing UI
   regression suite) in a job with `contents: read`; a separate job with no PR code posts. A red
   ui-check stays for a person (no retry loop; follow-up).
9. **The planted rehearsal bug** is specified outside every repository the agent can read (its GitHub
   tools can read arigsela/kubernetes). Its PR and deploy follow the earlier planted bugs' pattern.
10. **n8n last.** After kagent's version is live: the JS port of the `ui` check and render section
    with parity tests, the n8n agent binds the same tool, PAT A gains Contents write (owner).

### The step format (`ui` evidence, repro files, the tool's input)

```json
{"do": "click", "target": {"role": "button", "name": "Start", "within": {"css": ".card", "text": "Write copy for the pricing page"}}, "note": "dana starts her task"}
```

- `do`: actions `click`, `fill` (`value` = text), `select` (`value` = option label), `press` (`value` =
  key), `goto` (`value` = a path starting with `/`, never `//`); checks `expect_visible`,
  `expect_absent`, `expect_text` (`value` = text the target must contain).
- `target`: exactly one of `role` (+ optional `name`, exact match), `label`, `placeholder`, `text`;
  optional `within` = `{css, text}`, which must match exactly one element. `goto` has no target.
- `value` ≤ 200 characters; `note` (optional) ≤ 200; at most 15 steps.
- Every replay: a fresh app process and database (seeded demo data), the page `/` loaded, the
  network idle; after every action the network idle again (≤ 5 s); actions and `expect_visible` /
  `expect_text` wait ≤ 3 s; `expect_absent` checks once (the page is settled). An action's target
  must match exactly one element. The first failing step ends the run.
- A `ui` evidence item = `{id, kind: "ui", ref: <one line naming the scenario>, detail, steps,
  fails_at}`. `fails_at` = the 1-based step the agent says fails on the live code; `null` = every step
  passes (for "works as documented"). Verified iff the replay's first failing step equals `fails_at`
  (`null`: no step fails).
- A repro file (`ui-tests/repros/*.json`) = `{"title": str, "issue": int | null, "steps": [...]}`;
  every step must pass.

## Global Constraints

- Playwright `1.64.0` everywhere (pip and image tag `v1.64.0-noble`); image pinned by the index digest above.
- `browser-requirements.txt` (exact, x86_64 + pure wheels, `--require-hashes --no-deps --only-binary=:all:`):
  `playwright==1.64.0 --hash=sha256:5a59af1b230b234008524a5d42b613b233d4256f73bc1dd25bf3f11db0c81b75`,
  `greenlet==3.5.6 --hash=sha256:975736b002ed080d124cf81a79cb7e05cb26d6b3f5c7a7b651c0fcce70353aa1`,
  `pyee==14.0.0 --hash=sha256:3ac2d3229a9677f7de2c33d7f52fe25b638a46b19c413fea2edc8c6d0a644e4d`,
  `typing-extensions==4.16.0 --hash=sha256:481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8`.
- The core stays standard-library only; only `browser.py` imports Playwright, lazily, from `/deps`.
- Names: tool `replay_ui_steps`; RemoteMCPServer `taskboard-browser`; Deployment/Service/SA/ConfigMap
  `taskboard-browser-mcp`; code ConfigMap `taskboard-browser-code`; evidence branch
  `bug-investigator-evidence`; PR comment marker `<!-- bug-investigator ui-check -->`.
- Taskboard repo stays free of runtime dependencies; Playwright only in the ui-check job.
- No answer key for the planted bug in any repository. Commits: Conventional Commits with a scope,
  session trailers. Every kubernetes PR lists its validation and its deploy/secret impact.
- SPEC: this work is §T.153 with invariant §V.113.

## Review Focus

1. A target that matches two elements (two cards share a word) → the step fails "matched 2", never clicks the first. (Task A2)
2. An `expect_absent` that runs before the board has rendered → passes falsely; replay waits for network idle after load and after every action. (Task A2, delayed-render fixture)
3. Steps that try to leave the app (`goto` `//evil.example`, a link to another origin) → refused by validation, and any other-origin request is aborted. (Tasks A1, A2)
4. Chromium cannot start on a node (CPU, libraries) → `initialize` and `tools/list` still answer; the call returns `isError` with the launch error. (Task A3)
5. Screenshot upload fails (no contents write, a racing writer) → the comment is still posted, without images, and the failure is logged; one retry on a non-fast-forward. (Tasks A5, D1)

---

## Phase A — the core (arigsela/claude-agents, branch `feat/bug-investigator-ui-repro`, one PR)

Work in `bug-investigator/`. Tests: `uv run pytest -q` (stdlib tests). Browser tests carry
`@pytest.mark.browser` and skip without Playwright; run them in the image:
`docker run --rm -v "$PWD":/w -w /w mcr.microsoft.com/playwright/python:v1.64.0-noble sh -c 'pip install -q playwright==1.64.0 pytest && python -m pytest -q -m browser'`.

### Task A1: the step model (`steps.py`, no Playwright)

**Files:** Create `src/bug_investigator/steps.py`, `tests/test_steps.py`. Modify `pyproject.toml` (register marker `browser`).

**Interfaces — Produces:**
- `ACTIONS = ("click", "fill", "select", "press", "goto")`, `CHECKS = ("expect_visible", "expect_absent", "expect_text")`, `MAX_STEPS = 15`, `MAX_TEXT = 200`
- `validate(steps, fails_at=None) -> list[str]` (problems, `[]` when valid; `fails_at` must be `None` or an int in 1..len(steps))
- `describe(step: dict) -> str` (one plain sentence, e.g. `click button "Done" in .card "Update the cookie banner copy"`)
- `load_repro(path) -> dict` (a repro file, validated; raises `ValueError` with the problems)

- [ ] **Step 1: Failing tests** — `test_valid_click_with_within`, `test_target_needs_exactly_one_locator` (`{"role": "button", "label": "x"}` → problem), `test_goto_rejects_other_origins` (`//evil.example`, `https://x`, `evil` → problems; `/` ok), `test_limits` (16 steps, 201-char value → problems), `test_fails_at_range` (0, len+1, `"2"` → problems; None ok), `test_unknown_keys_rejected`, `test_describe_sentences` (one exact string per `do`), `test_load_repro_requires_title_and_steps`.
- [ ] **Step 2:** `uv run pytest tests/test_steps.py -q` → FAIL (no module).
- [ ] **Step 3:** Implement `steps.py` per the format in the Design.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit `feat(bug-investigator): a step format for UI reproductions`.

### Task A2: the replay engine (`browser.py`)

**Files:** Create `src/bug_investigator/browser.py`, `tests/fixtures/ui-app/server.py` (stdlib HTTP server: a page with two cards, each with buttons; env `FIXTURE_BROKEN=1` drops one button; `FIXTURE_DELAY_MS` delays rendering; a link to `http://other.invalid/`; logs one JSON line per request to stdout), `tests/test_browser.py` (`browser` marker).

**Interfaces — Consumes:** A1. **Produces:**
- `class BrowserError(RuntimeError)` (setup failed: app did not start in 15 s, Chromium did not launch, lock not acquired in 120 s)
- `@dataclass StepResult(n: int, text: str, ok: bool, error: str | None, screenshot: bytes | None)`
- `@dataclass Replay(ok: bool, failed_at: int | None, steps: list[StepResult], snapshot: str, app_log: list[str], console: list[str], commit: str | None, seconds: float)`; `Replay.to_json(include_png=False) -> dict`
- `replay(app_dir: str, steps: list[dict], *, commit: str | None = None, shots: str = "all") -> Replay` (`shots`: `all` | `failure` (the failing or last step) | `none`)
- CLI `python3 -I browser.py replay --app-dir D --steps F [--commit SHA] --out DIR` and `python3 -I browser.py check --app-dir D --repros DIR --out DIR [--changed a.json,b.json]` (writes `DIR/<stem>/result.json`, `DIR/<stem>/step-<n>.png`, `DIR/summary.json` = `{repros: [{file, title, issue, ok, failed_at, changed}]}`; exit 1 when any repro fails)

Approach: lock `fcntl.flock` on `$TMPDIR/bug-investigator-browser.lock`; app = `[sys.executable, "server.py"]` with `cwd=app_dir` and only `PATH, HOME=<tmp>, PORT=<free>, DB_PATH=<tmp>/app.db, PYTHONDONTWRITEBYTECODE=1, TASKBOARD_COMMIT=<commit>` (Taskboard imports its siblings, so no `-I`); poll `GET /healthz` (fixture serves it); stdout+stderr read by a thread, last 50 lines kept; `sys.path` gains `$DEPS_DIR` (default `/deps`) before importing Playwright; Chromium headless, viewport 1100×760, `context.route("**/*")` aborts other origins; locators: `get_by_role(role, name=..., exact=True)`, `get_by_label(..., exact=True)`, `get_by_placeholder(..., exact=True)`, `get_by_text(...)`, `within` = `page.locator(css).filter(has_text=text)` (must count 1); snapshot = `page.locator("body").aria_snapshot()` cut to 6000 chars; everything torn down in `finally`.

- [ ] **Step 1: Failing tests** — `test_all_steps_pass_on_fixture`, `test_missing_button_fails_at_that_step` (`FIXTURE_BROKEN=1` → `failed_at == 2`, error contains `0 matches`), `test_two_matches_fail_not_click` (no `within` → error contains `matched 2`), `test_absent_waits_for_render` (`FIXTURE_DELAY_MS=800`, `expect_absent` of a button that renders → fails), `test_other_origin_request_aborted` (click the external link → page stays on the app; `console` or step error records the abort), `test_shots_modes` (`failure` → exactly one PNG; PNG magic bytes), `test_app_log_captured`, `test_app_not_starting_raises` (app dir without `server.py` → `BrowserError`), `test_check_cli_summary` (two repro files, one broken → exit 1, `summary.json` as specified).
- [ ] **Step 2:** Run in the image (command above) → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Browser tests pass in the image; `uv run pytest -q` (no Playwright) skips them.
- [ ] **Step 5:** Commit `feat(bug-investigator): replay UI steps against a throwaway copy of the app`.

### Task A3: the tool server (`browser_server.py`)

**Files:** Create `src/bug_investigator/browser_server.py`, `tests/test_browser_server.py`. Modify `src/bug_investigator/mcp_server.py` (lift the JSON-RPC loop into a reusable `Server(name, version, tools, call_tool)` whose `call_tool` may return a list of MCP content items; telemetry behaviour unchanged).

**Interfaces — Consumes:** A1, A2. **Produces:**
- `SERVER_NAME = "taskboard-browser"`, tool `replay_ui_steps`, input `{steps: array, fails_at: integer|null, screenshots: "none"|"failure"|"all"}` (`screenshots` default `failure`), annotations `readOnlyHint: true, openWorldHint: false`, description saying the copy is the live commit on a fresh database with demo data and that nothing persists.
- Result content: item 0 = text JSON `{ok, failed_at, matches_claim, commit, steps: [{n, text, ok, error}], snapshot, app_log (last 20), console}` (`matches_claim` only when `fails_at` was given); then per screenshot a text item `step <n>` followed by an `image` item (`image/png`, base64). `isError: true` for invalid steps (the `validate` problems) and for `BrowserError`.
- Env: `APP_DIR` (default `/taskboard`), commit read from `APP_DIR/.source-commit`, `DEPS_DIR`.

- [ ] **Step 1: Failing tests** (fake `replay` injected) — `test_initialize_and_list_without_playwright` (Playwright not importable → `initialize` and `tools/list` answer, list = exactly `replay_ui_steps`), `test_invalid_steps_is_error`, `test_launch_failure_is_tool_error` (fake raises `BrowserError`), `test_result_shape_and_images`, `test_matches_claim`, `test_telemetry_server_unchanged` (existing `tests/test_mcp_server.py` still passes).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** `uv run pytest -q` passes.
- [ ] **Step 5:** Commit `feat(bug-investigator): taskboard-browser, a stdio MCP server with one tool`.

### Task A4: an MCP client for verify (`mcp_client.py`)

**Files:** Create `src/bug_investigator/mcp_client.py`, `tests/test_mcp_client.py` (a threaded `http.server` fake that answers JSON or `text/event-stream`).

**Interfaces — Produces:**
- `class McpClient(url, *, timeout=120.0, opener=None)`: context manager; `call(name, args) -> dict` (the `result`: `content`, `isError`); `initialize` + `notifications/initialized` on first call, `Mcp-Session-Id` kept, `DELETE` on close.
- `replay_via_mcp(url: str, steps: list, fails_at: int | None) -> dict` → `{ok, failed_at, matches_claim, commit, steps, snapshot, app_log, console, screenshots: [(n, png_bytes)], error}` (`error` set and `ok` False when `isError`).

- [ ] **Step 1: Failing tests** — `test_session_lifecycle` (initialize, initialized, call, DELETE with the session id), `test_sse_response_parsed`, `test_is_error_surfaces`, `test_replay_via_mcp_pairs_images_with_steps`, `test_http_error_raises_with_status`.
- [ ] **Step 2–4:** FAIL → implement → pass.
- [ ] **Step 5:** Commit `feat(bug-investigator): a small Streamable HTTP MCP client`.

### Task A5: screenshots on GitHub (`evidence.py`)

**Files:** Create `src/bug_investigator/evidence.py`, `tests/test_evidence.py` (fake opener recording requests).

**Interfaces — Produces:**
- `BRANCH = "bug-investigator-evidence"`; `MARKER = "<!-- bug-investigator ui-check -->"`
- `class Uploader(repo, token, *, api="https://api.github.com", opener=None)`: `upload(files: dict[str, bytes], message: str) -> str` (one commit on `BRANCH`, created as an orphan when missing; blobs → tree (base = current tree) → commit → ref; one retry after re-reading the head on 422); `link(sha, path) -> str`. Refuses: paths not matching `^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*\.png$` or containing `..`; bytes without the PNG signature; > 2 MiB per file; > 30 files.
- CLI `python3 -I evidence.py post-pr --repo R --pr N --results DIR [--run-url URL]` (token from `GITHUB_TOKEN`): for each repro in `summary.json` with `changed: true`, uploads its PNGs under `pr-<N>/<run id or UTC stamp>/<stem>/step-<n>.png`, then edits or creates the one comment carrying `MARKER`: per repro the title, ✓/✗ per step (`describe` text, error on the failing one), the last step's screenshot inline and the others in `<details>`. No changed repro → no comment.

- [ ] **Step 1: Failing tests** — `test_upload_creates_orphan_branch`, `test_upload_appends_to_existing_branch`, `test_retry_once_on_non_fast_forward`, `test_refuses_bad_paths_and_non_png`, `test_link_format`, `test_post_pr_upserts_one_comment`, `test_post_pr_without_changed_repros_posts_nothing`, `test_token_never_in_errors`.
- [ ] **Step 2–4:** FAIL → implement → pass.
- [ ] **Step 5:** Commit `feat(bug-investigator): screenshots on an evidence branch, and the ui-check PR comment`.

### Task A6: `ui` evidence in schema, verify, render; pins; docs

**Files:** Modify `findings.schema.json`, `src/bug_investigator/verify.py`, `src/bug_investigator/render.py`, `prompt.md`, `README.md`; create `browser-requirements.txt`; tests in `tests/test_verify.py`, `tests/test_pipeline.py` (render).

**Interfaces — Consumes:** A1, A4 (`replay_via_mcp`'s dict shape). **Produces:**
- Schema: evidence `kind` enum += `ui`; evidence properties += `steps` (array of step objects as in the Design, `additionalProperties: false` at each level) and `fails_at` (`["integer", "null"]`, minimum 1).
- `Ctx(..., ui_replay: Callable[[list, int | None], dict] | None = None)`; `ctx.ui_shots: dict[str, list[tuple[int, bytes]]]`.
- `check_ui(ctx, item) -> tuple[str | None, dict]`: invalid steps → the problems; no `ui_replay` → `"the browser is not available in this run"`; replay `error` → `"replay failed: <error>"`; `fails_at` null and a step failed → `"step <k> failed: <error>"`; `fails_at` k and first failure ≠ k → `"expected step <k> to fail; <what happened>"`. Verified extra: `{commit, failed_at, steps: [{n, text, ok, error}]}`; PNGs into `ctx.ui_shots[item id]`.
- Render: a section `### Reproduced in the browser` (or `### Checked in the browser` when `fails_at` is null) after the likely cause: per verified `ui` item one line `**E<n>** <detail> — on a copy of what's live (commit `<7>`)`, the numbered steps with ✓/✗, and, when `verified.screenshots` (`[{n, url}]`) is present, the failing step's image inline and the others in `<details>`.
- `browser-requirements.txt` exactly as in Global Constraints. `prompt.md` (the Action, no browser): one line, "There is no browser in this run: never cite `ui` evidence."

- [ ] **Step 1: Failing tests** — `test_ui_verified_when_claimed_step_fails`, `test_ui_dropped_when_other_step_fails`, `test_ui_null_fails_at_needs_all_pass`, `test_ui_without_browser_dropped`, `test_ui_invalid_steps_dropped`, `test_ui_shots_kept_off_the_json` (no bytes in `verify()`'s output), `test_render_reproduced_section_with_and_without_urls`, `test_schema_accepts_ui_item` (validate one item against the schema with a minimal draft-07 check already used in tests, or structurally).
- [ ] **Step 2–4:** FAIL → implement → `uv run pytest -q` passes; browser tests pass in the image.
- [ ] **Step 5:** README section "Reproducing UI bugs (kagent)" (the tool, the format, the evidence branch, ui-check); commit `feat(bug-investigator): ui evidence, verified by replaying the steps`.
- [ ] **Step 6:** Push, open the PR (`feat(bug-investigator): reproduce UI bugs in a browser`), body lists tests and the browser-test command. The owner merges; record the merge SHA for Phases B–D.

---

## Phase B — the server, unbound (arigsela/kubernetes, PR K1)

Worktree branch `feat/bug-investigator-ui-repro`. Validation per AGENTS.md: the touched pytest
suites one at a time, `yamllint`, kubeconform, the validators, every generator `--check`.

### Task B1: classify and pin `replay_ui_steps`

**Files:** Modify `base-apps/admission-policies/agent-capability-taxonomy.yaml` (`read` += `replay_ui_steps`; new key `server-pinned-tools: |` `{"replay_ui_steps": "taskboard-browser"}` with a comment citing option 1 and §V.113), `scripts/gen-agent-capability-policy.py`, `scripts/validate-agent-capability.py` (invariant 8), regenerate `base-apps/admission-policies/agent-capability.yaml`; tests in `tests/agent-capability/` and `tests/admission-policies/` fixtures.

**Interfaces — Produces:** CEL variable `pinned` (a map) and validation
`variables.mcp.all(b, b.?tools.orValue([]).all(n, !(n in variables.pinned) || b.?server.?name.orValue('') == variables.pinned[n]))`
with message `AgentTemplate '<name>' binds <tool> from a server other than <server>: a pinned tool is read only because that one server bounds what it can touch.` The validator reports the same; a missing `server-pinned-tools` key is an error, like `read-only-servers`.

- [ ] **Step 1: Failing tests** — validator: a template binding `replay_ui_steps` from `implementer` → error; from `taskboard-browser` → ok; generator `--check` drift test; admission fixture (deny/allow pair) in `tests/admission-policies/fixtures` following the suite's pattern.
- [ ] **Step 2–4:** FAIL → implement, regenerate → `python -m pytest tests/agent-capability/ -q` and `tests/admission-policies/` (Docker) pass; `scripts/gen-agent-capability-policy.py --repo-root . --check`.
- [ ] **Step 5:** Commit `feat(admission-policies): replay_ui_steps is read, pinned to taskboard-browser (§T.153)`.

### Task B2: generators — the live code copy and the core's new data file

**Files:** Modify `scripts/gen-taskboard-configmap.py` (also writes `base-apps/kagent/taskboard-browser-code-configmap.yaml`: ConfigMap `taskboard-browser-code`, namespace `kagent`, same data plus key `.source-commit`, same annotations; and `checksum/code` + `taskboard.arigsela.com/source-commit` on the `taskboard-browser-mcp` Deployment), `scripts/gen-bug-investigator-core.py` (`DATA_FILES += bug-investigator/browser-requirements.txt`; `checksum/core` on every Deployment that mounts the core: `taskboard-telemetry-mcp` and `taskboard-browser-mcp`), `tests/taskboard/test_gen_configmap.py`, `tests/taskboard/test_bug_investigator_core.py`. Regenerate both outputs: Taskboard at the deployed commit (`d7b37f21c045fc252f4033ef10501cbd582141f8` unless a later deploy landed; take it from `base-apps/taskboard/code-configmap.yaml`), the core at Phase A's merge SHA.

- [ ] **Step 1: Failing tests** — `test_browser_copy_matches_app_configmap` (same data apart from `.source-commit`, which equals the app's source-commit annotation), `test_browser_deployment_checksum_code`, `test_core_checksum_on_both_servers`, `test_core_has_browser_requirements`.
- [ ] **Step 2–4:** FAIL → implement, regenerate (B3's Deployment must exist first: do B3 Step 3 before regenerating) → pass, `--check` clean.
- [ ] **Step 5:** Commit with B3.

### Task B3: the `taskboard-browser-mcp` server and its RemoteMCPServer

**Files:** Create `base-apps/kagent/taskboard-browser-mcp.yaml`, `base-apps/kagent/taskboard-browser-mcp-remote.yaml`. Modify `tests/agent-identity/test_mcp_server_manifests.py` (`SERVERS["taskboard-browser-mcp"] = ("taskboard-browser-mcp-remote.yaml", "taskboard-browser", None)`; `UNBOUND = {"taskboard-browser": {"replay_ui_steps"}}` used by `test_gateway_allows_exactly_the_tools_agents_bind` only while no AgentTemplate binds the server; D2 deletes it).

Shape: copy `taskboard-telemetry-mcp.yaml` (header explaining the decisions above). Differences:
- `local.yaml`: allow exactly `["replay_ui_steps"]`; stdio `python3 -I /core/src/bug_investigator/browser_server.py`; tracing `service.name: '"taskboard-browser-mcp"'`.
- init container `install-playwright`: the playwright image, `pip install --no-cache-dir --require-hashes --no-deps --only-binary=:all: --target /deps -r /core/browser-requirements.txt`; requests 50m/128Mi, limits 500m/512Mi.
- container: the playwright image; env `APP_DIR=/taskboard`, `DEPS_DIR=/deps`, `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`, `HOME=/tmp`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=/core/src:/deps`; `runAsUser: 1001`, `runAsNonRoot: true`, `allowPrivilegeEscalation: false`, drop ALL; requests 100m/384Mi, limits 1500m/1536Mi.
- volumes: config, binary, core (`bug-investigator-core`, package + `browser-requirements.txt`), `/taskboard` (`taskboard-browser-code`), `/deps` emptyDir, `/tmp` emptyDir 512Mi, `/dev/shm` emptyDir Medium Memory 256Mi.
- RemoteMCPServer: `STREAMABLE_HTTP`, `http://taskboard-browser-mcp.kagent:3000/mcp`, `timeout: 90s`, `sseReadTimeout: 5m0s`, `terminateOnClose: true`.

- [ ] **Step 1:** Add the SERVERS entry → `python -m pytest tests/agent-identity/ -q` FAILS (no file).
- [ ] **Step 2:** Write the manifests; compute `checksum/config` with the suite's `-k checksum` hint; run B2's generators.
- [ ] **Step 3:** `tests/agent-identity`, `tests/taskboard`, `tests/agent-capability` pass; yamllint + kubeconform on the new files; `scripts/validate-agent-identity.py --repo-root .`, `validate-agent-capability.py --repo-root .`.
- [ ] **Step 4:** Commit `feat(kagent): taskboard-browser, a browser that replays UI steps on a copy of live Taskboard (§T.153)`.

### Task B4: docs, SPEC, PR

**Files:** Modify `base-apps/kagent/docs.md` (bug investigator: the browser server, what it can touch, the evidence branch to come), `base-apps/kagent/runbook.md` (check a replay by hand through a port-forward with `mcp_client.replay_via_mcp`; Chromium launch failures; roll back = unbind in the template), regenerate `scripts/gen-techdocs.py --repo-root .` and `scripts/gen-okf.py --repo-root .`; `SPEC.md`: row `T153` (caveman style like its neighbours) and invariant `V113` (replay_ui_steps is read and pinned to taskboard-browser; the browser reaches only the throwaway copy; the trigger writes only the evidence ref; residual: no branch protection on Taskboard).

- [ ] **Step 1:** Write; all generators `--check` clean; the suites touched in B1–B3 pass.
- [ ] **Step 2:** Commit `docs(kagent): the taskboard-browser tool server (§T.153)`; push; open PR K1 (deploy impact: one new Deployment in `kagent`, nothing bound; secret impact: none).
- [ ] **Step 3 (after the owner merges, Argo synced):** port-forward `svc/taskboard-browser-mcp` and run one replay (dana starts her own open task) from the laptop: expect `ok: true`, `commit` = the deployed commit, a PNG. A Chromium launch error here stops the plan (CPU/libraries) before anything is bound.

---

## Phase C — Taskboard (arigsela/taskboard)

### Task C1: the ui-check workflow

**Files:** Create `.github/workflows/ui-check.yml`, `ui-tests/repros/README.md` (format, the local Docker command), `ui-tests/repros/smoke-editor-starts-own-task.json` (as dana: Start on "Write copy for the pricing page"; `expect_text` on `{"role": "status"}` value `moved to in progress`).

Workflow: on `pull_request`, `push` to `main`, `workflow_dispatch`; env `ENGINE_SHA` = Phase A's merge SHA.
- job `replay` (`permissions: contents: read`, `container:` the playwright image by digest): checkout (persist-credentials false, fetch-depth 0); checkout `arigsela/claude-agents@${ENGINE_SHA}` to `.ui-engine`; `pip install --require-hashes --no-deps --only-binary=:all: -r .ui-engine/bug-investigator/browser-requirements.txt`; changed repros = `git diff --name-only <base>...HEAD -- ui-tests/repros/*.json` on PRs; `python3 -I .ui-engine/bug-investigator/src/bug_investigator/browser.py check --app-dir taskboard --repros ui-tests/repros --out ui-out --changed ...`; upload `ui-out` (always); the step's exit code is the check.
- job `post` (`needs: replay`, `if: always() && github.event_name == 'pull_request'`, `permissions: contents: write, pull-requests: write`): no checkout of the PR; checkout the engine only; download `ui-out`; `python3 -I .ui-engine/bug-investigator/src/bug_investigator/evidence.py post-pr --repo ${{ github.repository }} --pr ${{ github.event.number }} --results ui-out --run-url <run url>`.

- [ ] **Step 1:** Run the smoke repro locally in the image against the checkout (`browser.py check`) → passes.
- [ ] **Step 2:** Commit `ci: ui-check replays UI reproductions with the bug investigator's engine`; push; PR. The PR adds the smoke repro, so it counts as changed and the post job comments: confirm the job is green and the comment's images render (the first proof that `blob/<sha>/<path>?raw=true` images show in this private repo).
- [ ] **Step 3:** The owner merges.

### Task C2: the planted rehearsal bug (owner's brief, outside the repos)

- [ ] A Taskboard PR introducing the UI-only defect described in the owner's brief, written like the earlier planted changes; owner merges.
- [ ] A kubernetes deploy PR: `scripts/gen-taskboard-configmap.py --source <taskboard checkout> --commit <merge sha> --repo-root .` (now also refreshes the browser copy); owner merges. Check the browser server reports the new commit.

---

## Phase D — bind it, verify it, show it (arigsela/kubernetes, PR K2)

### Task D1: the trigger

**Files:** Modify `scripts/bug_investigator_trigger.py`, `base-apps/kagent/bug-investigator-trigger.yaml` (GithubAccessToken `contents: write` with a comment on the residual; env `BROWSER_URL`), regenerate `base-apps/kagent/bug-investigator-trigger-code-configmap.yaml`; tests in `tests/bug_investigator_trigger/`.

**Interfaces — Consumes:** A4 `replay_via_mcp`, A5 `Uploader`, A6 `Ctx(ui_replay=...)`, `ctx.ui_shots`. **Produces:**
- `Config.browser_url = "http://taskboard-browser-mcp.kagent.svc.cluster.local:3000/mcp"` (env `BROWSER_URL`), `Config.browser_enabled = True` (env `BROWSER_ENABLED`).
- `PackageCore.verify(...)` passes `ui_replay` when enabled and keeps `ctx.ui_shots` on `self.last_shots`.
- `publish_screenshots(core, token, issue: int, verified: dict, shots: dict, run_id: str, log) -> None`: uploads `issue-<N>/<run id or UTC stamp>/<Eid>-step-<n>.png` in one commit and sets `verified.screenshots = [{n, url}]` on each `ui` item; any failure is logged as `{"event": "screenshots_failed", ...}` and the comment goes out without images.
- `fix_request` appends, when a verified `ui` item has a `fails_at`: the block `UI reproduction: add the file ui-tests/repros/issue-<N>.json with exactly this content … ui-check replays it on your branch; every step must pass with your fix.` (JSON = `{"title": <issue title>, "issue": N, "steps": <the verified steps>}`).

- [ ] **Step 1: Failing tests** — `test_verify_gets_ui_replay_when_enabled`, `test_browser_disabled_passes_none`, `test_screenshots_published_and_linked`, `test_screenshot_failure_still_posts`, `test_fix_request_carries_repro_file`, `test_token_has_contents_write` (manifest), `test_log_contract` additions for the new events.
- [ ] **Step 2–4:** FAIL → implement → `python -m pytest tests/bug_investigator_trigger/ -q` passes; `scripts/gen-bug-investigator-trigger-configmap.py --repo-root . --check`.
- [ ] **Step 5:** Commit `feat(bug-investigator): the trigger verifies UI reproductions and posts their screenshots (§T.153)`.

### Task D2: bind the tool and teach the prompt

**Files:** Modify `base-apps/kagent/agent-templates/bug-investigator.yaml` (third binding: `taskboard-browser` / `replay_ui_steps`; prompt: "Where you may look" gains the browser; "The script that checks you" gains the `ui` rule; Method gains "for a symptom in the page, read `taskboard/index.html` and `app.js`, reproduce it with `replay_ui_steps`, and cite the steps you replayed as `ui` with `fails_at`; a page-only bug leaves no telemetry, so say so instead of searching logs"; steps describe the documented behaviour so the same steps pass once fixed), `tests/agent-identity/test_mcp_server_manifests.py` (delete `UNBOUND`), the n8n generator's copied systemPrompt if `scripts/gen-n8n-bug-investigator-workflow.py --check` requires it (the n8n agent keeps its own tool list until Phase E).

- [ ] **Step 1:** Edit; `tests/agent-identity`, `tests/agent-capability`, `tests/n8n_bug_investigator`, validators, generators `--check` pass.
- [ ] **Step 2:** Commit `feat(kagent): bug-investigator reproduces page bugs with replay_ui_steps (§T.153)`.

### Task D3: eval case, docs, PR, rehearsal

**Files:** Modify `tests/eval-corpus/bug-investigator.yaml` (one entry for the rehearsal issue: its facts gathered by the core's `gather` the way the trigger does, the expected verdict, the plant PR, the file, and a `replay_ui_steps` call as markers; no answer key beyond the public PR), `base-apps/kagent/docs.md`, `runbook.md`, techdocs/okf regen, `SPEC.md` (T153 progress).

- [ ] **Step 1:** File the rehearsal issue without the `bug` label; gather its facts; write the entry; `scripts/validate-eval-corpus.py --repo-root .`; `python -m pytest tests/eval-corpus/ -q`.
- [ ] **Step 2:** Commit, push, PR K2 (impact: the agent gains a tool; the trigger's token gains contents write). Owner merges; read `eval/bug-investigator` on the merge commit.
- [ ] **Step 3:** Owner labels the rehearsal issue `bug`. Check: the comment shows "Reproduced in the browser" with the failing step's screenshot; the fix PR adds `ui-tests/repros/issue-<N>.json`; ui-check is green and its comment shows the fixed action. Record results in SPEC T153.

---

## Phase E — the n8n port (arigsela/kubernetes, PR K3)

### Task E1: `ui` evidence in the n8n workflows

**Files:** Modify `scripts/n8n-bug-investigator/lib/verify.js` (a `ui` check through one MCP session to `taskboard-browser-mcp`, the same rules and messages as `check_ui`), `lib/render.js` (the same section), `lib/settings.js` (`BROWSER_URL`; tool list += `replay_ui_steps`), the upload of screenshots with PAT A (Git Data API, the same paths and refusals as `evidence.py`), `scripts/gen-n8n-bug-investigator-workflow.py` (the agent's MCP tool node for the browser), regenerate `base-apps/n8n/bug-investigator-workflow-configmap.yaml`, the handover package (`scripts/n8n-bug-investigator/make-package.py`: optional `[browser]` settings); tests in `tests/n8n_bug_investigator/` (parity with the Python `check_ui` and render on fixtures).

- [ ] **Step 1:** Owner widens PAT A (`k8s-secrets/n8n-bug-investigator`) to Contents read and write on arigsela/taskboard.
- [ ] **Step 2: Failing parity tests** — `test_ui_check_parity` (the four outcomes of `check_ui`), `test_render_ui_section_parity`, `test_upload_paths_and_refusals`.
- [ ] **Step 3–4:** FAIL → implement → `python -m pytest tests/n8n_bug_investigator/ -q`; `gen-n8n-bug-investigator-workflow.py --check`.
- [ ] **Step 5:** Commit `feat(n8n): the bug-investigator POC reproduces page bugs too (§T.153)`; PR K3; owner merges; rehearse once on a fresh issue.
