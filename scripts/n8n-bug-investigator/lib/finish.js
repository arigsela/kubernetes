// scripts/n8n-bug-investigator/lib/finish.js
// The labels an n8n investigation ends with (§T.152): only the POC's own, never kagent's.
//   success                        → n8n-investigated, lock removed
//   a failure after the agent was asked (spent: the answer, verify, the post, the agent itself)
//                                  → n8n-investigation-failed, lock removed
//   a failure before it was asked  → lock removed: the next run retries (pick caps the attempts)
'use strict';
const { LABELS } = require('./pick');

function outcome(item) {
  const failure = item && item.failure;
  const failed = !!(item && (item.failed || failure));
  if (!failed) return { add: [LABELS.done], remove: [LABELS.lock], outcome: 'investigated' };
  if (failure && failure.spent) return { add: [LABELS.failed], remove: [LABELS.lock], outcome: 'failed' };
  return { add: [], remove: [LABELS.lock], outcome: 'released' };
}

module.exports = { outcome };
