// Verify (child workflow): parse the agent's answer and re-check every claim (lib/verify.js).
// Spent either way: the agent was asked. An answer that is not findings JSON fails at "answer".
const { httpFromHelpers } = __require('./n8n');
const { makeGitHub } = __require('./github');
const { parseFindings, verify, makeVerifyCtx } = __require('./verify');
const { scrubText } = __require('./util');
const g = $('Gather facts').first().json;
const answer = $input.first().json;
const TOKEN = CONFIG.tokenName || 'BUG_INVESTIGATOR_GH_TOKEN';
const token = __secret(TOKEN);
const fail = (stage, e) => [{ json: { issue: g.issue, failed: true,
  failure: { stage, spent: true, error: scrubText(String((e && e.message) || e), [token]) } } }];
let findings;
try {
  findings = parseFindings(answer.output);
} catch (e) {
  return fail('answer', e);
}
try {
  const http = httpFromHelpers(this.helpers);
  const ctx = makeVerifyCtx(makeGitHub(http, token), http, CONFIG, g.facts, new Date(), [token],
    { deadlineMs: Date.now() + CONFIG.deadlineMs, telemetryUrl: CONFIG.telemetryUrl, grafanaUrl: CONFIG.grafanaUrl,
      datasources: CONFIG.datasources });
  const verified = await verify(findings, ctx);
  const stats = { turns: (answer.intermediateSteps || []).length, duration_ms: Date.now() - Date.parse(g.askedAt) };
  return [{ json: { issue: g.issue, failed: false, verified, stats } }];
} catch (e) {
  return fail('post', e);
}
