// Pick issue (parent workflow): the next issue to investigate, or no item, which ends the run
// unsaved. The rules (own labels, one at a time, the 👀 claim, stale locks) are in lib/pick.js.
// A missing or refused token idles the run instead of failing it: it would fail every 2 minutes,
// and each failure is a saved execution (lib/pick.js idleReason). The reason goes to the log.
// The token is the n8n Variable or environment variable CONFIG.tokenName (__secret, the bundle's
// prelude).
const { httpFromHelpers } = __require('./n8n');
const { makeGitHub } = __require('./github');
const { pickIssue, idleReason } = __require('./pick');
const TOKEN = CONFIG.tokenName || 'BUG_INVESTIGATOR_GH_TOKEN';
const token = __secret(TOKEN);
if (!token) {
  console.error(`bug-investigator: ${TOKEN} is not set; idle`);
  return [];
}
const gh = makeGitHub(httpFromHelpers(this.helpers), token);
let r;
try {
  r = await pickIssue(gh, CONFIG.repo, new Date(), { notBefore: CONFIG.notBefore, labels: CONFIG.labels, trustedBots: CONFIG.trustedBots });
} catch (e) {
  const why = idleReason(e);
  if (!why) throw e;
  console.error(`bug-investigator: ${why}; idle`);
  return [];
}
if (!r.issue) return [];
return [{ json: { issue: r.issue, filedBy: r.filedBy, released: r.released, exhausted: r.exhausted } }];
