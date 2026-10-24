# hk demo agent-platform picture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Ask hk tab opens on a picture of the agent platform that the presenter walks through, that lights up from a live run's own events, and whose pieces carry ✓ / ✗ from the run's evidence and open it when clicked.

**Architecture:** Page-side only, in `arigsela/hk-demo`. An inline SVG in `templates/index.html` (looks in `app.css`), and a new `static/platform.js`. Its pure logic (`PLATFORM`: pieces, captions, event→pieces, marks) is tested in Node's `vm` from pytest. Its DOM part is hooked into `app.js`'s existing stream handlers and renderers. No API or server change. The Boxes view stays as is, behind a Platform | Boxes switch.

**Tech Stack:** FastAPI + Jinja template, vanilla JS (no build), pytest (+ `node` for the logic tests), uv.

**Spec:** `docs/superpowers/specs/2026-10-24-hk-demo-agent-platform-picture-design.md` (this repo).

## Global Constraints

- Server text goes into the page with `textContent` only; no `innerHTML` in `platform.js`.
- Strict CSP: no inline `<script>`, no `<style>`, no `style=` or `font-style=` attribute in the template; looks are classes in `app.css`.
- Default view names no product: none of `Istio`, `Dex`, `oauth2`, `gVisor`, `Substrate`, `agentgateway`, `kagent`, `pgvector`, `Falco`, `Tempo`, `Loki`, `OpenTelemetry`, `Anthropic` outside `.tech` elements or in `PLATFORM.CAPTIONS`.
- Marks come only from the selected turn's boxes (§V.107); lit is never a ✓; nothing moves on a timer.
- The Boxes view (`#map`, `BOX_ORDER`, `light()`) is unchanged.
- Every PR bumps the version (hk-demo CLAUDE.md rule 8): this feature is **0.5.0** (`pyproject.toml`, `openapi.yaml` `info.version`, `uv lock`); `v0.5.0` must not be a tag before merging.
- hk-demo tests: `uv run pytest -q` and `uv run ruff check src tests scripts` from `~/git/hk-demo`.

## Review Focus

1. Several turns in one conversation (live or replayed): the picture shows the **selected** turn's lights and marks; selecting another turn re-renders from that turn's state.
2. A run that fails (`failed` event) or whose stream drops: no piece stays "now".
3. Keyboard: each piece is focusable and Enter/Space selects it, like the boxes.
4. Narrow screens: the SVG scales (`width: 100%; height: auto`) without a horizontal page scroll.
5. Switching to On its own and back keeps the Platform/Boxes choice; storage that throws (private mode) leaves Platform as the default.

---

### Task 1: The picture's logic (`PLATFORM`)

**Files:**
- Create: `~/git/hk-demo/src/hk_demo/static/platform.js` (logic part)
- Create: `~/git/hk-demo/tests/test_platform_js.py`

**Interfaces:**
- Produces (a top-level `const PLATFORM` in `platform.js`; no DOM access at load):
  - `PIECES`: `["callers","identity","definition","context","sandbox","security","evaluation","model","tools","approval","observability"]`
  - `STEPS`: `["all","identity","definition","context","sandbox","model","tools","approval","security","evaluation","observability"]`
  - `CONTEXT_TOOLS`: `["search_code","get_file_contents","get-catalog-entity","load_memory","save_memory"]`
  - `TRACE_BOXES`: `["identity","kagent","sandbox","model","tools"]`
  - `CAPTIONS`: `{all, callers, identity, …}`: the spec's caption copy verbatim.
  - `stepAfter(step: string, dir: 1|-1) -> string` wraps around `STEPS`.
  - `boxesFor(piece: string, agentLabel: "hk"|"implementer") -> string[]` per the spec's table (approval: `["tools"]` for implementer, `["kagent"]` for hk; observability: `[]`).
  - `piecesForEvent(type: string, data: object) -> string[]`, the "now" set:
    - `ids` → callers, identity, definition, sandbox
    - `progress` status → model
    - `progress` tool → tools (+ context for `CONTEXT_TOOLS`; + approval for `create_pull_request`)
    - `boxes` → observability
    - `answer`, `failed`, `done` → `[]`
  - `worst(states: string[]) -> string`: failed > partial > waiting > done.
  - `markFor(piece: string, turn: {agent: string, boxes: object, finished: bool} | null) -> {mark: string, note: string}`.
    - `mark` ∈ `"✓" "✗" "…" "" "⚙" "●"`.
    - `note` is "" or the spec's wording ("hk is read-only: nothing to approve", "handed to a person", "no pull request in this turn", "this answer used no docs, catalog or memory", "the run's trace was not found").
    - Tool names come from each tools-box span's `attrs["gen_ai.tool.name"]` or `name` minus `"execute_tool "`.

- [ ] **Step 1: Write the failing tests.** `tests/test_platform_js.py` runs `node -e` with `vm.createContext({})`. It loads `platform.js` into the context, evaluates an expression and parses the JSON it prints. It calls `pytest.fail` (not skip) when `shutil.which("node")` is None. Cases, each its own test:
  - `piecesForEvent`: `ids`; status; `k8s_get_events` → `["tools"]`; `search_code` → `["tools","context"]`; `create_pull_request` → `["tools","approval"]`; `boxes` → `["observability"]`; `failed` → `[]`.
  - `markFor("callers")` with app done, gateway waiting, login done → `""`. All done → `"✓"`. Login failed → `"✗"`. App partial → `"…"`.
  - `markFor("context")`:
    - tools box spans `[execute_tool search_code]` → `"✓"`.
    - `[execute_tool get-catalog-entity, error "boom"]` → `"✗"`.
    - `[execute_tool k8s_get_events]` → `"⚙"` with the no-docs note.
    - No tools box → `""`.
  - `markFor("approval")`:
    - Implementer, `create_pull_request` OK → `"●"` "handed to a person".
    - Implementer, `create_pull_request` errored → `"✗"`.
    - Implementer, no such span → `""` "no pull request in this turn".
    - hk → `"⚙"` read-only note.
  - `markFor("observability")`:
    - `finished: false` → `""`.
    - Finished with all `TRACE_BOXES` non-waiting → `"✓"`.
    - Finished with model waiting → `"…"` trace note.
  - `markFor("security"/"evaluation", null)` → `"⚙"`.
  - `markFor("identity")` follows its box's state; no turn → `""`.
  - `stepAfter("observability", 1)` → `"all"`; `stepAfter("all", -1)` → `"observability"`.
  - `Object.keys(CAPTIONS)` covers every step and `callers`.
  - No caption contains a Global Constraints product name.
  - `boxesFor("callers", "hk")` → `["app","gateway","login"]`.
- [ ] **Step 2:** `cd ~/git/hk-demo && uv run pytest tests/test_platform_js.py -q`. Expected: FAIL (`platform.js` missing).
- [ ] **Step 3:** Write `PLATFORM` in `platform.js` per the Interfaces. The DOM part (Task 3) runs only `if (typeof document !== "undefined")`.
- [ ] **Step 4:** Same command. Expected: all pass. Then `uv run pytest -q`: all pass.
- [ ] **Step 5:** Commit on branch `feat/agent-platform-picture`: `feat: the agent-platform picture's logic (marks, lights, steps)`.

### Task 2: The picture on the page

**Files:**
- Modify: `~/git/hk-demo/src/hk_demo/templates/index.html` (inside `#view-ask`, before `#map`)
- Modify: `~/git/hk-demo/src/hk_demo/static/app.css`
- Test: `~/git/hk-demo/tests/test_page.py`

**Interfaces:**
- Produces, in the template:
  - The switch: `<div class="mapswitch">` with `button#map-platform` and `button#map-boxes`.
  - `section#platform.platform` holding:
    - `div.platform-steps` of `button.step[data-step]` in `STEPS` order. Labels: All, Identity, Definition, Context, Sandbox, Model, Tools, Approval, Security, Evaluation, Observability.
    - `◀` `button#platform-prev`, `Next ▶` `button#platform-next`.
    - `input#tech-names[type=checkbox]` with its label "Show technical names".
    - `svg#platform-svg` (viewBox `0 0 1240 650`, `role="img"`).
    - `p#platform-caption`.
  - Each piece is `<g class="piece" data-piece="<id>" role="button" tabindex="0" aria-label="<title>">` containing:
    - `rect.pbox`, `text.pt` (title), `text.pd` lines, optional `g.pill` (`rect` + `text`).
    - `text.tech` (technical names) and `text.pmark` (empty).
  - Layout and copy as the approved mockup `agent-platform.html` and the spec's table. The area label is "Agent platform (in the cluster)". Italics use class `pi`.
- Produces, in CSS:
  - `.tech { visibility: hidden }` and `.techon .tech { visibility: visible }`.
  - `.piece.now` (strong blue), `.piece.visited` (soft tint), `.piece.selected` (outline), `.piece.dim` (opacity .25).
  - Mark colours `m-done` / `m-failed` / `m-partial` / `m-cfg` / `m-human`, matching the box marks.
  - `.platform svg { width: 100%; height: auto; display: block }`, and `[hidden]` respected.

- [ ] **Step 1: Write the failing tests** in `test_page.py`:
  - `test_page_has_the_platform_picture`:
    - `section(html, "platform")` precedes `id="map"`.
    - `re.findall(r'data-piece="([a-z]+)"', …)` as a set equals the 11 `PIECES`.
    - Every `<g class="piece"` tag has `role="button"`, `tabindex="0"` and `aria-label="`.
    - `data-step` order equals `STEPS`.
    - Ids `tech-names`, `map-platform`, `map-boxes`, `platform-caption`, `platform-prev`, `platform-next` are present.
  - `test_platform_default_view_names_no_product`: remove `<text class="tech"[^>]*>.*?</text>` from the platform section, then check that no Global Constraints product name remains.
  - `test_page_has_no_inline_script_or_style`: also asserts `"font-style=" not in html`.
  - `test_platform_css`: `.techon .tech`, `.piece.now`, `.piece.visited`, and `.platform svg` with `width: 100%` in `app.css`.
- [ ] **Step 2:** `uv run pytest tests/test_page.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3:** Add the markup and CSS. The template does not load `platform.js` yet (Task 3).
- [ ] **Step 4:** `uv run pytest -q`. Expected: all pass. The existing eight-box tests still pass: `#map` is untouched.
- [ ] **Step 5:** Commit: `feat: the agent-platform picture on the Ask hk tab (markup and styles)`.

### Task 3: Wire it to runs, clicks and the panel

**Files:**
- Modify: `~/git/hk-demo/src/hk_demo/static/platform.js` (DOM part)
- Modify: `~/git/hk-demo/src/hk_demo/static/app.js`
- Modify: `~/git/hk-demo/src/hk_demo/templates/index.html` (script tag)
- Test: `~/git/hk-demo/tests/test_page.py`

**Interfaces:**
- Consumes `PLATFORM` (Task 1). Consumes `app.js` globals at call time: `state` (`selected`, `box`, `tab`), `renderPanel()`, `label()`.
- Produces (globals in `platform.js`):
  - `platformEvent(turn, type, data)`. It sets `turn.pnow = PLATFORM.piecesForEvent(type, data)` and adds those to `turn.pvisited`, both arrays. `answer`, `failed` and `done` clear `pnow`. It re-renders when `turn === state.selected`.
  - `renderPlatform()`:
    - On each piece, toggles `now` / `visited` / `selected` and `dim` (dim only while `state.selected` is null and a step other than `all` is chosen).
    - `visited` shows only while the piece's mark is `""`.
    - Sets the `.pmark` text and the `m-*` class from `PLATFORM.markFor(piece, state.selected)`.
  - `selectPiece(id)`:
    - Sets the caption: `CAPTIONS[id]`, plus `" · " + note` when a turn is selected and the note is non-empty.
    - With a selected turn and boxes: `state.box = boxes[0]`, `state.also = boxes`, `renderPanel()`.
    - `observability` switches to the Boxes view.
  - `showMapView("platform"|"boxes")` toggles `#platform` / `#map` hidden and the switch buttons. It stores the choice in `localStorage` key `hk-demo-ask-map`, inside try/catch.
  - Init on `DOMContentLoaded` wires:
    - the step buttons, prev/next (`stepAfter`) and the tech toggle (class `techon` on the svg);
    - a click on each piece, and Enter/Space keydown (`preventDefault` on Space);
    - the saved view (default platform).
- Changes in `app.js`:
  - Each stream handler (`ids`, `progress`, `answer`, `boxes`, `failed`, `done`) also calls `platformEvent(turn, "<type>", data)`.
  - `renderMap()` ends with `renderPlatform()`.
  - `renderPanel()` shows, when `state.also` has more than one box, a `p.also` line "Also:" with a button per other box (its `title` from the turn's box, else the id). Clicking one sets `state.box` and calls `renderPanel()`.
  - A click on a `#map .box` sets `state.also = null`.
  - The new `index.html` script order: `common.js`, `platform.js`, `app.js`, `journeys.js`.

- [ ] **Step 1: Write the failing tests** in `test_page.py`:
  - `test_scripts_load_common_first`: the order now includes `platform.js` between `common.js` and `app.js`.
  - `test_app_js_feeds_the_platform`: `platformEvent(turn, "<t>"` appears for each of the six event types, and `renderPlatform()` is inside `renderMap`.
  - `test_platform_js_is_as_careful_as_app_js`: no `innerHTML`; `localStorage` appears only inside `try {`; `"hk-demo-ask-map"`; `keydown` with `"Enter"` and `" "`.
  - `test_app_js_shows_the_other_boxes_of_a_piece`: `state.also` and `"Also:"` in `app.js`.
- [ ] **Step 2:** `uv run pytest tests/test_page.py -q`. Expected: these FAIL.
- [ ] **Step 3:** Implement the DOM part, the `app.js` hooks and the script tag.
- [ ] **Step 4:** `uv run pytest -q` and `uv run ruff check src tests scripts`. Expected: all pass, clean.
- [ ] **Step 5:** Browser check with the fakes. Run `uv run uvicorn --factory dev_preview:app --port 8765` from the scratchpad, with `PYTHONPATH` set to hk-demo and its `src`. The preview builds `create_app` with `RunManager(FakeAgent(), FakeObs(), …)` and `REQUIRE_EMAIL=false`. Check, with no console errors:
  - The picture is the default; step through it; the tech toggle works.
  - Ask a question: pieces light, then marks appear; clicking Callers shows the "Also:" line; Observability switches to Boxes.
  - Reload: the Boxes choice is kept.
  - At a 600 px window: no horizontal scroll.
- [ ] **Step 6:** Commit: `feat: the agent-platform picture follows the run and opens its evidence`.

### Task 4: Release v0.5.0

**Files:**
- Modify: `~/git/hk-demo/pyproject.toml`, `openapi.yaml` (`info.version` 0.5.0), `uv.lock`, `README.md` (one paragraph: the Ask hk tab opens on the agent-platform picture; Platform | Boxes)

- [ ] **Step 1:** Bump the version, `uv lock`, update the README, then `uv run pytest -q`. Expected: pass. `git ls-remote --tags origin v0.5.0` prints nothing.
- [ ] **Step 2:** Commit (`chore: v0.5.0, the agent-platform picture`), push, open the PR, wait for CI green, merge (`gh pr merge --merge`).
- [ ] **Step 3:** Follow `tag-on-merge` and `release.yml` on `v0.5.0`. Expected: both green, and a pin PR `chore(hk-demo): pin v0.5.0` in arigsela/kubernetes. Wait for its checks, then merge it with `--rebase --admin`.
- [ ] **Step 4:** Verify live. The hk-demo pod runs the v0.5.0 digest; on https://hk-demo.arigsela.com the Ask hk tab opens on the picture; asking a question lights the pieces and leaves marks.

### Task 5: Docs in arigsela/kubernetes

**Files:**
- Add: this plan and its spec (already written under `docs/superpowers/`)
- Modify: `SPEC.md` (§T row `T141|x|…|V107`, caveman style), `base-apps/hk-demo/docs.md` (one line: the Ask hk tab opens on the agent-platform picture), then `python3 scripts/gen-techdocs.py --repo-root .`

- [ ] **Step 1:** Edit, regenerate, then run `python3 scripts/gen-okf.py --repo-root . --check`, `python3 scripts/validate-agent-docs.py --repo-root .` and `python3 -m pytest tests/techdocs/ -q`. Expected: in sync, passed.
- [ ] **Step 2:** Branch `docs/hk-demo-agent-platform-picture`, commit (`docs(hk-demo): the agent-platform picture (§T.141)`), PR. It is docs-only, so promotion merges it once green; confirm it merged.
