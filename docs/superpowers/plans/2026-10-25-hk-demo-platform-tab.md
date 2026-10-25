# hk demo "The platform" tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new first tab, "The platform", walks a viewer through the whole agent platform (two ways in, the shared middle, two ways out) with each component's caption, its real configuration and where it shows up in a run. Ask hk goes back to its eight evidence boxes.

**Architecture:** Page-side plus one server change: `index` renders each component's excerpts from `config_excerpts.yaml` into the template (Jinja, autoescaped). `static/platform.js` keeps only the walkthrough: `PLATFORM` (components, steps, captions, chips, `stepAfter`), tested in Node from pytest, and its DOM wiring. `journeys.js`'s tab switch gains a third view. v0.5.0's run coupling on Ask hk is removed.

**Tech Stack:** FastAPI + Jinja, vanilla JS, pytest (+ `node`), uv.

**Spec:** `docs/superpowers/specs/2026-10-25-hk-demo-platform-tab-design.md` (this repo; supersedes the 2026-10-24 picture design).

## Global Constraints

- Server text goes into the page with `textContent` only, or with Jinja autoescape. No `innerHTML`.
- Strict CSP: no inline `<script>`, `<style>`, `style=` or `font-style=` in the template. The CSP header is unchanged (`test_csp_header_unchanged`).
- The default view names no product: none of `Istio`, `Dex`, `oauth2`, `gVisor`, `Substrate`, `agentgateway`, `kagent`, `pgvector`, `Falco`, `Tempo`, `Loki`, `OpenTelemetry`, `Anthropic`, `Grafana` outside `.tech` elements, in captions or in chips. Chip text names boxes ("kagent" is a box title on the run tabs). The chips are exempt: they repeat the run tabs' box titles.
- The platform tab carries no run data and makes no API call.
- Version **0.6.0** (hk-demo CLAUDE.md rule 8); `v0.6.0` must not be a tag before merging.
- hk-demo checks: `uv run pytest -q`, `uv run ruff check src tests scripts` (from `~/git/hk-demo`).

## Review Focus

1. A browser that saved `hk-demo-view` = `ask` or `journeys` before this release reopens on that tab. Only a browser with nothing saved opens on The platform.
2. Storage that throws (private mode): the page opens on The platform, and switching tabs still works.
3. An excerpt whose text contains `<`, `&` or quotes renders as text, not markup (autoescape).
4. Narrow screens: the three panel parts stack with no horizontal scroll, and the picture scales.
5. Keyboard: Tab reaches every component; Enter or Space selects it; the step buttons and Prev/Next are buttons.

---

### Task 1: Ask hk back to its eight boxes

**Files:** modify `~/git/hk-demo/src/hk_demo/static/app.js`, `templates/index.html`, `static/app.css` and `static/platform.js` (run logic out); tests `tests/test_page.py` and `tests/test_platform_js.py`.

- [ ] **Step 1: Failing tests.**
  - `test_ask_view_is_its_boxes_again`, in `#view-ask`:
    - `#map` carries no `hidden`;
    - none of `map-platform`, `map-boxes`, `platform-svg`;
    - the subtitle contains "click any box".
  - `test_app_js_has_no_platform_coupling`: `app.js` contains none of `platformEvent`, `renderPlatform`, `state.also`, `"Also:"`, `turn.dropped`.
  - Delete the v0.5.0 Ask-picture tests in `test_page.py`:
    - `test_page_has_the_platform_picture`
    - `test_platform_default_view_names_no_product`
    - `test_app_js_feeds_the_platform`
    - `test_app_js_shows_the_other_boxes_of_a_piece`
    - `test_platform_saves_the_view_only_from_its_switch`
    - `test_app_js_marks_a_dropped_stream`
    - `test_ask_view_tells_what_to_click`
    - `test_platform_focus_and_now_show_over_selection`
    - `test_platform_technical_names_fit_their_pieces` (Task 3 brings its own version)
    - `test_platform_css` (same)
  - Delete the run-logic tests in `test_platform_js.py`. Keep the `js()` harness.
  - Keep `test_csp_header_unchanged`.
- [ ] **Step 2:** `uv run pytest tests/test_page.py -q`. Expected: the two new tests FAIL.
- [ ] **Step 3:** Remove the Ask picture section and the switch from the template (`#map` visible), and restore the subtitle as it was at v0.4.4 (`git show v0.4.4:src/hk_demo/templates/index.html`). Remove the `app.js` hooks, `state.also`, the panel's Also line and `turn.dropped`. Remove from `app.css` the picture's run CSS (`.mapswitch`, `.piece.now`, `.piece.visited`, `.m-*`, `.panel .also`). Reduce `platform.js` to an empty `PLATFORM` shell, so the script tag and the harness keep working until Task 3.
- [ ] **Step 4:** `uv run pytest -q` and ruff. Expected: all pass.
- [ ] **Step 5:** Commit on branch `feat/platform-tab`: `refactor: Ask hk back to its eight boxes (the picture moves to its own tab)`.

### Task 2: Each component's excerpts, rendered by the server

**Files:** create `~/git/hk-demo/src/hk_demo/platform.py`; modify `src/hk_demo/routes.py` (`index`) and `src/hk_demo/config_excerpts.yaml` (new key `evaluation`); test `tests/test_platform_excerpts.py`.

**Interfaces (Produces):**
- `COMPONENTS: tuple[str, ...]` = `("people","alert","dispatcher","identity","definition","context","sandbox","model","tools","results","person","security","evaluation","observability")`.
- `component_excerpts() -> dict[str, list[ConfigExcerpt]]`: every component, following the spec's "How it is set up here" table (keys, then include/exclude by source). Uses `evidence._excerpts()`.
- `index` passes `{"excerpts": component_excerpts()}` to the template.
- New excerpt key `evaluation`, source `base-apps/kagent/eval-gate-job.yaml`: the Job's header comment (a PostSync hook that re-scores every agent after a definition change and gates automated work), plus `metadata.name` and the hook annotation.

- [ ] **Step 1: Failing tests** in `test_platform_excerpts.py`:
  - `component_excerpts()` keys == `COMPONENTS`.
  - `context`'s sources == `{"base-apps/kagent/agent-docs-mcp.yaml"}`.
  - `tools` has no agent-docs source.
  - `security`'s sources == `{"base-apps/admission-policies/agent-capability.yaml"}`, and `definition` has no such source.
  - `observability`'s sources == `{"base-apps/tracing/collector.yaml"}`, and `model` has none of it.
  - `evaluation` has one entry from `eval-gate-job.yaml` mentioning `PostSync`.
  - `results` holds `pr` + `slack`; `people` holds `login`.
  - Every component except none has at least one excerpt.
  - The rendered page holds each excerpt's text, escaped: plant `<b>&` via a monkeypatched `_excerpts` and assert `&lt;b&gt;&amp;` appears.
- [ ] **Step 2:** `uv run pytest tests/test_platform_excerpts.py -q`. Expected: FAIL (no module).
- [ ] **Step 3:** Implement `platform.py`, the YAML key and the `index` context.
- [ ] **Step 4:** `uv run pytest -q` and ruff. Expected: pass.
- [ ] **Step 5:** Commit: `feat: each platform component's configuration excerpts, rendered by the server`.

### Task 3: The platform tab

**Files:** modify `templates/index.html` (third nav button first, `#view-platform`), `static/platform.js`, `static/journeys.js` (`setView`, `savedView`) and `static/app.css`; tests `tests/test_page.py` and `tests/test_platform_js.py`.

**Interfaces:**
- `PLATFORM` = `{ PIECES` (the 14 `COMPONENTS` in step order), `STEPS` (`"all"` + `PIECES`), `CAPTIONS` (`all` + each piece, the spec's copy), `WHERE` (each piece → array of chip strings, the spec's table; `all` → `[]`), `stepAfter(step, dir) }`.
- Template, inside `#view-platform`:
  - `.platform-steps`: `button.pstep[data-step]` for `STEPS`; the labels All, People, Alert, Dispatcher, Identity, Definition, Context, Sandbox, Model, Tools, Ways out, A person, Security, Evaluation, Observability; `#platform-prev`; `#platform-next`; `#tech-names`.
  - `svg#platform-svg` with one `g.piece[data-piece][role=button][tabindex=0][aria-label]` per piece, the mockup `platform-tab.html` layout and the spec's copy.
  - `.ppanel` with `#p-what`, `#p-setup` and `#p-where`.
  - For each component, a hidden `<div class="pexcerpts" data-piece="…" hidden>` holding, per excerpt, `<p class="src">{{source}}</p><pre>{{text}}</pre>`.
- Nav: `button[data-view="platform"]` comes first, then `ask`, then `journeys`. The template shows `#view-platform` and hides the other two. `savedView()` returns `"platform"` unless `ask` or `journeys` is saved.

- [ ] **Step 1: Failing tests.**
  - `test_platform_tab_is_first_and_default`: the nav order is platform, ask, journeys; `#view-platform` has no `hidden`, and `#view-ask` and `#view-journeys` do; `journeys.js`'s `savedView` returns `"platform"` by default.
  - `test_platform_tab_has_every_component`:
    - the `data-piece` set == `COMPONENTS`;
    - each `g.piece` has `role`, `tabindex` and `aria-label`;
    - the `data-step` order == `["all", *COMPONENTS]`;
    - one `.pexcerpts` per component;
    - ids `p-what`, `p-setup`, `p-where`, `tech-names`, `platform-prev`, `platform-next`.
  - `test_platform_tab_default_view_names_no_product`: strip `.tech` texts, then check the platform section.
  - `test_platform_technical_names_fit_their_pieces`: as v0.5.0's, against the new layout.
  - `test_platform_css`: `.techon .tech`, `.piece.selected`, `.piece.dim`, `.ppanel`, `.platform svg` `width: 100%`, and a `@media` rule stacking `.ppanel`.
  - Node tests (`test_platform_js.py`):
    - `PIECES` == `COMPONENTS`;
    - `STEPS[0] == "all"`;
    - `CAPTIONS` and `WHERE` cover `STEPS`;
    - no product name in captions;
    - `stepAfter` wraps;
    - `WHERE.dispatcher` == `["On its own: Dispatcher"]`.
  - `test_platform_js_is_careful`: no `innerHTML`; `textContent`; `keydown` with `"Enter"` and `" "`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement the template, `PLATFORM`, the DOM wiring and the `setView` change.
  - DOM wiring: selecting a piece fills `#p-what` (caption), `#p-setup` (clones of that component's `.pexcerpts` children, or "No excerpt for this one yet.", or for All "Pick a component to see how it is set up here.") and `#p-where` (chips); it also dims and highlights; the tech toggle adds `techon` to the svg.
  - `setView` handles three views; `loadJourneys` runs only for journeys.
  - CSS from the mockup with `app.css` variables; `.ppanel` uses a 3-column grid, stacking under 900px.
- [ ] **Step 4:** `uv run pytest -q`, ruff, then a browser check with the fakes (`scratchpad/dev_preview.py`, port 8765).
  - The first visit opens on The platform; step through it; Dispatcher shows its rule and limits; the tech toggle works.
  - The Ask hk tab shows eight boxes and still answers; On its own still loads.
  - Reload keeps the tab; no console errors; at 600 px there's no horizontal scroll.
- [ ] **Step 5:** Commit: `feat: "The platform" tab, a walkthrough of the agent platform`.

### Task 4: Release v0.6.0

- [ ] **Step 1:** Bump `pyproject.toml` / `openapi.yaml` to 0.6.0 and run `uv lock`. In the README, replace the picture paragraph with the platform tab's. `uv run pytest -q` passes, and `git ls-remote --tags origin v0.6.0` prints nothing.
- [ ] **Step 2:** Commit (`chore: v0.6.0, the platform tab`), push, open the PR, wait for green CI, then `gh pr merge --merge`.
- [ ] **Step 3:** Follow `tag-on-merge` and `release.yml` to `v0.6.0`, and the pin PR `chore(hk-demo): pin v0.6.0`. Wait for its checks, then merge it with `--rebase --admin`.
- [ ] **Step 4:** Live check on https://hk-demo.arigsela.com: the platform tab walks through every component, the excerpts show, Ask hk has eight boxes, and On its own loads.

### Task 5: Docs in arigsela/kubernetes

- [ ] **Step 1:** Add the spec and the plan. Mark the 2026-10-24 picture design "Superseded by `2026-10-25-hk-demo-platform-tab-design.md`" at its top.
- [ ] **Step 2:** Add a SPEC.md `T142|x|…|V107` row. Replace hk-demo `docs.md`'s "The agent-platform picture" section with "The platform tab". Then run `gen-techdocs.py`, `gen-okf.py --check`, `validate-agent-docs.py` and `tests/techdocs`.
- [ ] **Step 3:** Branch `docs/hk-demo-platform-tab`; commit `docs(hk-demo): the platform tab (§T.142)`; open the PR. Docs-only, so promotion merges it; confirm it merged.
