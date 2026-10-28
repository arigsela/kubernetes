// scripts/n8n-bug-investigator/lib/settings.js
// Everything specific to one deployment (§T.152): this homelab's values, used where CONFIG sets
// nothing. The handover package ships its own settings.js with neutral values in this file's
// place (scripts/n8n-bug-investigator/package/settings.js, same exports).
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
  lock: 'n8n-investigating',
  done: 'n8n-investigated',
  failed: 'n8n-investigation-failed',
  skip: 'rehearsal',
};
// GitHub App bots whose bug label counts without a permission check.
const TRUSTED_BOTS = ['arigsela-implementer[bot]'];
// Other tools' labels, left out of the ticket facts (kagent's).
const DROPPED_LABELS = ['investigated', 'investigation-failed', 'fix-proposed'];
// The comment: <!-- bug-investigator mode=… --> (how the workflow finds its own), title, footer lead.
const COMMENT = { mode: 'n8nagent', header: '🔎 Bug investigation — n8n AI Agent (POC)', footer: 'n8n/bug-investigator-poc' };
// The agent's tools without n8n's node-name prefix, for the trail.
const TOOLS = ['get_file_contents', 'search_code', 'list_commits', 'get_commit', 'list_pull_requests', 'pull_request_read',
  'query_loki_logs', 'query_loki_stats', 'list_loki_label_names', 'list_loki_label_values', 'query_prometheus'];
// Branches the comment flags when the agent read them (kagent's fix branches; null: none), and the flag.
const WATCH_BRANCHES = 'agent/taskboard-issue-(\\d+)';
const FLAG = "⚠️ While it ran, this agent read kagent's fix pull request branch(es) {branches}; compare with care.";
// Telemetry: the MCP server verify re-runs queries through, Grafana for Explore links, datasource UIDs.
const TELEMETRY_URL = 'http://taskboard-telemetry-mcp.kagent.svc.cluster.local:3000/mcp';
const GRAFANA_URL = 'https://grafana.arigsela.com';
const DATASOURCES = { loki: 'loki', prometheus: 'PBFA97CFB590B2093' };

const labelsOf = (labels) => ({ ...LABELS, ...(labels || {}) });

// What the agent is told about Taskboard's telemetry (the core's gather.telemetry_pointers).
function telemetryPointers(namespace, datasources = {}) {
  const ds = { ...DATASOURCES, ...(datasources || {}) };
  return {
    loki: {
      datasourceUid: ds.loki, selector: `{namespace="${namespace}"}`,
      app_label_values: [namespace, `${namespace}-traffic`],
      json_fields: ['time', 'level', 'msg', 'request_id', 'user', 'role', 'method', 'route', 'status',
        'duration_ms', 'task_id', 'error', 'stack', 'rule'],
      domain_events: ['status_changed', 'permission_denied', 'conflict', 'archive_run',
        'migration_applied', 'config_loaded', 'seeded', 'sim_action'],
    },
    prometheus: {
      datasourceUid: ds.prometheus,
      metrics: [`${namespace}_http_requests_total{method,route,status}`, `${namespace}_http_request_duration_seconds`,
        `${namespace}_tasks{status}`, `${namespace}_tasks_archived_total`,
        `${namespace}_permission_denied_total{rule}`, `${namespace}_archive_runs_total`,
        `${namespace}_build_info{commit}`],
      note: `other metrics need namespace="${namespace}"`,
    },
  };
}

module.exports = {
  LABELS, TRUSTED_BOTS, DROPPED_LABELS, COMMENT, TOOLS, WATCH_BRANCHES, FLAG, TELEMETRY_URL, GRAFANA_URL, DATASOURCES,
  labelsOf, telemetryPointers,
};
