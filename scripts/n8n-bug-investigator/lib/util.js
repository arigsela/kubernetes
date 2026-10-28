// scripts/n8n-bug-investigator/lib/util.js
// Times, query windows, clip/fence and secret scrubbing for the n8n bug-investigator POC (§T.152).
// Ports of the bug-investigator core (base-apps/kagent/bug-investigator-core-configmap.yaml):
// util.parse_time, util.iso, util.window, util.scrub_text, util.scrub, gather._short,
// gather._clip, gather._fence. tests/n8n_bug_investigator/test_util.py runs both sides on the
// same inputs. Python semantics kept on purpose: str.strip()'s whitespace set, len() counting
// code points, and Unicode-aware \b.
'use strict';

const HOUR_MS = 3_600_000;

// The characters Python's str.isspace() accepts: what str.strip() removes. JS trim() differs
// (it keeps \x1c-\x1f and \x85, and strips ﻿).
const PY_WS = '\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
const LEADING_WS = new RegExp(`^[${PY_WS}]+`);
const TRAILING_WS = new RegExp(`[${PY_WS}]+$`);

// Python str.splitlines().
function splitLines(text) {
  const parts = String(text).split(/\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]/);
  if (parts.length && parts[parts.length - 1] === '') parts.pop();
  return parts;
}

function strip(s) {
  return String(s).replace(LEADING_WS, '').replace(TRAILING_WS, '');
}

function rstrip(s) {
  return String(s).replace(TRAILING_WS, '');
}

function pad(n, w = 2) {
  return String(n).padStart(w, '0');
}

function daysIn(year, month) {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

// The subset of datetime.fromisoformat (3.11+) that timestamps here use: a date, optionally
// a separator (any one character) and HH[:MM[:SS[.f…]]], optionally an offset ±HH[[:]MM[[:]SS]].
const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})(?:[\s\S](\d{2})(?::(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)?)?(?:([+-])(\d{2})(?::?(\d{2})(?::?(\d{2}))?)?)?$/;

function parseTime(s) {
  if (typeof s !== 'string' || !strip(s)) return null;
  let t = strip(s).replace(' ', 'T');
  if (t.endsWith('Z') || t.endsWith('z')) t = t.slice(0, -1) + '+00:00';
  t = t.replace(/(\.\d{6})\d+/, '$1');
  const m = ISO_RE.exec(t);
  if (!m) return null;
  const [year, month, day] = [m[1], m[2], m[3]].map(Number);
  const hour = m[4] === undefined ? 0 : Number(m[4]);
  const minute = m[5] === undefined ? 0 : Number(m[5]);
  const second = m[6] === undefined ? 0 : Number(m[6]);
  const ms = m[7] === undefined ? 0 : Math.floor(Number(m[7].padEnd(6, '0')) / 1000);
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > daysIn(year, month)) return null;
  if (hour > 23 || minute > 59 || second > 59) return null;
  let offsetMs = 0;
  if (m[8]) {
    const oh = Number(m[9]);
    const om = m[10] === undefined ? 0 : Number(m[10]);
    const os = m[11] === undefined ? 0 : Number(m[11]);
    if (oh > 23 || om > 59 || os > 59) return null;
    offsetMs = (m[8] === '-' ? -1 : 1) * ((oh * 60 + om) * 60 + os) * 1000;
  }
  const d = new Date(Date.UTC(year, month - 1, day, hour, minute, second, ms) - offsetMs);
  return Number.isNaN(d.getTime()) ? null : d;
}

function iso(d) {
  return d ? d.toISOString().slice(0, 19) + 'Z' : null;
}

function short(s) {
  const d = parseTime(s);
  if (!d) return '?';
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

function clip(s, n) {
  const t = strip(s == null ? '' : s);
  const cps = Array.from(t);
  return cps.length <= n ? t : rstrip(cps.slice(0, n - 1).join('')) + '…';
}

function fence(text) {
  const runs = String(text || '').match(/`+|~+/g) || [];
  const longest = runs.reduce((a, r) => Math.max(a, r.length), 0);
  const f = '~'.repeat(Math.max(4, longest + 1));
  return `${f}text\n${text}\n${f}`;
}

// A query window: the given one, else fallback ± pad; never longer than cap, never in the future.
// `now` replaces the core's utc_now() so the result depends only on the inputs.
function window(start, end, fallback, now, padMs = 2 * HOUR_MS, capMs = 24 * HOUR_MS) {
  let s = start ? start.getTime() : null;
  let e = end ? end.getTime() : null;
  const n = now.getTime();
  if (s === null && e === null) {
    const center = fallback ? fallback.getTime() : n;
    s = center - padMs;
    e = center + padMs;
  } else if (s === null) {
    s = e - padMs;
  } else if (e === null) {
    e = s + padMs;
  }
  if (e > n) e = n;
  if (s >= e) s = e - padMs;
  if (e - s > capMs) s = e - capMs;
  return [new Date(s), new Date(e)];
}

// ------------------------------------------------------------------------------------- scrub

// Python's \b is Unicode-aware for str patterns (a letter like é is a word character); JS's is
// ASCII-only, so the boundaries are spelled out.
const NOT_AFTER_WORD = '(?<![\\p{L}\\p{N}_])';
const NOT_BEFORE_WORD = '(?![\\p{L}\\p{N}_])';
const SECRET_PATTERNS = [
  new RegExp(`${NOT_AFTER_WORD}bearer[${PY_WS}]+[A-Za-z0-9._~+/=-]{8,}`, 'giu'),
  new RegExp(`${NOT_AFTER_WORD}glsa_[A-Za-z0-9_]{8,}`, 'gu'),
  new RegExp(`${NOT_AFTER_WORD}sk-ant-[A-Za-z0-9_-]{8,}`, 'gu'),
  new RegExp(`${NOT_AFTER_WORD}gh[pousr]_[A-Za-z0-9]{16,}`, 'gu'),
  new RegExp(`${NOT_AFTER_WORD}github_pat_[A-Za-z0-9_]{16,}`, 'gu'),
  new RegExp(`${NOT_AFTER_WORD}xox[abprs]-[A-Za-z0-9-]{8,}`, 'gu'),
  new RegExp(`${NOT_AFTER_WORD}AKIA[0-9A-Z]{16}${NOT_BEFORE_WORD}`, 'gu'),
];
const LONG_BLOB = /[A-Za-z0-9+/_-]{41,}={0,2}/g;

function keepBlob(v) {
  if (/^[0-9a-fA-F]+$/.test(v)) return true; // commit SHAs stay
  const inner = v.replace(/^\/+|\/+$/g, '');
  if (inner.includes('/') && (v.match(/\//g) || []).length >= 2 && !v.endsWith('=')) return true; // paths stay
  return !(/[A-Z]/.test(v) && /[a-z]/.test(v) && /[0-9]/.test(v)); // snake_case identifiers stay
}

function scrubText(s, extra = []) {
  let out = String(s);
  for (const secret of extra || []) {
    if (secret && Array.from(secret).length >= 8) out = out.split(secret).join('[redacted]');
  }
  for (const p of SECRET_PATTERNS) out = out.replace(p, '[redacted]');
  return out.replace(LONG_BLOB, (v) => (keepBlob(v) ? v : '[redacted]'));
}

function scrub(obj, extra = []) {
  if (typeof obj === 'string') return scrubText(obj, extra);
  if (Array.isArray(obj)) return obj.map((x) => scrub(x, extra));
  if (obj && typeof obj === 'object' && !(obj instanceof Date)) {
    return Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, scrub(v, extra)]));
  }
  return obj;
}

module.exports = { HOUR_MS, strip, rstrip, splitLines, parseTime, iso, short, clip, fence, window, scrubText, scrub };
