// Render and post (child workflow): the comment (lib/render.js), flagged when the agent's tool
// calls named a kagent fix branch, posted or edited as the POC's own comment. Spent on failure.
const { httpFromHelpers } = __require('./n8n');
const { makeGitHub } = __require('./github');
const { renderComment, trailFromSteps, sawKagentWork, postComment } = __require('./render');
const { scrubText } = __require('./util');
const v = $input.first().json;
if (v.failed) return [{ json: v }];
const token = $env.BUG_INVESTIGATOR_GH_TOKEN || '';
const steps = $('Investigate').first().json.intermediateSteps || [];
const contaminated = sawKagentWork(steps);
try {
  const body = renderComment(v.verified, { model: CONFIG.model, stats: v.stats, trail: trailFromSteps(steps), contaminated });
  const posted = await postComment(makeGitHub(httpFromHelpers(this.helpers), token), CONFIG.repo, v.issue.number, body);
  const ver = v.verified.verification || {};
  return [{ json: { issue: v.issue, failed: false, posted, verdict: v.verified.verdict, verified_n: ver.verified, total: ver.total, contaminated } }];
} catch (e) {
  const failure = { stage: 'post', spent: true, error: scrubText(String((e && e.message) || e), [token]) };
  return [{ json: { issue: v.issue, failed: true, contaminated, failure } }];
}
