// scripts/n8n-bug-investigator/lib/mcp.js
// A minimal MCP client over Streamable HTTP, and countResults, for the n8n bug-investigator
// POC (§T.152). Verify re-runs the agent's telemetry queries through taskboard-telemetry (so
// its namespace scope check applies) on ONE session per verify, closed with DELETE: every
// session holds a stdio child on that shared server (Task 1 confirmed the session header,
// SSE replies and DELETE).
'use strict';

const PROTOCOL_VERSION = '2025-06-18';
const SESSION_HEADER = 'mcp-session-id';
const BASE_HEADERS = { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' };

function head(body) {
  return (typeof body === 'string' ? body : JSON.stringify(body == null ? '' : body)).slice(0, 200);
}

// The JSON-RPC message with this id, from a JSON body (object or text) or an SSE stream.
function replyFor(r, id) {
  let body = r.body;
  const ct = String((r.headers && r.headers['content-type']) || '');
  if (typeof body === 'string') {
    if (ct.includes('text/event-stream') || /^(event|data):/m.test(body)) {
      let found = null;
      for (const line of body.split(/\r?\n/)) {
        if (!line.startsWith('data:')) continue;
        try {
          const m = JSON.parse(line.slice(5).trim());
          if (m && m.id === id) found = m;
        } catch (e) { /* a keep-alive or partial line */ }
      }
      return found;
    }
    try { body = JSON.parse(body); } catch (e) { return null; }
  }
  if (Array.isArray(body)) return body.find((m) => m && m.id === id) || null;
  return body && body.id === id ? body : null;
}

async function openSession(http, url, { timeoutMs = 20000, clientName = 'n8n-bug-investigator-poc' } = {}) {
  let nextId = 1;
  const initId = nextId++;
  const r = await http({
    method: 'POST', url, headers: BASE_HEADERS, timeoutMs,
    body: { jsonrpc: '2.0', id: initId, method: 'initialize',
      params: { protocolVersion: PROTOCOL_VERSION, capabilities: {}, clientInfo: { name: clientName, version: '1' } } },
  });
  const init = replyFor(r, initId);
  if (r.status < 200 || r.status >= 300 || !init || init.error) {
    throw new Error(`MCP initialize at ${url} failed: HTTP ${r.status} ${init && init.error ? init.error.message : head(r.body)}`);
  }
  const sessionId = (r.headers && r.headers[SESSION_HEADER]) || null;
  const headers = sessionId ? { ...BASE_HEADERS, [SESSION_HEADER]: sessionId } : BASE_HEADERS;
  await http({ method: 'POST', url, headers, timeoutMs, body: { jsonrpc: '2.0', method: 'notifications/initialized' } });
  let closed = false;

  return {
    sessionId,
    async callTool(name, args) {
      const id = nextId++;
      const res = await http({
        method: 'POST', url, headers, timeoutMs,
        body: { jsonrpc: '2.0', id, method: 'tools/call', params: { name, arguments: args || {} } },
      });
      const m = replyFor(res, id);
      if (m && m.error) return { isError: true, text: String(m.error.message || 'MCP error') };
      if (res.status < 200 || res.status >= 300 || !m) {
        throw new Error(`MCP tools/call ${name} at ${url}: HTTP ${res.status} ${head(res.body)}`);
      }
      const content = (m.result && m.result.content) || [];
      const text = content.filter((c) => c && c.type === 'text').map((c) => c.text).join('');
      return { isError: !!(m.result && m.result.isError), text };
    },
    async close() {
      if (closed || !sessionId) return;
      closed = true;
      try {
        await http({ method: 'DELETE', url, headers, timeoutMs });
      } catch (e) { /* the server reaps it later */ }
    },
  };
}

async function callTool(http, url, name, args, opts) {
  const s = await openSession(http, url, opts);
  try {
    return await s.callTool(name, args);
  } finally {
    await s.close();
  }
}

// ---------------------------------------------------------------------------- countResults

function sampleCounts(v) {
  const s = String(v).trim().toLowerCase();
  const f = /^[+]?inf(inity)?$/.test(s) ? Infinity : /^-inf(inity)?$/.test(s) ? -Infinity : Number(s);
  return s !== '' && !Number.isNaN(f) && f !== 0;
}

const isLogEntry = (x) => x && typeof x === 'object' && !Array.isArray(x) && 'line' in x;

function countSeries(list) {
  let n = 0;
  for (const s of list || []) {
    for (const p of (s && s.values) || []) if (p && sampleCounts(p.value)) n += 1;
  }
  return n;
}

// Results in the telemetry server's output, with grafana.count_results' meaning: log lines for
// logs, non-zero samples (±Inf included, NaN not) for series. null = could not count: the
// server truncated the text, or kept no series out of a truncated list. A null must never
// confirm `expect: none`.
function countResults(text) {
  if (typeof text !== 'string' || text.endsWith(' ...[truncated]')) return null;
  let v;
  try { v = JSON.parse(text); } catch (e) { return null; }
  if (Array.isArray(v)) {
    if (v.length && v.every(isLogEntry)) return v.length;
    return countSeries(v);
  }
  if (v && typeof v === 'object') {
    if (v.truncated === true && Array.isArray(v.items)) {
      if (v.items.some(isLogEntry)) return typeof v.total === 'number' ? v.total : v.items.length;
      const n = countSeries(v.items);
      return n > 0 ? n : null;
    }
    if (Array.isArray(v.series)) return countSeries(v.series);
  }
  return null;
}

module.exports = { PROTOCOL_VERSION, SESSION_HEADER, openSession, callTool, countResults };
