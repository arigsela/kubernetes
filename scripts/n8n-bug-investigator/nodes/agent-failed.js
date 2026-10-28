// Agent failed (child workflow): the AI Agent's error output (an MCP server down at connect, the
// model refused, the execution timed out). Spent: the agent was asked. The error item carries
// only a short message (Task 1), so the issue comes from Gather facts.
const { scrubText } = __require('./util');
const g = $('Gather facts').first().json;
const e = $input.first().json;
const TOKEN = CONFIG.tokenName || 'BUG_INVESTIGATOR_GH_TOKEN';
const error = scrubText(String(e.error || 'the AI Agent failed'), [__secret(TOKEN)]).slice(0, 500);
return [{ json: { issue: g.issue, failed: true, failure: { stage: 'ask', spent: true, error } } }];
