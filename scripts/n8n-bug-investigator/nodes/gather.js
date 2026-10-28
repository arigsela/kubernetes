// Gather facts (child workflow): the facts and the agent's message, or a prepare failure. Not
// spent: the agent was not asked, so Finish only releases the lock and the next run retries.
const { httpFromHelpers } = __require('./n8n');
const { makeGitHub } = __require('./github');
const { gatherFacts, toMarkdown, buildMessage } = __require('./gather');
const { scrubText } = __require('./util');
const input = $input.first().json;
const TOKEN = CONFIG.tokenName || 'BUG_INVESTIGATOR_GH_TOKEN';
const token = __secret(TOKEN);
const out = { issue: input.issue || null, filedBy: input.filedBy || null, failed: false };
try {
  if (!token) throw new Error(`${TOKEN} is not set`);
  if (!out.issue || !out.issue.number) throw new Error('no issue in the input');
  const gh = makeGitHub(httpFromHelpers(this.helpers), token);
  const facts = await gatherFacts(gh, CONFIG, out.issue.number, new Date(), { deadlineMs: Date.now() + CONFIG.deadlineMs });
  out.facts = facts;
  out.message = buildMessage(out.issue.number, CONFIG.repo, toMarkdown(facts));
  out.askedAt = new Date().toISOString();
} catch (e) {
  out.failed = true;
  out.failure = { stage: 'prepare', spent: false, error: scrubText(String((e && e.message) || e), [token]) };
}
return [{ json: out }];
