#!/usr/bin/env node
// node-shapes.js <nodes.json> [--full]: the parameter shapes of the node types the bug-investigator
// workflows use, read from `n8n export:nodes --output=nodes.json` of the pinned image (Task 1).
//
//   docker exec n8n-spike n8n export:nodes --output=/tmp/nodes.json
//   docker cp n8n-spike:/tmp/nodes.json /tmp/nodes.json
//   node scripts/n8n-bug-investigator/spike/node-shapes.js /tmp/nodes.json > tests/n8n_bug_investigator/fixtures/n8n-2.42.3-nodes.json
//
// For each type it keeps the latest typeVersion and, per property, its name, type, default,
// the `show` conditions that gate it, and the names (or values) of its options. --full keeps
// every property; the default keeps only the ones the generator sets.
'use strict';
const fs = require('fs');

const WANT = {
  'n8n-nodes-base.scheduleTrigger': ['rule'],
  'n8n-nodes-base.code': ['mode', 'language', 'jsCode'],
  'n8n-nodes-base.if': ['conditions', 'looseTypeValidation', 'options'],
  'n8n-nodes-base.executeWorkflow': ['source', 'workflowId', 'workflowInputs', 'mode', 'options'],
  'n8n-nodes-base.executeWorkflowTrigger': ['inputSource', 'workflowInputs', 'jsonExample'],
  '@n8n/n8n-nodes-langchain.agent': ['promptType', 'text', 'hasOutputParser', 'needsFallback', 'options'],
  '@n8n/n8n-nodes-langchain.lmChatAnthropic': ['model', 'options'],
  '@n8n/n8n-nodes-langchain.mcpClientTool': ['endpointUrl', 'serverTransport', 'authentication', 'include', 'includeTools', 'excludeTools', 'options'],
};

function latest(nodes, name) {
  let best = null;
  for (const n of nodes) {
    if (n.name !== name) continue;
    const versions = [].concat(n.version);
    const top = Math.max(...versions);
    if (!best || top > best.top) best = { top, desc: n };
  }
  return best;
}

function shapeOf(p) {
  const out = { type: p.type };
  if (p.default !== undefined && p.default !== '') out.default = p.default;
  if (p.displayOptions && p.displayOptions.show) out.show = p.displayOptions.show;
  if (Array.isArray(p.options)) {
    out.options = p.options.map((o) => (o.name !== undefined && o.value === undefined && o.type ? o.name
      : o.value !== undefined ? o.value : o.name));
    const typed = p.options.filter((o) => o.type);
    if (typed.length) out.fields = Object.fromEntries(typed.map((o) => [o.name, shapeOf(o)]));
  }
  if (p.modes) out.modes = p.modes.map((m) => m.name);
  if (p.typeOptions && p.typeOptions.multipleValues) out.multipleValues = true;
  return out;
}

function main() {
  const [file, flag] = process.argv.slice(2);
  if (!file) { console.error('usage: node-shapes.js <nodes.json> [--full]'); process.exit(2); }
  const nodes = JSON.parse(fs.readFileSync(file, 'utf8'));
  const result = {};
  for (const [name, keep] of Object.entries(WANT)) {
    const found = latest(nodes, name);
    if (!found) { console.error(`missing node type ${name}`); process.exit(1); }
    const params = {};
    for (const p of found.desc.properties || []) {
      if (flag === '--full' || keep.includes(p.name)) {
        // A property can appear several times with different `show` gates; keep each.
        const key = params[p.name] ? `${p.name}#${Object.keys(params).filter((k) => k.split('#')[0] === p.name).length}` : p.name;
        params[key] = shapeOf(p);
      }
    }
    result[name] = { typeVersion: found.top, parameters: params };
  }
  process.stdout.write(JSON.stringify(result, null, 2) + '\n');
}

main();
