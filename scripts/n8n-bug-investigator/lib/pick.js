// scripts/n8n-bug-investigator/lib/pick.js
// Which Taskboard `bug` issue the n8n POC investigates next (§T.152, Decisions 7 and 8).
//
// The trigger's rule (b) (scripts/bug_investigator_trigger.py pick_issue), on the POC's OWN labels:
// the oldest open `bug` issue whose `bug` label a writer or a trusted bot added, skipping
// n8n-investigated, n8n-investigation-failed, rehearsal, and issues created before notBefore.
// kagent's labels are ignored and never touched.
//
// One investigation at a time: a fresh n8n-investigating lock anywhere makes the run idle. The
// lock label is not a mutex (adding a label that is already there succeeds, and two n8n pods
// overlap during a rollout), so the issue is claimed first with a 👀 reaction from the token's
// user: GitHub answers 201 when it creates it and 200 when that user's reaction exists. Only a
// 201 proceeds. A lock (or a lone claim) older than LOCK_STALE_MS was left by a run that died;
// a stale lock is released as n8n-investigation-failed. An issue locked MAX_ATTEMPTS times
// without finishing is failed rather than retried forever.
'use strict';
const { parseTime } = require('./util');
const { GitHubError } = require('./github');

const LABELS = {
  bug: 'bug',
  lock: 'n8n-investigating',
  done: 'n8n-investigated',
  failed: 'n8n-investigation-failed',
  rehearsal: 'rehearsal',
};
const TRUSTED_BOTS = ['arigsela-implementer[bot]'];
const WRITE_ROLES = ['admin', 'maintain', 'write'];
const LOCK_STALE_MS = 1_200_000;
const MAX_ATTEMPTS = 3;
const CLAIM = 'eyes';

const toDate = (x) => (x instanceof Date ? x : parseTime(x));
const labelNames = (issue) => ((issue && issue.labels) || []).map((l) => (typeof l === 'string' ? l : l && l.name));

// Who added the `bug` label (the latest such event); the author if no event says.
function labelledBy(events, issue) {
  let who = null;
  for (const ev of events || []) {
    if (ev.event === 'labeled' && ev.label && ev.label.name === LABELS.bug) who = (ev.actor && ev.actor.login) || who;
  }
  return who || (issue && issue.user && issue.user.login) || null;
}

function mayTrigger(permission, login) {
  if (!login) return false;
  if (TRUSTED_BOTS.includes(login)) return true;
  if (!permission) return false;
  return WRITE_ROLES.includes(permission.role_name) || ['admin', 'write'].includes(permission.permission);
}

const lockEvents = (events) => (events || []).filter((ev) => ev.event === 'labeled' && ev.label && ev.label.name === LABELS.lock);

// When the lock was last added (the latest event: a retry adds it again).
function lockAddedAt(events) {
  let at = null;
  for (const ev of lockEvents(events)) {
    const t = parseTime(ev.created_at);
    if (t && (!at || t > at)) at = t;
  }
  return at;
}

const lockAttempts = (events) => lockEvents(events).length;

async function addLabel(gh, repo, n, label) {
  await gh.post(`/repos/${repo}/issues/${n}/labels`, { labels: [label] });
}

async function removeLabel(gh, repo, n, label) {
  const path = `/repos/${repo}/issues/${n}/labels/${encodeURIComponent(label)}`;
  const r = await gh.request('DELETE', path);
  if (r.status !== 200 && r.status !== 404) throw new GitHubError(r.status, path, `removing ${label} from #${n}: HTTP ${r.status}`);
}

// Deletes the token user's 👀 on issue n; anyone else's reaction stays.
async function releaseClaim(gh, repo, n) {
  const me = await gh.login();
  const reactions = await gh.getAll(`/repos/${repo}/issues/${n}/reactions`, { content: CLAIM });
  for (const r of reactions) {
    if (r && r.content === CLAIM && r.user && r.user.login === me) await gh.del(`/repos/${repo}/issues/${n}/reactions/${r.id}`);
  }
}

// 'won', or 'held' when another execution's claim is fresh. A claim of ours older than
// LOCK_STALE_MS with no lock beside it is a leftover: it is deleted and taken again, once.
async function claim(gh, repo, n, now) {
  const path = `/repos/${repo}/issues/${n}/reactions`;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const r = await gh.request('POST', path, { content: CLAIM });
    if (r.status === 201) return 'won';
    if (r.status !== 200) throw new GitHubError(r.status, path, `claiming #${n}: HTTP ${r.status}`);
    const at = parseTime(r.body && r.body.created_at);
    if (attempt > 0 || !at || now - at < LOCK_STALE_MS) return 'held';
    await releaseClaim(gh, repo, n);
  }
  return 'held';
}

// Errors that recur on every run until a person acts: an expired, revoked or under-scoped token,
// or a rate limit. The picker idles on them instead of failing every 2 minutes, which would save
// an error execution each time (720 a day) and push every other workflow's history out of n8n's
// database. Anything else still fails the run.
function idleReason(e) {
  if (!e || e.name !== 'GitHubError') return null;
  if (e.status === 401) return 'GitHub refused the token (HTTP 401: expired or revoked?)';
  if (e.status === 403) return 'GitHub answered HTTP 403 (the token lacks a permission, or a rate limit)';
  return null;
}

const shape = (i) => ({
  number: i.number, title: i.title, body: i.body, created_at: i.created_at, html_url: i.html_url, labels: labelNames(i),
});

async function pickIssue(gh, repo, nowIn, { notBefore } = {}) {
  const now = toDate(nowIn);
  const nb = notBefore ? toDate(notBefore) : null;
  if (!now) throw new Error('pickIssue needs now');
  if (!nb) throw new Error('pickIssue needs notBefore: issues opened before the POC was switched on are never picked');

  const issues = (await gh.getAll(`/repos/${repo}/issues`, { labels: LABELS.bug, state: 'open', sort: 'created', direction: 'asc' }))
    .filter((i) => !i.pull_request);
  const cache = new Map();
  const events = async (n) => {
    if (!cache.has(n)) cache.set(n, await gh.getAll(`/repos/${repo}/issues/${n}/events`));
    return cache.get(n);
  };
  const released = [];
  const exhausted = [];
  const out = (issue, filedBy, idle) => ({ issue, filedBy, released, exhausted, idle });

  // 1. Locks: release the stale ones; any fresh one means an investigation is running.
  let busy = null;
  for (const issue of issues) {
    if (!labelNames(issue).includes(LABELS.lock)) continue;
    const at = lockAddedAt(await events(issue.number));
    if (at && now - at < LOCK_STALE_MS) {
      busy = busy || issue.number;
      continue;
    }
    await removeLabel(gh, repo, issue.number, LABELS.lock);
    await addLabel(gh, repo, issue.number, LABELS.failed);
    await releaseClaim(gh, repo, issue.number);
    released.push(issue.number);
  }
  if (busy) return out(null, null, `busy #${busy}`);

  // 2. The oldest eligible issue, claimed, then locked.
  for (const issue of issues) {
    const n = issue.number;
    const labels = labelNames(issue);
    if (released.includes(n) || [LABELS.done, LABELS.failed, LABELS.rehearsal, LABELS.lock].some((l) => labels.includes(l))) continue;
    const created = parseTime(issue.created_at);
    if (!created || created < nb) continue;
    const evs = await events(n);
    const login = labelledBy(evs, issue);
    let permission = null;
    if (login && !TRUSTED_BOTS.includes(login)) {
      try {
        permission = await gh.get(`/repos/${repo}/collaborators/${encodeURIComponent(login)}/permission`);
      } catch (e) {
        permission = null;
      }
    }
    if (!mayTrigger(permission, login)) continue;
    if (lockAttempts(evs) >= MAX_ATTEMPTS) {
      await addLabel(gh, repo, n, LABELS.failed);
      exhausted.push(n);
      continue;
    }
    if ((await claim(gh, repo, n, now)) === 'held') return out(null, null, `claimed elsewhere #${n}`);
    await addLabel(gh, repo, n, LABELS.lock);
    return out(shape(issue), login, null);
  }
  return out(null, null, 'no eligible issue');
}

module.exports = {
  LABELS, TRUSTED_BOTS, LOCK_STALE_MS, MAX_ATTEMPTS, CLAIM,
  labelledBy, mayTrigger, lockAddedAt, lockAttempts, releaseClaim, removeLabel, addLabel, pickIssue, idleReason,
};
