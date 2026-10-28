// scripts/n8n-bug-investigator/lib/n8n.js
// The one place that touches n8n: adapts a Code node's `this.helpers.httpRequest` to the
// `http` contract every other lib uses (§T.152). Field names and the timeout behaviour were
// confirmed in Task 1: statusCode/headers/body with returnFullResponse, no throw on a status
// with ignoreHttpStatusErrors, and a throw after `timeout` ms.
'use strict';

function httpFromHelpers(helpers) {
  return async function http({ method, url, headers, body, timeoutMs = 20000 }) {
    const opts = {
      method, url, headers: headers || {},
      json: true, returnFullResponse: true, ignoreHttpStatusErrors: true, timeout: timeoutMs,
    };
    if (body !== undefined && body !== null) opts.body = body;
    const r = await helpers.httpRequest(opts);
    const lower = {};
    for (const [k, v] of Object.entries((r && r.headers) || {})) lower[k.toLowerCase()] = v;
    return { status: r.statusCode, headers: lower, body: r.body === undefined ? null : r.body };
  };
}

module.exports = { httpFromHelpers };
