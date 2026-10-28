#!/usr/bin/env node
// tests/n8n_bug_investigator/run.js: the bridge pytest uses to call the POC's JavaScript.
//
// stdin:  {"module": "util", "fn": "scrubText", "args": [...], "ctx": null | {"fake": "<name>", "spec": {...}}}
// stdout: {"ok": true, "value": <JSON>, "calls": [...]} or {"ok": false, "error": {"name", "message", ...}}
//
// `fn` is looked up in ADAPTERS first (test-only conveniences that turn JSON into Dates and
// back, kept here so lib/ stays free of test code), then in the module's exports. With a
// `ctx`, the fake built from its spec is passed as the first argument and its recorded
// calls are returned beside the value.
'use strict';
const path = require('path');

const LIB = path.resolve(__dirname, '../../scripts/n8n-bug-investigator/lib');

const ADAPTERS = {
  util: {
    isoOfParsed: (m, s) => m.iso(m.parseTime(s)),
    shortOf: (m, s) => m.short(s),
    windowIso: (m, start, end, fallback, now) =>
      m.window(m.parseTime(start), m.parseTime(end), m.parseTime(fallback), m.parseTime(now)).map(m.iso),
  },
  github: {
    // ghCall(fakeHttp, token, method, ...args): makeGitHub(http, token)[method](...args), plus the request count.
    ghCall: async (m, http, token, method, ...args) => {
      const gh = m.makeGitHub(http, token);
      const value = await gh[method](...args);
      return { value, requests: gh.requests };
    },
    ghLoginTwice: async (m, http, token) => {
      const gh = m.makeGitHub(http, token);
      return [await gh.login(), await gh.login()];
    },
  },
  mcp: {
    // mcpSession(fakeHttp, url, [[name, args], ...]): one session, the calls in order, then close().
    mcpSession: async (m, http, url, calls) => {
      const s = await m.openSession(http, url);
      const out = [];
      try {
        for (const [name, args] of calls) out.push(await s.callTool(name, args));
      } finally {
        await s.close();
      }
      return out;
    },
  },
  n8n: {
    httpVia: (m, helpers, req) => m.httpFromHelpers(helpers)(req),
  },
  gather: {
    // gatherVia(fakeHttp, cfg, issue, nowIso, {deadlineMs, clockMs}): a fixed clock when clockMs is given.
    gatherVia: (m, http, cfg, issue, now, opts) => {
      const o = opts || {};
      const clock = o.clockMs == null ? () => 0 : () => o.clockMs;
      const deadlineMs = o.deadlineMs == null ? Infinity : o.deadlineMs;
      return m.gatherFacts(github().makeGitHub(http, 'test-token'), cfg, issue, now, { clock, deadlineMs });
    },
  },
  verify: {
    verifyVia: (m, ctx, findings) => m.verify(findings, ctx),
    // ctxCall(fakeHttp, cfg, facts, nowIso, method, ...args): one call on a real makeVerifyCtx.
    ctxCall: (m, http, cfg, facts, now, method, ...args) =>
      m.makeVerifyCtx(github().makeGitHub(http, 'test-token'), http, cfg, facts, now, ['test-token'])[method](...args),
    // verifyReal(fakeHttp, cfg, facts, nowIso, findings): verify on a real makeVerifyCtx.
    verifyReal: (m, http, cfg, facts, now, findings) =>
      m.verify(findings, m.makeVerifyCtx(github().makeGitHub(http, 'test-token'), http, cfg, facts, now, ['test-token'])),
  },
  render: {
    postVia: (m, http, repo, n, body) => m.postComment(github().makeGitHub(http, 'test-token'), repo, n, body),
  },
  pick: {
    pickVia: (m, http, repo, now, notBefore) =>
      m.pickIssue(github().makeGitHub(http, 'test-token'), repo, now, { notBefore }),
    releaseClaimVia: (m, http, repo, n) => m.releaseClaim(github().makeGitHub(http, 'test-token'), repo, n),
  },
};

function github() {
  return require(path.join(LIB, 'github.js'));
}

// fakeHttp: replays canned replies by "METHOD url" (a list is consumed in order, its last
// reply repeating); `throws` maps "METHOD url" to a network-error message. Every request is
// recorded with lower-cased header names. An unknown route throws, so a test cannot pass by
// accident on a request it did not expect.
function fakeHttp(spec) {
  const calls = [];
  const used = {};
  const http = async ({ method, url, headers, body }) => {
    const key = `${method} ${url}`;
    const lower = Object.fromEntries(Object.entries(headers || {}).map(([k, v]) => [k.toLowerCase(), v]));
    calls.push({ method, url, headers: lower, body: body === undefined ? null : body });
    if (spec.throws && spec.throws[key]) throw new Error(spec.throws[key]);
    // GraphQL: the first `graphql` entry whose `match` text is in the query answers.
    if (method === 'POST' && url.endsWith('/graphql') && spec.graphql) {
      const hit = spec.graphql.find((g) => body && String(body.query).includes(g.match));
      if (!hit) throw new Error(`fakeHttp: no graphql route for ${String(body && body.query).slice(0, 80)}`);
      return { status: hit.status === undefined ? 200 : hit.status, headers: {}, body: hit.body };
    }
    const route = (spec.routes || {})[key];
    if (route === undefined) throw new Error(`fakeHttp: no route for ${key}`);
    const replies = Array.isArray(route) ? route : [route];
    const i = Math.min(used[key] || 0, replies.length - 1);
    used[key] = (used[key] || 0) + 1;
    const r = replies[i];
    return { status: r.status === undefined ? 200 : r.status, headers: r.headers || {}, body: r.body === undefined ? null : r.body };
  };
  http.calls = calls;
  return http;
}

// fakeHelpers: n8n's `this.helpers` with an httpRequest that records its options and returns
// spec.reply (or throws spec.throws).
function fakeHelpers(spec) {
  const calls = [];
  return {
    calls,
    httpRequest: async (opts) => {
      calls.push(opts);
      if (spec.throws) throw new Error(spec.throws);
      return spec.reply;
    },
  };
}

// fakeVerifyCtx: the verify ctx contract served from a case (what makeVerifyCtx would answer):
// commits on main per repo, files per repo, PR and issue states, and telemetry tool replies
// keyed by expression, mapped with verify.js's own queryResult. Query calls are recorded.
function fakeVerifyCtx(spec) {
  const v = require(path.join(LIB, 'verify.js'));
  const u = require(path.join(LIB, 'util.js'));
  const calls = [];
  const find = (key, sha) => {
    const s = String(sha).toLowerCase();
    const hits = [...(spec.commits[key] || []), spec.heads[key]].filter((c) => c && c.startsWith(s));
    return hits.length === 1 ? hits[0] : null;
  };
  return {
    calls,
    issue: spec.issue,
    issueCreated: u.parseTime(spec.issueCreated),
    now: u.parseTime(spec.now),
    repos: spec.repos,
    heads: spec.heads,
    secrets: spec.secrets || [],
    grafanaUrl: spec.grafanaUrl,
    commit: async (key, sha) => find(key, sha),
    fileLines: async (key, p) => {
      const text = ((spec.files || {})[key] || {})[p];
      return text === undefined ? null : v.splitLines(text);
    },
    prState: async (n) => (spec.prs || {})[String(n)] || null,
    issueState: async (n) => (spec.issues || {})[String(n)] || null,
    query: async (kind, expr, start, end) => {
      calls.push({ kind, expr, start, end });
      const reply = (spec.queries || {})[expr];
      if (!reply) throw new Error(`fakeVerifyCtx: no reply for ${expr}`);
      return v.queryResult(reply);
    },
    close: async () => { calls.push({ closed: true }); },
  };
}

const FAKES = { fakeHttp, fakeHelpers, fakeVerifyCtx };

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (c) => { data += c; });
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

function errorJson(e) {
  const out = { name: (e && e.name) || 'Error', message: String((e && e.message) || e) };
  for (const k of ['status', 'path', 'stage', 'spent']) if (e && e[k] !== undefined) out[k] = e[k];
  return out;
}

async function main() {
  const req = JSON.parse(await readStdin());
  let fake = null;
  try {
    const mod = require(path.join(LIB, `${req.module}.js`));
    const adapter = (ADAPTERS[req.module] || {})[req.fn];
    const args = req.args || [];
    if (req.ctx) {
      const make = FAKES[req.ctx.fake];
      if (!make) throw new Error(`run.js has no fake named ${req.ctx.fake}`);
      fake = make(req.ctx.spec || {});
      args.unshift(fake);
    }
    let value;
    if (adapter) value = await adapter(mod, ...args);
    else if (typeof mod[req.fn] === 'function') value = await mod[req.fn](...args);
    else throw new Error(`lib/${req.module}.js has no export ${req.fn}`);
    process.stdout.write(JSON.stringify({ ok: true, value: value === undefined ? null : value, calls: fake ? fake.calls || [] : [] }));
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: errorJson(e), calls: fake ? fake.calls || [] : [] }));
  }
}

main().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
