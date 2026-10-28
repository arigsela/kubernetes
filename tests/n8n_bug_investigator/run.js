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
};

// Fakes are added by the tasks that need them (fakeHttp, fakeGitHub, fakeVerifyCtx).
const FAKES = {};

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
