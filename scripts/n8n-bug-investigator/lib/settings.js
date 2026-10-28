// scripts/n8n-bug-investigator/lib/settings.js
// The label names and trusted bots (§T.152): this homelab's values, and the defaults when CONFIG
// sets none. CONFIG.labels may set any of them; labelsOf fills in the rest.
//   bug     the label that asks for an investigation (and marks past bugs)
//   lock    on the issue while an investigation runs
//   done    the investigation was posted
//   failed  the investigation failed after the agent was asked, or a lock went stale
//   skip    never investigated (and left out of past bugs and merged pull requests)
'use strict';

const LABELS = {
  bug: 'bug',
  lock: 'n8n-investigating',
  done: 'n8n-investigated',
  failed: 'n8n-investigation-failed',
  skip: 'rehearsal',
};
const TRUSTED_BOTS = ['arigsela-implementer[bot]'];

const labelsOf = (labels) => ({ ...LABELS, ...(labels || {}) });

module.exports = { LABELS, TRUSTED_BOTS, labelsOf };
