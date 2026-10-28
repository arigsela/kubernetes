// Pick issue (parent workflow): the next issue to investigate, or no item, which ends the run
// unsaved. The rules (own labels, one at a time, the 👀 claim, stale locks) are in lib/pick.js.
const { httpFromHelpers } = __require('./n8n');
const { makeGitHub } = __require('./github');
const { pickIssue } = __require('./pick');
const token = $env.BUG_INVESTIGATOR_GH_TOKEN;
if (!token) throw new Error('BUG_INVESTIGATOR_GH_TOKEN is not set');
const gh = makeGitHub(httpFromHelpers(this.helpers), token);
const r = await pickIssue(gh, CONFIG.repo, new Date(), { notBefore: CONFIG.notBefore });
if (!r.issue) return [];
return [{ json: { issue: r.issue, filedBy: r.filedBy, released: r.released, exhausted: r.exhausted } }];
