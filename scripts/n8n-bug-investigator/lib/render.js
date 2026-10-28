// scripts/n8n-bug-investigator/lib/render.js
// The n8n investigation comment (§T.152): a port of the core's render.render, relabelled as the
// POC (marker mode=n8nagent, its own title and footer lead), plus the trail of the AI Agent's
// tool calls, the contamination flag and the post. tests/n8n_bug_investigator/test_render.py
// checks the body equals the core's render of the same verified findings after the relabel.
// The relabel (marker mode, title, footer lead), the tool names the trail strips n8n's prefix from
// and the branch pattern the contamination flag watches are settings; the constants below are
// this homelab's values and the defaults.
'use strict';
const { strip, parseTime, clip, scrubText, splitLines } = require('./util');

const ZWSP = String.fromCharCode(0x200b);
const MARKER = (mode) => `<!-- bug-investigator mode=${mode} -->`;
const N8N_MODE = 'n8nagent';
const HEADERS = { full: '🔎 Bug investigation — full', n8n: '🔎 n8n-style baseline (ticket + code only)' };
const N8N_HEADER = '🔎 Bug investigation — n8n AI Agent (POC)';
const FOOTER_LEAD = 'n8n/bug-investigator-poc';
const VERDICTS = { likely_cause: 'Likely cause found', not_a_bug: 'Not a bug: works as documented',
  needs_info: 'Needs more information', unclear: 'Unclear' };
const STATUS = { supported: '✅ supported', ruled_out: '❌ ruled out', untested: '❔ untested' };
const KIND_LABEL = { logql: 'Loki', promql: 'Prometheus' };
const MAX_LEN = 60000;
const MAX_TRAIL = 40;
const TOOLS = ['get_file_contents', 'search_code', 'list_commits', 'get_commit', 'list_pull_requests', 'pull_request_read',
  'query_loki_logs', 'query_loki_stats', 'list_loki_label_names', 'list_loki_label_values', 'query_prometheus'];
const KAGENT_BRANCH_RE = /agent\/taskboard-issue-(\d+)/g;
const CODE_SPAN = /(`+)(.+?)\1/gs;

const truthy = (x) => !!x && !(Array.isArray(x) && !x.length) && !(typeof x === 'object' && !Array.isArray(x) && !Object.keys(x).length);
const pyStr = (v) => (v === null || v === undefined ? 'None' : v === true ? 'True' : v === false ? 'False' : String(v));
const isObj = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);
const pad2 = (n) => String(n).padStart(2, '0');
const all = (s, a, b) => s.split(a).join(b);

// Model-written text: no HTML, no @-mentions, no fake markers. Inside a code span `<` stays,
// except that `<!--` is broken so no span can carry a marker.
function t(s) {
  const text = strip(truthy(s) ? pyStr(s) : '');
  const out = [];
  let last = 0;
  for (const m of text.matchAll(CODE_SPAN)) {
    out.push(all(text.slice(last, m.index), '<', '&lt;'));
    out.push(all(m[0], '<!--', `<${ZWSP}!--`));
    last = m.index + m[0].length;
  }
  out.push(all(text.slice(last), '<', '&lt;'));
  return all(out.join(''), '@', `@${ZWSP}`);
}

const inline = (s) => all(all(t(s), '\r', ' '), '\n', ' ');
const cell = (s) => all(inline(s), '|', '\\|');

function when(s) {
  const d = parseTime(s);
  return d ? `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())} UTC` : t(s);
}

function hm(s) {
  const d = parseTime(s);
  return d ? `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}` : '?';
}

// A one-line code span; backticks inside become quotes so the span cannot break.
function code(s, limit = 160) {
  let v = strip(truthy(s) ? pyStr(s) : '').split(/[\t\n\x0b\x0c\r\x1c-\x1f \x85\xa0]+/).filter(Boolean).join(' ');
  v = all(v, '`', "'");
  const cps = Array.from(v);
  if (cps.length > limit) v = `${cps.slice(0, limit - 1).join('')}…`;
  return `\`${v}\``;
}

function trailLines(trail, cap = MAX_TRAIL) {
  const raw = isObj(trail) ? trail.steps : null;
  const steps = Array.isArray(raw) ? raw.filter(isObj) : [];
  if (!steps.length) return [];
  const out = [`<details><summary>How the agent worked (${steps.length} steps)</summary>`, ''];
  for (const s of steps.slice(0, cap)) {
    const parts = [];
    if (truthy(s.narration)) parts.push(inline(s.narration));
    if (truthy(s.tool)) {
      let target = code(s.input);
      const link = s.link;
      if (typeof link === 'string' && link.startsWith('https://') && ![' ', '(', ')', '<', '>', '\n'].some((c) => link.includes(c))) {
        target = `[${target}](${link})`;
      }
      if (truthy(s.window)) target += ` ${inline(s.window)}`;
      const mark = s.status === 'blocked' ? '⛔ ' : '';
      parts.push(`${mark}${inline(s.tool)}: ${target} → ${inline(s.result)}`);
    }
    const n = Number.isInteger(s.n) ? s.n : '?';
    out.push(`${n}. ${parts.join(' — ')}`);
  }
  if (steps.length > cap) out.push(`\n… ${steps.length - cap} more steps (all of them are in \`trail-<mode>.json\` in the run artifacts)`);
  out.push('', '</details>', '');
  return out;
}

function evidenceLink(e) {
  const v = e.verified || {};
  const kind = e.kind;
  const ref = String(e.ref || '');
  let label;
  if (kind === 'commit') label = `commit \`${(v.sha || ref).slice(0, 10)}\``;
  else if (kind === 'pr') label = `PR #${pyStr(v.number)}${truthy(v.title) ? ` ${cell(v.title)}` : ''}`;
  else if (kind === 'issue') label = `#${pyStr(v.number)}${truthy(v.title) ? ` ${cell(v.title)}` : ''}`;
  else if (kind === 'file' || kind === 'doc') label = `\`${pyStr(v.path)}${truthy(v.line) ? `#L${v.line}` : ''}\``;
  else if (kind === 'logql' || kind === 'promql') label = `${KIND_LABEL[kind]} query`;
  else label = cell(ref);
  return truthy(v.link) ? `[${label}](${v.link})` : label;
}

// render.render(f, mode, model, stats, runUrl, trail, fix=None), with `beforeFooter` lines
// placed right above the `---` footer separator.
function renderCore(f, mode, model = '', stats = {}, runUrl = '', trail = null, beforeFooter = []) {
  const st = stats || {};
  const v = f.verification || {};
  const ev = new Map((f.evidence || []).map((e) => [e.id, e]));
  const out = [MARKER(mode), `## ${HEADERS[mode] || HEADERS.full}`, ''];
  const verdict = VERDICTS[f.verdict] !== undefined ? VERDICTS[f.verdict] : cell(f.verdict);
  const vVerified = v.verified === undefined ? 0 : v.verified;
  const vTotal = v.total === undefined ? 0 : v.total;
  out.push(`**${verdict}** · confidence ${cell(f.confidence || '?')} · verified ${pyStr(vVerified)} of ${pyStr(vTotal)} evidence links`,
    '', t(f.summary), '');

  const lc = f.likely_cause;
  if (!truthy(lc) && f.verdict === 'likely_cause') {
    const sup = (f.hypotheses || []).filter((h) => h.status === 'supported');
    if (sup.length) {
      const ids = (sup[0].evidence_ids || []).join(', ');
      out.push('### Likely cause', '', `- ${t(sup[0].statement)}${ids ? ` (${ids})` : ''}`, '');
    }
  }
  if (truthy(lc)) {
    out.push(`### Likely cause${truthy(lc.unconfirmed) ? ' (not confirmed)' : ''}`, '');
    const cv = lc.change_verified;
    const kv = lc.code_verified;
    if (truthy(cv)) {
      const label = 'number' in cv ? `PR #${pyStr(cv.number)}${truthy(cv.title) ? ` “${cell(cv.title)}”` : ''}` : `commit \`${cv.sha.slice(0, 10)}\``;
      const live = truthy(lc.live_since) ? `, live since ≈ ${when(lc.live_since)}` : '';
      out.push(`- **Change:** [${label}](${pyStr(cv.link)})${live}`);
    }
    if (truthy(kv)) out.push(`- **Code:** [\`${pyStr(kv.path)}${truthy(kv.line) ? `#L${kv.line}` : ''}\`](${pyStr(kv.link)})`);
    out.push(`- ${t(lc.explanation)}`, '');
  }

  let dr = f.docs_rule;
  if (!truthy(dr)) {
    const doc = (f.evidence || []).find((e) => e.kind === 'doc' && truthy(e.quote) && truthy((e.verified || {}).link));
    if (doc) dr = { quote: doc.quote, rule_id: doc.id, verified: doc.verified };
  }
  if (truthy(dr)) {
    const dv = dr.verified || {};
    const quote = splitLines(strip(truthy(dr.quote) ? pyStr(dr.quote) : '')).map((x) => `> ${t(x)}`).join('\n');
    out.push('### What the docs say should happen', '', quote, '',
      `— rule **${cell(dr.rule_id)}**, [\`${pyStr(dv.path)}#L${pyStr(dv.line)}\`](${pyStr(dv.link)})`, '');
  }

  const tele = (f.evidence || []).filter((e) => e.kind === 'logql' || e.kind === 'promql');
  out.push('### What happened', '');
  if (tele.length) {
    for (const e of tele) {
      const vv = e.verified || {};
      const res = e.expect === 'none' ? 'no results (as expected)' : `${pyStr(vv.count)} results`;
      out.push(`- **${e.id}** ${t(e.detail)} — ${evidenceLink(e)} · ${res}, ${when(vv.start)}–${hm(vv.end)}`);
    }
  } else {
    out.push(mode !== 'full' ? 'No telemetry in this mode.' : 'No verified telemetry evidence.');
  }
  out.push('');

  const hyps = f.hypotheses || [];
  if (hyps.length) {
    out.push('### Hypotheses', '', '| | Hypothesis | Status | Evidence |', '|---|---|---|---|');
    for (const h of hyps) {
      const ids = (h.evidence_ids || []).filter((i) => ev.has(i)).join(', ') || '—';
      out.push(`| ${cell(h.id)} | ${cell(h.statement)} | ${STATUS[h.status] !== undefined ? STATUS[h.status] : cell(h.status)} | ${ids} |`);
    }
    out.push('');
  }

  const bugs = f.similar_past_bugs || [];
  if (bugs.length) {
    out.push('### Similar past bugs', '');
    for (const b of bugs) {
      const bv = b.verified || {};
      out.push(`- [#${pyStr(bv.number)} ${cell(bv.title)}](${pyStr(bv.link)}) — ${t(b.why)}`);
    }
    out.push('');
  }

  if (truthy(f.next_step)) out.push('### Suggested next step', '', t(f.next_step), '');
  if (truthy(f.could_not_check)) out.push("### Couldn't check", '', ...f.could_not_check.map((x) => `- ${t(x)}`), '');
  out.push(...trailLines(trail));

  const rest = (f.evidence || []).filter((e) => e.kind !== 'logql' && e.kind !== 'promql');
  if (truthy(f.evidence)) {
    out.push(`<details><summary>Evidence (${f.evidence.length} verified)</summary>`, '');
    for (const e of [...rest, ...tele]) {
      const q = truthy(e.quote) && (e.kind === 'file' || e.kind === 'doc') ? `\n  > ${inline(e.quote)}` : '';
      const c = e.kind === 'logql' || e.kind === 'promql' ? `\n  \`${inline(e.ref)}\`` : '';
      out.push(`- **${e.id}** ${evidenceLink(e)} — ${t(e.detail)}${c}${q}`);
    }
    out.push('', '</details>', '');
  }

  const rep = f.report || {};
  if (Object.values(rep).some(truthy)) {
    out.push('<details><summary>The report as understood</summary>', '',
      `- **Who:** ${t(rep.who)}`, `- **What:** ${t(rep.what)}`,
      `- **When:** ${when(rep.when_start)} – ${when(rep.when_end)}`,
      `- **Where:** ${t(rep.where)}`, `- **Expected:** ${t(rep.expected)}`,
      `- **Actual:** ${t(rep.actual)}`, '', '</details>', '');
  }

  const dropped = v.dropped || [];
  const notes = v.notes || [];
  if (dropped.length || notes.length) {
    out.push(`<details><summary>Dropped by verification (${dropped.length})</summary>`, '');
    for (const d of dropped) {
      out.push(`- ${inline(d.id)} (${inline(d.kind)} \`${inline(Array.from(pyStr(d.ref)).slice(0, 120).join(''))}\`): ${inline(d.reason)}`);
    }
    out.push(...notes.map((n) => `- ${inline(n)}`), '', '</details>', '');
  }

  const foot = [`${mode} mode`];
  if (model) foot.push(model);
  if (st.turns !== undefined && st.turns !== null) foot.push(`${st.turns} turns`);
  if (truthy(st.duration_ms)) {
    const s = Math.trunc(st.duration_ms / 1000);
    foot.push(`${Math.floor(s / 60)}m ${pad2(s % 60)}s`);
  }
  if (st.cost !== undefined && st.cost !== null) foot.push(`$${Number(st.cost).toFixed(2)}`);
  foot.push(`verified ${pyStr(vVerified)} of ${pyStr(vTotal)} evidence links`);
  if (truthy(st.denials)) foot.push(`${st.denials} tool calls blocked`);
  if (runUrl) foot.push(`[run](${runUrl})`);
  out.push(...beforeFooter, '---', `<sub>${foot.join(' · ')}</sub>`, '');
  let body = scrubText(out.join('\n'));
  const cps = Array.from(body);
  if (cps.length > MAX_LEN) body = `${cps.slice(0, MAX_LEN - 200).join('')}\n\n…(truncated; the full findings are in the run artifacts)\n`;
  return body;
}

function renderComment(verified, { model = '', stats = {}, trail = null, contaminated = [], comment = {} } = {}) {
  const c = { mode: N8N_MODE, header: N8N_HEADER, footer: FOOTER_LEAD, ...(comment || {}) };
  const f = isObj(verified) ? verified : {};
  const flag = contaminated && contaminated.length
    ? [`⚠️ While it ran, this agent read kagent's fix pull request branch(es) ${contaminated.join(', ')}; compare with care.`, '']
    : [];
  return renderCore(f, 'full', model, stats, '', trail, flag)
    .replace(MARKER('full'), MARKER(c.mode))
    .replace(`## ${HEADERS.full}`, `## ${c.header}`)
    .replace('<sub>full mode · ', `<sub>${c.footer} · `);
}

// ------------------------------------------------------------------------------------- trail

// json.dumps(value): ", " and ": " separators, every non-ASCII character escaped.
function pyDumps(v) {
  if (v === null || v === undefined) return 'null';
  if (Array.isArray(v)) return `[${v.map(pyDumps).join(', ')}]`;
  if (typeof v === 'object') return `{${Object.entries(v).map(([k, x]) => `${pyDumps(k)}: ${pyDumps(x)}`).join(', ')}}`;
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  return JSON.stringify(v).replace(/[\u0080-\uffff]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
}

// What a tool call was about, in a few words (the trigger's target_of): a path, a PR, a query.
function targetOf(args) {
  const a = isObj(args) ? args : {};
  const repo = a.repo;
  let x;
  if ((a.pullNumber !== undefined && a.pullNumber !== null) || (a.pull_number !== undefined && a.pull_number !== null)) {
    x = `#${pyStr(a.pullNumber !== undefined ? a.pullNumber : a.pull_number)}${truthy(a.method) ? ` ${a.method}` : ''}`;
  } else if (truthy(a.path)) {
    x = String(a.path);
  } else if (['logql', 'expr', 'query', 'matcher'].some((k) => truthy(a[k]))) {
    x = String(a[['logql', 'expr', 'query', 'matcher'].find((k) => truthy(a[k]))]);
    if (truthy(a.labelName)) x = `${a.labelName} ${x}`;
  } else if (truthy(a.sha)) {
    x = String(a.sha).slice(0, 12);
  } else if (truthy(a.labelName)) {
    x = `label ${a.labelName}`;
  } else {
    const sorted = Object.fromEntries(Object.keys(a).sort().filter((k) => k !== 'owner').map((k) => [k, a[k]]));
    return pyDumps(sorted);
  }
  return truthy(repo) ? `${repo}:${x}` : x;
}

// n8n prefixes each MCP tool with its node's name (Task 1); the bare name is the AgentTemplate's.
function bareTool(name, tools = TOOLS) {
  const s = String(name || '?');
  return (tools || TOOLS).find((tool) => s === tool || s.endsWith(`_${tool}`)) || s;
}

function textOf(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) return content.filter((p) => p && p.type === 'text').map((p) => p.text).join('');
  return '';
}

// An observation as text: the MCP result's text parts when it parses as
// [{"response": [{"type": "text", "text": …}]}], else the string or its JSON.
function observationText(obs) {
  let v = obs;
  if (typeof obs === 'string') {
    try { v = JSON.parse(obs); } catch (e) { return obs; }
  }
  if (Array.isArray(v) && v.every((x) => isObj(x) && Array.isArray(x.response))) return v.map((x) => textOf(x.response)).join('');
  if (Array.isArray(v) && v.every((x) => isObj(x) && 'type' in x)) return textOf(v);
  if (typeof obs === 'string') return obs;
  return obs === null || obs === undefined ? '' : JSON.stringify(obs);
}

function trailFromSteps(steps, tools = TOOLS) {
  return {
    steps: (steps || []).filter(isObj).map((s, i) => {
      const a = s.action || {};
      const ml = (a.messageLog || [])[0] || {};
      const narration = textOf(ml.content !== undefined ? ml.content : (ml.lc_kwargs || {}).content);
      const input = isObj(a.toolInput) ? Object.fromEntries(Object.entries(a.toolInput).filter(([k]) => k !== 'tool')) : a.toolInput;
      return {
        n: i + 1,
        narration: clip(narration, 300),
        tool: bareTool(a.tool, tools),
        input: clip(targetOf(input), 120),
        result: clip(observationText(s.observation), 200),
      };
    }),
  };
}

// Every branch matching `pattern` (by default kagent's fix branches, agent/taskboard-issue-<N>)
// the agent's tool calls or results named, by the number the pattern captures. A null pattern
// watches nothing.
function sawKagentWork(steps, pattern = KAGENT_BRANCH_RE.source) {
  if (!pattern) return [];
  const re = new RegExp(pattern, 'g');
  const seen = new Map();
  for (const s of steps || []) {
    if (!isObj(s)) continue;
    const a = s.action || {};
    const texts = [typeof s.observation === 'string' ? s.observation : JSON.stringify(s.observation === undefined ? null : s.observation),
      JSON.stringify(a.toolInput === undefined ? null : a.toolInput)];
    for (const text of texts) for (const m of String(text).matchAll(re)) seen.set(m[0], Number(m[1]));
  }
  return [...seen].sort((x, y) => x[1] - y[1] || (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0)).map(([name]) => name);
}

// ------------------------------------------------------------------------------------- post

// Edits the POC's own comment (its marker, written by the token's user), else creates one.
async function postComment(gh, repo, issueNumber, body, mode = N8N_MODE) {
  const me = await gh.login();
  const comments = await gh.getAll(`/repos/${repo}/issues/${issueNumber}/comments`);
  const own = comments.find((c) => String(c.body || '').includes(MARKER(mode || N8N_MODE)) && c.user && c.user.login === me);
  if (own) {
    await gh.patch(`/repos/${repo}/issues/comments/${own.id}`, { body });
    return 'edited';
  }
  await gh.post(`/repos/${repo}/issues/${issueNumber}/comments`, { body });
  return 'created';
}

module.exports = {
  MARKER, N8N_MODE, TOOLS, renderComment, renderCore, trailFromSteps, sawKagentWork, targetOf, bareTool, postComment,
};
