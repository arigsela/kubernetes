// scripts/n8n-bug-investigator/lib/github.js
// A small GitHub REST/GraphQL client over an injected `http` (lib/n8n.js adapts n8n's
// this.helpers.httpRequest to it), for the n8n bug-investigator POC (§T.152).
//
// http contract: async http({method, url, headers, body, timeoutMs}) → {status, headers (lower-case), body};
// it never throws on an HTTP status, only on a network error or timeout.
// Every error message is scrubbed with the token as an extra secret: a token echoed by GitHub
// or by a proxy never reaches a log line, an execution record or a comment.
'use strict';
const { scrubText } = require('./util');

class GitHubError extends Error {
  constructor(status, path, message) {
    super(message);
    this.name = 'GitHubError';
    this.status = status;
    this.path = path;
  }
}

function qs(query) {
  const parts = Object.entries(query || {})
    .filter(([, v]) => v !== undefined && v !== null)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
  return parts.length ? `?${parts.join('&')}` : '';
}

function nextLink(headers) {
  const link = (headers && headers.link) || '';
  for (const part of String(link).split(',')) {
    const m = /<([^>]+)>\s*;\s*rel="next"/.exec(part);
    if (m) return m[1];
  }
  return null;
}

function detail(body) {
  if (body && typeof body === 'object') return String(body.message || JSON.stringify(body)).slice(0, 300);
  return String(body == null ? '' : body).slice(0, 300);
}

function makeGitHub(http, token, { api = 'https://api.github.com' } = {}) {
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'n8n-bug-investigator-poc',
  };
  const secrets = [token];
  const gh = { requests: 0 };
  let loginPromise = null;

  const urlOf = (pathOrUrl, query) => (/^https?:\/\//.test(pathOrUrl) ? pathOrUrl : `${api}${pathOrUrl}${qs(query)}`);
  const pathOf = (url) => {
    try { return new URL(url).pathname; } catch (e) { return String(url); }
  };

  async function send(method, url, body) {
    gh.requests += 1;
    const h = body === undefined ? headers : { ...headers, 'Content-Type': 'application/json' };
    try {
      return await http({ method, url, headers: h, body });
    } catch (e) {
      throw new GitHubError(0, pathOf(url), scrubText(`GitHub ${method} ${pathOf(url)}: ${e && e.message}`, secrets));
    }
  }

  function ok(r, method, url) {
    if (r.status >= 200 && r.status < 300) return r.body;
    throw new GitHubError(r.status, pathOf(url), scrubText(`GitHub ${method} ${pathOf(url)}: HTTP ${r.status} ${detail(r.body)}`, secrets));
  }

  gh.get = async (path, query) => {
    const url = urlOf(path, query);
    return ok(await send('GET', url), 'GET', url);
  };

  // Every page of a list endpoint, following `link: <…>; rel="next"`. `key` names the array
  // inside an object body (compare's `commits`); without it the body is the array.
  gh.getAll = async (path, query = {}, maxPages = 10, key = null) => {
    const q = { ...query };
    if (q.per_page === undefined) q.per_page = 100;
    let url = urlOf(path, q);
    const items = [];
    for (let page = 0; url && page < maxPages; page += 1) {
      const r = await send('GET', url);
      const body = ok(r, 'GET', url);
      const list = key ? (body && body[key]) || [] : body || [];
      items.push(...list);
      url = nextLink(r.headers);
    }
    return items;
  };

  gh.post = async (path, body) => {
    const url = urlOf(path);
    return ok(await send('POST', url, body), 'POST', url);
  };

  gh.patch = async (path, body) => {
    const url = urlOf(path);
    return ok(await send('PATCH', url, body), 'PATCH', url);
  };

  gh.del = async (path) => {
    const url = urlOf(path);
    return ok(await send('DELETE', url), 'DELETE', url);
  };

  // The raw status and body, for callers that branch on the status (the claim's 201 vs 200).
  gh.request = async (method, path, body) => {
    const r = await send(method, urlOf(path), body);
    return { status: r.status, body: r.body };
  };

  gh.graphql = async (query, variables = {}) => {
    const url = `${api}/graphql`;
    const body = ok(await send('POST', url, { query, variables }), 'POST', url);
    if (body && Array.isArray(body.errors) && body.errors.length) {
      const msg = body.errors.map((e) => e && e.message).filter(Boolean).join('; ');
      throw new GitHubError(200, '/graphql', scrubText(`GitHub GraphQL: ${msg}`.slice(0, 600), secrets));
    }
    return (body && body.data) || null;
  };

  gh.login = () => {
    if (!loginPromise) loginPromise = gh.get('/user').then((u) => u.login);
    return loginPromise;
  };

  return gh;
}

module.exports = { GitHubError, makeGitHub };
