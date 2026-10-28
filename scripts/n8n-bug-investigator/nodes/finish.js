// Finish (child workflow): the final labels (lib/finish.js, the POC's own only) and the 👀 claim
// released. A label call that fails leaves the lock; the next pick releases it after 20 minutes.
const { httpFromHelpers } = __require('./n8n');
const { makeGitHub } = __require('./github');
const { addLabel, removeLabel, releaseClaim } = __require('./pick');
const { outcome } = __require('./finish');
const { scrubText } = __require('./util');
const item = $input.first().json;
const o = outcome(item, CONFIG.labels);
const n = item.issue && item.issue.number;
const TOKEN = CONFIG.tokenName || 'BUG_INVESTIGATOR_GH_TOKEN';
const token = __secret(TOKEN);
const result = { issue: n || null, outcome: o.outcome, verdict: item.verdict || null, verified_n: item.verified_n ?? null,
  total: item.total ?? null, contaminated: item.contaminated || [], failure: item.failure || null };
try {
  if (!token) throw new Error(`${TOKEN} is not set`);
  if (!n) throw new Error('no issue to label');
  const gh = makeGitHub(httpFromHelpers(this.helpers), token);
  for (const label of o.remove) await removeLabel(gh, CONFIG.repo, n, label);
  for (const label of o.add) await addLabel(gh, CONFIG.repo, n, label);
  await releaseClaim(gh, CONFIG.repo, n);
} catch (e) {
  result.finishError = scrubText(String((e && e.message) || e), [token]);
}
return [{ json: result }];
