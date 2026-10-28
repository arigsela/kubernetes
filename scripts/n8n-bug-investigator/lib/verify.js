// scripts/n8n-bug-investigator/lib/verify.js
// Parse and verify the n8n agent's findings (§T.152): ports of the trigger's parse_findings and
// the core's verify.verify. Every evidence item is re-checked before anything is posted, and the
// output must equal the core's for the same inputs (tests/n8n_bug_investigator/test_verify.py
// runs both on one case). Where the inputs reach this code differently:
// - files come from the GitHub API at the heads gather captured; a deploy-repo file resolves only
//   where kagent's cone-mode sparse checkout of base-apps/taskboard has files;
// - a commit counts only if it is in that head's history (compare), as in the single-branch clones;
// - telemetry re-runs through taskboard-telemetry, one MCP session per verify; its scope refusal
//   is the core's scope check, so "out of scope: …" reads the same. Output the server truncated
//   cannot be counted and is dropped (the core counts every frame).
'use strict';
const { strip, parseTime, iso, window, scrub, HOUR_MS } = require('./util');
const { openSession, countResults } = require('./mcp');

const PY_WS = '\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
const SHA_RE = /^[0-9a-fA-F]{7,40}$/;
const NUM_RE = new RegExp(`^[${PY_WS}]*(?:PR[${PY_WS}]*|issue[${PY_WS}]*)?#?([0-9]{1,7})[${PY_WS}]*$`, 'i');
const LINE_SUFFIX_RE = /^(.*?)(?::|#L)([0-9]+)(?:-L?[0-9]+)?$/;
const WS_SPLIT = new RegExp(`[${PY_WS}]+`);
const MERGED_RE = /(?<![\p{L}\p{N}_])merged(?![\p{L}\p{N}_])/iu;
const NOT_MERGED_RE = /(?<![\p{L}\p{N}_])not merged(?![\p{L}\p{N}_])/iu;
const PAD_MS = 2 * HOUR_MS;
const TELEMETRY_URL = 'http://taskboard-telemetry-mcp.kagent.svc.cluster.local:3000/mcp';
const LOKI_UID = 'loki';
const PROM_UID = 'PBFA97CFB590B2093';
const DATASOURCES = { logql: { uid: LOKI_UID, type: 'loki' }, promql: { uid: PROM_UID, type: 'prometheus' } };
const TRUNCATED = 'the telemetry server truncated the output; could not count it';

class VerifyTimeout extends Error {
  constructor(message = 'verifying passed its deadline') {
    super(message);
    this.name = 'VerifyTimeout';
  }
}

const isObj = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);
const pyStr = (v) => (v === null || v === undefined ? 'None' : v === true ? 'True' : v === false ? 'False' : String(v));
function pyRepr(v) {
  if (v === null || v === undefined) return 'None';
  if (typeof v === 'boolean') return v ? 'True' : 'False';
  if (typeof v !== 'string') return String(v);
  if (v.includes("'") && !v.includes('"')) return `"${v}"`;
  return `'${v.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
}

// Python str.splitlines().
function splitLines(text) {
  const parts = String(text).split(/\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]/);
  if (parts.length && parts[parts.length - 1] === '') parts.pop();
  return parts;
}

const norm = (s) => String(s || '').replace(/[`*]/g, '').split(WS_SPLIT).filter(Boolean).join(' ');

// 1-based line where the normalized quote starts within lines[lo..hi], else null.
function findQuote(lines, quote, lo = 1, hi = null) {
  const q = norm(quote);
  if (!q) return null;
  const top = Math.min(hi || lines.length, lines.length);
  const starts = [];
  const parts = [];
  let pos = 0;
  for (let i = Math.max(lo, 1); i <= top; i += 1) {
    const t = norm(lines[i - 1]);
    starts.push([pos, i]);
    parts.push(t);
    pos += t.length + 1;
  }
  const idx = parts.join(' ').indexOf(q);
  if (idx < 0) return null;
  let line = starts.length ? starts[0][1] : null;
  for (const [p, i] of starts) {
    if (p <= idx) line = i;
    else break;
  }
  return line;
}

// `path:42` or `path#L42` → [path, 42]; an explicit line wins.
function splitLine(ref, line) {
  let explicit = null;
  if (typeof line === 'number' && Number.isInteger(line)) explicit = line;
  else if (typeof line === 'string' && /^[0-9]+$/.test(strip(line))) explicit = Number(strip(line));
  const m = LINE_SUFFIX_RE.exec(ref || '');
  if (m) return [m[1], explicit !== null ? explicit : Number(m[2])];
  return [ref, explicit];
}

// A repo-relative path with . and .. resolved; null when it leaves the checkout or is absolute.
function normPath(ref) {
  if (ref.startsWith('/')) return null;
  const out = [];
  for (const seg of (ref.startsWith('./') ? ref.slice(2) : ref).split('/')) {
    if (seg === '' || seg === '.') continue;
    if (seg === '..') {
      if (!out.length) return null;
      out.pop();
    } else {
      out.push(seg);
    }
  }
  return out.length ? out.join('/') : null;
}

const roots = (prefer) => (prefer === 'deploy' ? ['deploy', 'app'] : ['app', 'deploy']);

async function resolve(ctx, ref, prefer) {
  const r = strip(ref == null ? '' : ref);
  if (!r) return null;
  const rel = normPath(r);
  if (!rel) return null;
  for (const key of roots(prefer)) {
    const lines = await ctx.fileLines(key, rel);
    if (lines) return [key, rel, lines];
  }
  return null;
}

const blobLink = (ctx, key, rel, line) =>
  `https://github.com/${ctx.repos[key]}/blob/${ctx.heads[key] || 'HEAD'}/${rel}${line ? `#L${line}` : ''}`;

function pyQuote(s) {
  return encodeURIComponent(s).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}

// json.dumps escapes every non-ASCII character.
const asciiJson = (v) => JSON.stringify(v).replace(/[\u007f-\uffff]/g, (c) => (c === '\u007f' ? c : `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`));

function exploreLink(baseUrl, kind, expr, startMs, endMs) {
  const ds = DATASOURCES[kind];
  const q = { refId: 'A', datasource: { type: ds.type, uid: ds.uid }, expr };
  if (kind === 'logql') q.queryType = 'range';
  else q.range = true;
  const panes = { a: { datasource: ds.uid, queries: [q], range: { from: String(startMs), to: String(endMs) } } };
  return `${String(baseUrl).replace(/\/+$/, '')}/explore?schemaVersion=1&orgId=1&panes=${pyQuote(asciiJson(panes))}`;
}

// ------------------------------------------------------------------------------------ checks

async function checkCommit(ctx, ref, prefer = null) {
  const sha = strip(ref || '');
  if (!SHA_RE.test(sha)) return ['not a commit SHA', {}];
  for (const key of roots(prefer)) {
    const full = await ctx.commit(key, sha);
    if (full) return [null, { repo: key, sha: full, link: `https://github.com/${ctx.repos[key]}/commit/${full}` }];
  }
  return ['commit not found in the checkouts', {}];
}

async function cached(ctx, kind, n, fn) {
  ctx._cache = ctx._cache || new Map();
  const k = `${kind}:${n}`;
  if (!ctx._cache.has(k)) ctx._cache.set(k, await fn(n));
  return ctx._cache.get(k);
}

async function checkPr(ctx, ref, detail = '') {
  const m = NUM_RE.exec(ref || '');
  if (!m) return ['not a PR number', {}];
  const n = Number(m[1]);
  const st = await cached(ctx, 'pr', n, ctx.prState);
  if (!st) return [`PR #${n} not found`, {}];
  const d = detail || '';
  if (MERGED_RE.test(d) && st.state !== 'MERGED' && !NOT_MERGED_RE.test(d)) return [`PR #${n} is ${pyStr(st.state)}, not merged`, {}];
  return [null, { number: n, state: st.state === undefined ? null : st.state, title: st.title === undefined ? null : st.title,
    link: st.url || `https://github.com/${ctx.repos.app}/pull/${n}` }];
}

async function checkIssue(ctx, ref) {
  const m = NUM_RE.exec(ref || '');
  if (!m) return ['not an issue number', {}];
  const n = Number(m[1]);
  const st = await cached(ctx, 'issue', n, ctx.issueState);
  if (!st) return [`issue #${n} not found`, {}];
  if ((st.labels || []).includes('rehearsal')) return [`issue #${n} is a rehearsal`, {}];
  return [null, { number: n, state: st.state === undefined ? null : st.state, title: st.title === undefined ? null : st.title,
    link: st.url || `https://github.com/${ctx.repos.app}/issues/${n}` }];
}

async function checkFile(ctx, ref, line, quote, prefer) {
  const [path, lnIn] = splitLine(ref, line);
  let ln = lnIn;
  const found = await resolve(ctx, path, prefer);
  if (!found) return [`file ${pyStr(path)} not found in the checkouts`, {}];
  const [key, rel, lines] = found;
  if (ln !== null && !(ln >= 1 && ln <= Math.max(lines.length, 1))) return [`line ${ln} is outside ${rel} (${lines.length} lines)`, {}];
  if (quote) {
    const at = ln ? findQuote(lines, quote, ln - 3, ln + 3) : findQuote(lines, quote);
    if (at === null) return [`quote not found ${ln ? `near line ${ln}` : 'in the file'} of ${rel}`, {}];
    ln = ln || at;
  }
  return [null, { repo: key, path: rel, line: ln, link: blobLink(ctx, key, rel, ln) }];
}

async function checkDoc(ctx, ref, quote, prefer) {
  const [path] = splitLine(ref, null);
  const found = await resolve(ctx, path, prefer);
  if (!found) return [`doc ${pyStr(path)} not found in the checkouts`, {}];
  if (!quote || !norm(quote)) return ['doc evidence needs a verbatim quote', {}];
  const [key, rel, lines] = found;
  const at = findQuote(lines, quote);
  if (at === null) return [`quote not found verbatim in ${rel}`, {}];
  return [null, { repo: key, path: rel, line: at, link: blobLink(ctx, key, rel, at) }];
}

function reportWindow(findings, ctx) {
  const rep = findings.report || {};
  const s = parseTime(rep.when_start);
  const e = parseTime(rep.when_end);
  const plus = (d, ms) => new Date(d.getTime() + ms);
  if (s && e) return window(plus(s, -PAD_MS), plus(e, PAD_MS), null, ctx.now);
  if (s || e) return window(plus(s || e, -PAD_MS), plus(e || s, PAD_MS), null, ctx.now);
  return window(null, null, ctx.issueCreated || ctx.now, ctx.now);
}

// A taskboard-telemetry reply as verify reads it: {refused}, {error} or {count}.
function queryResult(reply) {
  if (!reply || reply.isError) {
    const t = String((reply && reply.text) || '');
    return t.startsWith('refused: ') ? { refused: t.slice('refused: '.length) } : { error: t };
  }
  const n = countResults(reply.text);
  return n === null ? { error: TRUNCATED } : { count: n };
}

async function checkQuery(ctx, item, findings) {
  const kind = item.kind;
  const expr = item.ref;
  const s = parseTime(item.start);
  const e = parseTime(item.end);
  const [start, end] = s || e ? window(s, e, null, ctx.now) : reportWindow(findings, ctx);
  const r = await ctx.query(kind, expr, iso(start), iso(end));
  if (r.refused !== undefined) return [`out of scope: ${r.refused}`, {}];
  if (r.error !== undefined) return [`Loki/Prometheus query failed: ${r.error}`, {}];
  const n = r.count;
  const expect = item.expect || 'found';
  if (expect === 'found' && n === 0) return [`query returned nothing in ${iso(start)}..${iso(end)}`, {}];
  if (expect === 'none' && n > 0) return [`query returned ${n} results in ${iso(start)}..${iso(end)}, expected none`, {}];
  return [null, { count: n, start: iso(start), end: iso(end), link: exploreLink(ctx.grafanaUrl, kind, expr, start.getTime(), end.getTime()) }];
}

async function checkItem(ctx, item, findings) {
  const kind = item.kind;
  const ref = String(item.ref || '');
  const prefer = item.repo;
  if (kind === 'commit') return checkCommit(ctx, ref, prefer);
  if (kind === 'pr') return checkPr(ctx, ref, item.detail || '');
  if (kind === 'issue') return checkIssue(ctx, ref);
  if (kind === 'file') return checkFile(ctx, ref, item.line, item.quote, prefer);
  if (kind === 'doc') return checkDoc(ctx, ref, item.quote, prefer);
  if (kind === 'logql' || kind === 'promql') return checkQuery(ctx, item, findings);
  return [`unknown evidence kind ${pyRepr(kind)}`, {}];
}

// ------------------------------------------------------------------------------------ verify

function placeholder(reason) {
  return {
    summary: `The investigation did not finish: ${reason}.`,
    verdict: 'unclear', confidence: 'low',
    report: Object.fromEntries(['who', 'what', 'when_start', 'when_end', 'where', 'expected', 'actual'].map((k) => [k, ''])),
    docs_rule: null, likely_cause: null, hypotheses: [], evidence: [], similar_past_bugs: [],
    next_step: 'Re-run the investigation (workflow_dispatch) or investigate by hand.',
    could_not_check: [reason],
  };
}

const ref = (x) => (x === undefined ? null : x);

async function verifyFindings(findings, ctx) {
  const out = isObj(findings) ? JSON.parse(JSON.stringify(findings)) : placeholder('no findings were produced');
  for (const key of ['hypotheses', 'evidence', 'similar_past_bugs', 'could_not_check']) {
    if (!Array.isArray(out[key])) out[key] = [];
  }
  const dropped = [];
  const notes = [];

  const kept = [];
  const seen = new Set();
  for (const item of out.evidence) {
    if (!isObj(item)) continue;
    const iid = pyStr(item.id || `E${seen.size + 1}`);
    if (seen.has(iid)) {
      dropped.push({ id: iid, kind: ref(item.kind), ref: ref(item.ref), reason: 'duplicate id' });
      continue;
    }
    seen.add(iid);
    let reason;
    let extra;
    try {
      [reason, extra] = await checkItem(ctx, item, out);
    } catch (e) { // a check that crashes fails the item, not the run
      if (e && e.name === 'VerifyTimeout') throw e;
      [reason, extra] = [`check failed: ${e && e.message}`, {}];
    }
    if (reason) dropped.push({ id: iid, kind: ref(item.kind), ref: ref(item.ref), reason });
    else kept.push({ ...item, id: iid, verified: extra });
  }
  const total = seen.size;
  out.evidence = kept;
  const okIds = new Set(kept.map((e) => e.id));

  for (const h of out.hypotheses) {
    if (!isObj(h)) continue;
    const ids = (h.evidence_ids || []).filter((i) => okIds.has(i));
    h.evidence_ids = ids;
    if ((h.status === 'supported' || h.status === 'ruled_out') && !ids.length) {
      notes.push(`${pyStr(h.id)}: ${h.status} → untested (no verified evidence)`);
      h.status = 'untested';
    }
  }
  out.hypotheses = out.hypotheses.filter(isObj);

  const dr = out.docs_rule;
  if (isObj(dr)) {
    const [reason, extra] = await checkDoc(ctx, dr.path === undefined ? '' : dr.path, dr.quote, null);
    if (reason) {
      dropped.push({ id: 'docs_rule', kind: 'doc', ref: ref(dr.path), reason });
      out.docs_rule = null;
    } else {
      dr.verified = extra;
    }
  } else {
    out.docs_rule = null;
  }

  const lc = out.likely_cause;
  let lcFailed = false; // a stated cause whose change and code both failed verification
  if (isObj(lc)) {
    const cref = String(lc.change_ref || '');
    let reason;
    let extra;
    if (NUM_RE.test(cref)) [reason, extra] = await checkPr(ctx, cref);
    else if (SHA_RE.test(strip(cref))) [reason, extra] = await checkCommit(ctx, cref);
    else [reason, extra] = ['change_ref is neither #N nor a commit SHA', {}];
    if (reason) dropped.push({ id: 'likely_cause.change_ref', kind: 'change', ref: cref, reason });
    lc.change_verified = reason ? null : extra;
    const [creason, cextra] = await checkFile(ctx, String(lc.code_ref || ''), null, null, 'app');
    if (creason) dropped.push({ id: 'likely_cause.code_ref', kind: 'file', ref: ref(lc.code_ref), reason: creason });
    lc.code_verified = creason ? null : cextra;
    if (reason && creason) {
      out.likely_cause = null;
      lcFailed = true;
    }
  } else {
    out.likely_cause = null;
  }

  const supported = out.hypotheses.some((h) => h.status === 'supported');
  if (out.verdict === 'likely_cause' && (!supported || lcFailed)) {
    notes.push('verdict likely_cause → unclear (no supported hypothesis with verified evidence)');
    out.verdict = 'unclear';
    if (out.likely_cause) out.likely_cause.unconfirmed = true;
  } else if (out.verdict === 'likely_cause' && !out.likely_cause) {
    notes.push('likely_cause details missing; the supported hypothesis stands in for them');
  }
  if (out.verdict === 'not_a_bug' && !out.docs_rule) {
    notes.push('verdict not_a_bug → unclear (no verified docs rule saying this is intended)');
    dropped.push({ id: 'verdict', kind: 'verdict', ref: 'not_a_bug', reason: 'not_a_bug needs a verified docs_rule quote' });
    out.verdict = 'unclear';
  }

  const bugs = [];
  for (const b of out.similar_past_bugs) {
    if (!isObj(b)) continue;
    let [reason, extra] = await checkIssue(ctx, String(b.issue || ''));
    if (!reason && extra.number === ctx.issue) reason = 'that is this issue';
    if (reason) dropped.push({ id: 'similar_past_bugs', kind: 'issue', ref: ref(b.issue), reason });
    else bugs.push({ ...b, verified: extra });
  }
  out.similar_past_bugs = bugs;

  out.verification = { verified: kept.length, total, dropped, notes, heads: ctx.heads, checked_at: iso(ctx.now) };
  return scrub(out, ctx.secrets || []);
}

async function verify(findings, ctx) {
  try {
    return await verifyFindings(findings, ctx);
  } finally {
    if (ctx.close) await ctx.close();
  }
}

// ------------------------------------------------------------------------------ parse_findings

// The end of the JSON object that starts at s[0] ('{'), or -1 if it never closes.
function objectEnd(s) {
  let depth = 0;
  let inStr = false;
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i];
    if (inStr) {
      if (c === '\\') i += 1;
      else if (c === '"') inStr = false;
    } else if (c === '"') {
      inStr = true;
    } else if (c === '{' || c === '[') {
      depth += 1;
    } else if (c === '}' || c === ']') {
      depth -= 1;
      if (depth === 0) return i + 1;
    }
  }
  return -1;
}

// The agent's answer is the findings JSON; a code fence or a line around it is tolerated.
function parseFindings(text) {
  if (!text || !strip(text)) throw new Error("the agent's answer is empty");
  let s = strip(text);
  const fence = /```(?:json)?\s*\n([\s\S]*?)\n```/.exec(s);
  if (fence) s = strip(fence[1]);
  const start = s.indexOf('{');
  if (start < 0) throw new Error(`no JSON object in the agent's answer: ${pyRepr(Array.from(s).slice(0, 200).join(''))}`);
  let obj;
  try {
    const rest = s.slice(start);
    const end = objectEnd(rest);
    if (end < 0) throw new Error('the object never closes');
    obj = JSON.parse(rest.slice(0, end));
  } catch (e) {
    throw new Error(`the agent's answer is not valid JSON (${e.message}); it may have been cut off`);
  }
  if (!isObj(obj) || !('verdict' in obj)) throw new Error("the agent's JSON has no `verdict`: not the findings schema");
  return obj;
}

// ------------------------------------------------------------------------------ the ctx

const encPath = (p) => String(p).split('/').map(encodeURIComponent).join('/');

function b64decode(s) {
  const clean = String(s || '').replace(/\s/g, '');
  if (typeof Buffer !== 'undefined') return Buffer.from(clean, 'base64').toString('utf8');
  const bin = atob(clean);
  return new TextDecoder('utf-8').decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

// What kagent's cone-mode sparse checkout of deployPath holds: the cone, files at the repo root,
// and files directly in each directory above the cone.
function inCone(path, deployPath) {
  const cone = String(deployPath).replace(/\/+$/, '');
  if (path.startsWith(`${cone}/`)) return true;
  const dir = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
  if (dir === '') return true;
  return `${cone}/`.startsWith(`${dir}/`) && dir !== cone;
}

function makeVerifyCtx(gh, http, cfg, facts, nowIn, secrets = [],
  { clock = () => Date.now(), deadlineMs = Infinity, telemetryUrl = TELEMETRY_URL, grafanaUrl = 'https://grafana.arigsela.com' } = {}) {
  const guard = () => {
    if (clock() > deadlineMs) throw new VerifyTimeout();
  };
  const repos = { app: cfg.repo, deploy: cfg.deployRepo };
  const heads = {};
  if (facts.app_head) heads.app = facts.app_head;
  if (facts.deploy_head) heads.deploy = facts.deploy_head;
  let session = null;
  const notFound = (e) => e && e.name === 'GitHubError';

  return {
    issue: (facts.ticket || {}).number,
    issueCreated: parseTime((facts.ticket || {}).created_at),
    now: nowIn instanceof Date ? nowIn : parseTime(nowIn),
    repos, heads, secrets, grafanaUrl,

    async commit(key, sha) {
      guard();
      try {
        const c = await gh.get(`/repos/${repos[key]}/commits/${encodeURIComponent(sha)}`);
        if (!heads[key] || !c || !c.sha) return null;
        const cmp = await gh.get(`/repos/${repos[key]}/compare/${heads[key]}...${c.sha}`);
        return cmp && (cmp.status === 'behind' || cmp.status === 'identical') ? c.sha : null;
      } catch (e) {
        if (notFound(e)) return null;
        throw e;
      }
    },

    async fileLines(key, path) {
      if (!heads[key]) return null;
      if (key === 'deploy' && !inCone(path, cfg.deployPath)) return null;
      guard();
      try {
        const f = await gh.get(`/repos/${repos[key]}/contents/${encPath(path)}`, { ref: heads[key] });
        if (!f || Array.isArray(f) || f.type !== 'file') return null;
        return splitLines(b64decode(f.content));
      } catch (e) {
        if (notFound(e) && e.status === 404) return null;
        throw e;
      }
    },

    async prState(n) {
      guard();
      try {
        const p = await gh.get(`/repos/${repos.app}/pulls/${n}`);
        return { state: p.merged_at ? 'MERGED' : String(p.state || '').toUpperCase(), title: p.title, url: p.html_url };
      } catch (e) {
        if (notFound(e)) return null;
        throw e;
      }
    },

    async issueState(n) {
      guard();
      try {
        const i = await gh.get(`/repos/${repos.app}/issues/${n}`);
        if (i.pull_request) return null; // `gh issue view` refuses a PR number
        return { state: String(i.state || '').toUpperCase(), title: i.title, url: i.html_url,
          labels: (i.labels || []).map((l) => (typeof l === 'string' ? l : l.name)) };
      } catch (e) {
        if (notFound(e)) return null;
        throw e;
      }
    },

    async query(kind, expr, startIso, endIso) {
      guard();
      let name;
      let args;
      if (kind === 'logql') {
        name = 'query_loki_logs';
        args = { datasourceUid: LOKI_UID, logql: expr, startRfc3339: startIso, endRfc3339: endIso, limit: 100 };
      } else {
        name = 'query_prometheus';
        args = { datasourceUid: PROM_UID, expr, startTime: startIso, endTime: endIso };
      }
      try {
        if (!session) session = await openSession(http, telemetryUrl);
        return queryResult(await session.callTool(name, args));
      } catch (e) {
        return { error: String((e && e.message) || e) };
      }
    },

    async close() {
      if (session) await session.close();
      session = null;
    },
  };
}

module.exports = {
  VerifyTimeout, TELEMETRY_URL, splitLines, findQuote, splitLine, normPath, inCone, exploreLink, queryResult,
  parseFindings, verify, makeVerifyCtx, placeholder,
};
