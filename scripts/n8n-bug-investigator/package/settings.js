// engine/lib/settings.js: the defaults for anything config.toml leaves unset. build.py writes
// every setting into the workflows' CONFIG, so these matter only if you edit CONFIG by hand.
// Same exports as the original deployment's settings.js; keep them if you change this file.
//
// Labels (CONFIG.labels may set any of them; labelsOf fills in the rest):
//   bug     the label that asks for an investigation (and marks past bugs)
//   lock    on the issue while an investigation runs
//   done    the investigation was posted
//   failed  the investigation failed after the agent was asked, or a lock went stale
//   skip    never investigated (and left out of past bugs and merged pull requests)
'use strict';

const LABELS = {
  bug: 'bug',
  lock: 'ai-investigating',
  done: 'ai-investigated',
  failed: 'ai-investigation-failed',
  skip: 'no-ai',
};
// GitHub App bots whose bug label counts without a permission check.
const TRUSTED_BOTS = [];
// Other tools' labels, left out of the ticket facts.
const DROPPED_LABELS = [];
// The comment: <!-- bug-investigator mode=… --> (how the workflow finds its own), title, footer lead.
const COMMENT = { mode: 'n8n-ai', header: '🔎 Bug investigation — AI agent', footer: 'n8n/bug-investigator' };
// The agent's tools without n8n's node-name prefix, for the trail.
const TOOLS = ['get_file_contents', 'search_code', 'list_commits', 'get_commit', 'list_pull_requests', 'pull_request_read'];
// Branches the comment flags when the agent read them (null: none), and the flag.
const WATCH_BRANCHES = null;
const FLAG = '⚠️ While it ran, this agent read the branch(es) {branches}; compare with care.';
// Telemetry: the MCP server verify re-runs queries through (null: none), Grafana for Explore
// links, datasource UIDs. Only for Loki and Prometheus through a Grafana MCP server.
const TELEMETRY_URL = null;
const GRAFANA_URL = null;
const DATASOURCES = { loki: '', prometheus: '' };

const labelsOf = (labels) => ({ ...LABELS, ...(labels || {}) });

// What the agent is told about telemetry when a telemetry server is set and config.toml has no
// telemetry notes (rendered under "Telemetry pointers").
function telemetryPointers(namespace, datasources = {}) {
  const ds = { ...DATASOURCES, ...(datasources || {}) };
  const scope = namespace ? ` Every Loki selector and Prometheus query needs namespace="${namespace}".` : '';
  return {
    notes: [
      `Loki datasourceUid \`${ds.loki}\`, Prometheus datasourceUid \`${ds.prometheus}\`.${scope}`,
    ],
  };
}

module.exports = {
  LABELS, TRUSTED_BOTS, DROPPED_LABELS, COMMENT, TOOLS, WATCH_BRANCHES, FLAG, TELEMETRY_URL, GRAFANA_URL, DATASOURCES,
  labelsOf, telemetryPointers,
};
