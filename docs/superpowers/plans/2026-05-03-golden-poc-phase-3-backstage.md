# Golden POC — Phase 3: Backstage Template + Plugins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the "Create New Agent" Software Template to Backstage so engineers self-serve new agents through a form (5 fields → PR), and ship the per-agent page polish (Try-it card with chat sidecar + Langfuse invocations link) and the embedded skill catalog plugin (top-level `/skills` route).

**Architecture:** The Software Template is a `scaffolder.backstage.io/v1beta3` Template manifest under `base-apps/backstage/templates/create-agent/`, with a Jinja skeleton that renders two files per agent: `agent.yaml` (the XAgent CR) and `catalog-info.yaml` (the Backstage Component). Two custom frontend plugins extend the Backstage app: `plugin-agent-tryit` (per-agent card) and `plugin-skill-catalog` (top-level page). Both fetch from existing services (Langfuse REST API, agentregistry HTTP API).

**Tech Stack:** Backstage (existing), `@backstage/plugin-scaffolder` (existing), Jinja templating in scaffolder skeleton, React + TypeScript for plugins, `@backstage/core-plugin-api`, Backstage CLI for plugin scaffolding.

**Reference design:** `docs/superpowers/specs/2026-05-02-golden-ai-platform-poc-design.md` Section 5 (Backstage UX), Section 7 (per-agent specifics).

**Dependencies:**
- Phase 0 complete (agentregistry HTTP API surface documented in preflight-results).
- Phase 1 complete (XAgent XRD live; the Template scaffolds CRs of this kind).
- Phase 2 complete (two agents reachable via real surfaces; the Try-it card needs a working agent endpoint to demo).

---

## Task 3.1: Create the "Create New Agent" Software Template

**Files:**
- Create: `base-apps/backstage/templates/create-agent/template.yaml`
- Create: `base-apps/backstage/templates/create-agent/skeleton/agent.yaml`
- Create: `base-apps/backstage/templates/create-agent/skeleton/catalog-info.yaml`
- Modify: `base-apps/backstage/configmaps.yaml` (add the Template to the Backstage catalog locations)

- [ ] **Step 1: Write the Template manifest**

`base-apps/backstage/templates/create-agent/template.yaml`:
```yaml
apiVersion: scaffolder.backstage.io/v1beta3
kind: Template
metadata:
  name: create-agent
  title: Create New Agent
  description: |
    Ship a new AI agent through the platform: Backstage form -> PR -> ArgoCD ->
    Crossplane Composition -> kagent runtime. Skills come from agentregistry.
  tags: [ai, agent, golden-poc]
spec:
  owner: platform-team
  type: agent

  parameters:
    - title: Identity
      required: [name, namespace, owner]
      properties:
        name:
          type: string
          title: Agent name
          description: Lowercase letters, numbers, hyphens. Used everywhere — choose carefully.
          pattern: "^[a-z][a-z0-9-]{2,30}$"
        namespace:
          type: string
          title: Namespace
          enum: [agents]
          default: agents
        owner:
          type: string
          title: Owner
          ui:field: OwnerPicker
          ui:options:
            allowedKinds: [Group, User]

    - title: Behavior
      required: [description, systemPrompt]
      properties:
        description:
          type: string
          title: Description
          description: One sentence shown in the Backstage catalog.
          minLength: 10
          maxLength: 200
        systemPrompt:
          type: string
          title: System prompt
          description: What the agent should do, how to behave, what to avoid.
          ui:widget: textarea
          ui:options: {rows: 12}

    - title: Skills + Surface
      required: [skills, surface]
      properties:
        skills:
          type: array
          title: Skills
          description: agentregistry skill OCI references. Browse the Skills tab to pick.
          minItems: 1
          items:
            type: object
            required: [ref]
            properties:
              ref:
                type: string
                title: OCI ref
                pattern: "^oci://.+:.+$"
              alias:
                type: string
                title: Alias (optional)
        surface:
          type: string
          title: Surface
          enum: [slack, http, mcp, github-webhook]
          enumNames:
            - "Slack (chat command)"
            - "HTTP only (Try-it button)"
            - "MCP (IDE integration)"
            - "GitHub webhook (PR review)"

    - title: Surface configuration
      properties:
        slackCommand:
          type: string
          title: Slack command (only for surface=slack)
          description: The word that follows the bot mention. e.g. 'cluster-health' for '@bot cluster-health <args>'
          pattern: "^[a-z][a-z0-9-]*$"
        githubRepo:
          type: string
          title: GitHub repo (only for surface=github-webhook)
          description: e.g. arigsela/kubernetes
          pattern: "^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$"

  steps:
    - id: render
      name: Render agent + catalog manifests
      action: fetch:template
      input:
        url: ./skeleton
        targetPath: base-apps/agents/${{ parameters.name }}
        values:
          name: ${{ parameters.name }}
          namespace: ${{ parameters.namespace }}
          owner: ${{ parameters.owner }}
          description: ${{ parameters.description }}
          systemPrompt: ${{ parameters.systemPrompt }}
          skills: ${{ parameters.skills }}
          surface: ${{ parameters.surface }}
          slackCommand: ${{ parameters.slackCommand }}
          githubRepo: ${{ parameters.githubRepo }}

    - id: publish
      name: Open Pull Request
      action: publish:github:pull-request
      input:
        repoUrl: github.com?repo=kubernetes&owner=arigsela
        branchName: agent/${{ parameters.name }}
        title: "Add agent: ${{ parameters.name }}"
        description: |
          Adds the `${{ parameters.name }}` agent to the platform.

          - **Surface:** ${{ parameters.surface }}
          - **Skills:** ${{ parameters.skills.length }}
          - **Owner:** ${{ parameters.owner }}

          Generated by the Backstage "Create New Agent" template.
        targetPath: base-apps/agents/${{ parameters.name }}

  output:
    links:
      - title: Pull Request
        url: ${{ steps.publish.output.remoteUrl }}
        icon: github
```

- [ ] **Step 2: Write the skeleton — `agent.yaml`**

`base-apps/backstage/templates/create-agent/skeleton/agent.yaml`:
```yaml
apiVersion: platform.arigsela.com/v1alpha1
kind: XAgent
metadata:
  name: ${{ values.name }}
  namespace: ${{ values.namespace }}
  annotations:
    platform.arigsela.com/base-domain: "<base-domain>"  # platform-managed; replace at scaffold time if templated dynamically
{%- if values.surface == "slack" and values.slackCommand %}
    platform.arigsela.com/slack-command: ${{ values.slackCommand }}
{%- endif %}
{%- if values.surface == "github-webhook" and values.githubRepo %}
    platform.arigsela.com/github-repo: ${{ values.githubRepo }}
{%- endif %}
spec:
  description: ${{ values.description | dump }}
  systemPrompt: |
    ${{ values.systemPrompt | indent(4) }}
  skills:
{%- for s in values.skills %}
    - ref: ${{ s.ref }}
{%- if s.alias %}
      alias: ${{ s.alias }}
{%- endif %}
{%- endfor %}
  surface: ${{ values.surface }}
```

- [ ] **Step 3: Write the skeleton — `catalog-info.yaml`**

`base-apps/backstage/templates/create-agent/skeleton/catalog-info.yaml`:
```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: ${{ values.name }}-agent
  description: ${{ values.description | dump }}
  annotations:
    backstage.io/kubernetes-id: ${{ values.name }}
    kagent.dev/agent-name: ${{ values.name }}
    langfuse.platform.arigsela.com/project: golden-poc
    agent.platform.arigsela.com/try-url: https://${{ values.name }}.<base-domain>/v1/messages
spec:
  type: agent
  lifecycle: experimental
  owner: ${{ values.owner }}
```

- [ ] **Step 4: Register the Template in Backstage's catalog locations**

Edit `base-apps/backstage/configmaps.yaml`. Find the `app-config.yaml` section (or whichever ConfigMap holds Backstage's app-config). Add to `catalog.locations`:

```yaml
catalog:
  locations:
    # ... existing locations ...
    - type: url
      target: https://github.com/arigsela/kubernetes/blob/main/base-apps/backstage/templates/create-agent/template.yaml
      rules:
        - allow: [Template]
```

- [ ] **Step 5: Commit**

```bash
git add base-apps/backstage/templates/create-agent/ base-apps/backstage/configmaps.yaml
git commit -m "feat(backstage): Software Template 'Create New Agent' (Phase 3)"
```

After ArgoCD sync (or Backstage's catalog refresh interval — usually 5min): Template appears in Backstage at "Create" → "Create New Agent".

---

## Task 3.2: End-to-end test: scaffold a third agent through the template

**Files:** None (verification-only); produces a PR and a third agent in `base-apps/agents/`

- [ ] **Step 1: Open Backstage in a browser**

Navigate to `https://backstage.<base-domain>` → "Create" → "Create New Agent".

- [ ] **Step 2: Fill out the form for a test agent**

- Name: `echo-test`
- Namespace: `agents`
- Owner: pick yourself or platform-team
- Description: "Test agent that echoes input back. Used to verify the Software Template scaffolds correctly."
- System prompt: "You are a simple echo agent. Reply with exactly: '[ECHO] ' followed by the user's last message."
- Skills: 1 entry — `oci://agentregistry.agentregistry.svc/skills/kubernetes-mcp:v1` (you don't actually need it; just satisfies the minItems: 1 requirement). Alias `k8s`.
- Surface: `http` (no Slack or GitHub wiring needed — minimal smoke test)

Click Create.

- [ ] **Step 3: Verify the PR opens with the right contents**

Backstage redirects to a PR URL. Confirm:
- Branch: `agent/echo-test`
- Files added: `base-apps/agents/echo-test/agent.yaml` and `base-apps/agents/echo-test/catalog-info.yaml`
- Contents match the values you submitted

- [ ] **Step 4: Merge the PR**

Merge to main. ArgoCD syncs; Crossplane Composition reconciles; the kagent Agent comes up.

- [ ] **Step 5: Verify the agent appears in the Backstage catalog**

Wait ~2 minutes (Backstage catalog refresh + Kubernetes plugin discovery).

In Backstage, navigate to Catalog → filter by Type: agent. Expected: `echo-test-agent` appears alongside `cluster-health-agent` and `pr-review-agent`.

- [ ] **Step 6: Smoke-test the rendered agent**

```bash
kubectl run curl-test --rm -i --image=curlimages/curl --restart=Never --quiet -- \
  curl -sS -X POST http://echo-test.agents.svc.cluster.local/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"hello world"}]}'
```

Expected: response containing "[ECHO] hello world".

If everything works: the template is functional. Don't delete `echo-test` yet — Phase 3 Task 3.3's Try-it card uses it as a demo target.

---

## Task 3.3: Build the per-agent Try-it card plugin

**Files:**
- Create: `services/backstage-plugin-agent-tryit/package.json`
- Create: `services/backstage-plugin-agent-tryit/src/plugin.ts`
- Create: `services/backstage-plugin-agent-tryit/src/components/TryItCard/TryItCard.tsx`
- Create: `services/backstage-plugin-agent-tryit/src/components/TryItCard/index.ts`
- Create: `services/backstage-plugin-agent-tryit/src/api/AgentApi.ts`
- Create: `services/backstage-plugin-agent-tryit/src/api/LangfuseApi.ts`
- Create: `services/backstage-plugin-agent-tryit/src/index.ts`
- Modify: Backstage app `packages/app/src/App.tsx` (register plugin) — done via the Backstage app build, not in this repo

Backstage frontend plugin architecture: the plugin code lives outside this repo (in the Backstage app's `plugins/` directory). For GitOps, the *configuration* and the *plugin source* both go into the repo as scaffolding — the actual `yarn build` happens in the Backstage Docker image build, which is out of scope for this plan.

For Phase 3, we ship the plugin source as a scaffold under `services/` so it lives in this repo for review, with a README documenting how to wire it into the Backstage app build.

- [ ] **Step 1: Scaffold the plugin via Backstage CLI (one-time, requires Node)**

Locally (not in cluster):
```bash
cd services/
npx @backstage/cli new --select frontend-plugin --option id=agent-tryit
```

This generates `services/backstage-plugin-agent-tryit/` with the standard structure. Move/rename if needed to match the file paths above.

- [ ] **Step 2: Implement the AgentApi (proxy through Backstage backend)**

The Try-it button POSTs to the agent's Service. Direct browser → cluster Service is blocked (ingress allowlists). Route via Backstage's backend proxy.

`services/backstage-plugin-agent-tryit/src/api/AgentApi.ts`:
```typescript
import { createApiRef, DiscoveryApi, FetchApi } from '@backstage/core-plugin-api';

export interface AgentMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface AgentApi {
  invoke(tryUrl: string, messages: AgentMessage[]): Promise<string>;
}

export const agentApiRef = createApiRef<AgentApi>({
  id: 'plugin.agent-tryit.agent',
});

export class AgentApiClient implements AgentApi {
  constructor(
    private readonly discovery: DiscoveryApi,
    private readonly fetch: FetchApi,
  ) {}

  async invoke(tryUrl: string, messages: AgentMessage[]): Promise<string> {
    // Backstage backend proxy must be configured to forward
    // /api/proxy/agents/<host> -> https://<host>/. See README for proxy config.
    const u = new URL(tryUrl);
    const proxyPath = await this.discovery.getBaseUrl('proxy');
    const url = `${proxyPath}/agents/${u.hostname}${u.pathname}`;
    const res = await this.fetch.fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages }),
    });
    if (!res.ok) {
      throw new Error(`Agent returned ${res.status}: ${await res.text()}`);
    }
    const body = await res.json();
    // kagent shape: { content: [{ type: 'text', text: '...' }] }
    return (body.content ?? [])
      .filter((c: any) => c.type === 'text')
      .map((c: any) => c.text)
      .join('\n');
  }
}
```

- [ ] **Step 3: Implement the LangfuseApi (recent invocations)**

`services/backstage-plugin-agent-tryit/src/api/LangfuseApi.ts`:
```typescript
import { createApiRef, DiscoveryApi, FetchApi } from '@backstage/core-plugin-api';

export interface LangfuseTrace {
  id: string;
  name: string;
  timestamp: string;
  latencyMs: number;
  totalTokens?: number;
  url: string;
}

export interface LangfuseApi {
  recentTraces(agentName: string, limit?: number): Promise<LangfuseTrace[]>;
}

export const langfuseApiRef = createApiRef<LangfuseApi>({
  id: 'plugin.agent-tryit.langfuse',
});

export class LangfuseApiClient implements LangfuseApi {
  constructor(
    private readonly discovery: DiscoveryApi,
    private readonly fetch: FetchApi,
    private readonly project: string = 'golden-poc',
  ) {}

  async recentTraces(agentName: string, limit = 10): Promise<LangfuseTrace[]> {
    // Proxy: /api/proxy/langfuse/api/public/traces?tags=<agent>&limit=10
    const proxyBase = await this.discovery.getBaseUrl('proxy');
    const url = `${proxyBase}/langfuse/api/public/traces?tags=${encodeURIComponent(agentName)}&limit=${limit}`;
    const res = await this.fetch.fetch(url);
    if (!res.ok) throw new Error(`Langfuse returned ${res.status}`);
    const body = await res.json();
    return (body.data ?? []).map((t: any) => ({
      id: t.id,
      name: t.name ?? '(unnamed)',
      timestamp: t.timestamp,
      latencyMs: t.latency ?? 0,
      totalTokens: t.totalTokens,
      url: `${t.htmlPath ?? '/trace/' + t.id}`,
    }));
  }
}
```

- [ ] **Step 4: Implement the TryItCard component**

`services/backstage-plugin-agent-tryit/src/components/TryItCard/TryItCard.tsx`:
```tsx
import React, { useEffect, useState } from 'react';
import {
  Card, CardContent, CardHeader, TextField, Button,
  Typography, CircularProgress, Box, Link, Divider,
} from '@material-ui/core';
import { useEntity } from '@backstage/plugin-catalog-react';
import { useApi } from '@backstage/core-plugin-api';
import { agentApiRef } from '../../api/AgentApi';
import { langfuseApiRef, LangfuseTrace } from '../../api/LangfuseApi';

const TRY_URL_ANNOTATION = 'agent.platform.arigsela.com/try-url';
const KAGENT_NAME_ANNOTATION = 'kagent.dev/agent-name';

export const TryItCard: React.FC = () => {
  const { entity } = useEntity();
  const agentApi = useApi(agentApiRef);
  const langfuseApi = useApi(langfuseApiRef);

  const tryUrl = entity.metadata.annotations?.[TRY_URL_ANNOTATION];
  const agentName = entity.metadata.annotations?.[KAGENT_NAME_ANNOTATION];

  const [input, setInput] = useState('');
  const [response, setResponse] = useState('');
  const [loading, setLoading] = useState(false);
  const [traces, setTraces] = useState<LangfuseTrace[]>([]);

  useEffect(() => {
    if (!agentName) return;
    langfuseApi.recentTraces(agentName, 5).then(setTraces).catch(() => {});
  }, [agentName, langfuseApi]);

  if (!tryUrl) {
    return (
      <Card>
        <CardHeader title="Try this agent" />
        <CardContent>
          <Typography color="textSecondary">
            No <code>{TRY_URL_ANNOTATION}</code> annotation found on this Component.
          </Typography>
        </CardContent>
      </Card>
    );
  }

  const onSend = async () => {
    setLoading(true);
    setResponse('');
    try {
      const out = await agentApi.invoke(tryUrl, [{ role: 'user', content: input }]);
      setResponse(out);
    } catch (e: any) {
      setResponse(`Error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader title="Try this agent" subheader={tryUrl} />
      <CardContent>
        <TextField
          fullWidth
          multiline
          minRows={3}
          variant="outlined"
          label="Your message"
          value={input}
          onChange={e => setInput(e.target.value)}
          disabled={loading}
        />
        <Box mt={2}>
          <Button
            variant="contained"
            color="primary"
            onClick={onSend}
            disabled={loading || !input}
            startIcon={loading ? <CircularProgress size={16} /> : null}
          >
            {loading ? 'Calling agent...' : 'Send'}
          </Button>
        </Box>
        {response && (
          <Box mt={3}>
            <Typography variant="subtitle2">Response</Typography>
            <Box mt={1} p={2} bgcolor="grey.100" borderRadius={4}>
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{response}</pre>
            </Box>
          </Box>
        )}
        <Divider style={{ margin: '24px 0' }} />
        <Typography variant="subtitle2">Recent traces (Langfuse)</Typography>
        {traces.length === 0 ? (
          <Typography color="textSecondary" variant="body2">No recent traces.</Typography>
        ) : (
          <ul>
            {traces.map(t => (
              <li key={t.id}>
                <Link href={t.url} target="_blank" rel="noopener">
                  {new Date(t.timestamp).toLocaleString()} — {t.latencyMs}ms{t.totalTokens ? `, ${t.totalTokens} tokens` : ''}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
};
```

- [ ] **Step 5: Wire the API factories + plugin entry point**

`services/backstage-plugin-agent-tryit/src/plugin.ts`:
```typescript
import {
  createPlugin,
  createApiFactory,
  discoveryApiRef,
  fetchApiRef,
} from '@backstage/core-plugin-api';
import { agentApiRef, AgentApiClient } from './api/AgentApi';
import { langfuseApiRef, LangfuseApiClient } from './api/LangfuseApi';

export const agentTryitPlugin = createPlugin({
  id: 'agent-tryit',
  apis: [
    createApiFactory({
      api: agentApiRef,
      deps: { discovery: discoveryApiRef, fetch: fetchApiRef },
      factory: ({ discovery, fetch }) => new AgentApiClient(discovery, fetch),
    }),
    createApiFactory({
      api: langfuseApiRef,
      deps: { discovery: discoveryApiRef, fetch: fetchApiRef },
      factory: ({ discovery, fetch }) => new LangfuseApiClient(discovery, fetch),
    }),
  ],
});
```

`services/backstage-plugin-agent-tryit/src/index.ts`:
```typescript
export { agentTryitPlugin } from './plugin';
export { TryItCard } from './components/TryItCard';
```

- [ ] **Step 6: Document the Backstage app integration in README**

`services/backstage-plugin-agent-tryit/README.md`:
````markdown
# @internal/plugin-agent-tryit

Backstage frontend plugin for the Golden POC. Adds a "Try this agent" card
on Component pages where the Component has type=agent.

## Wiring into the Backstage app

In your Backstage app repo:

1. Add this plugin as a workspace dependency:
   ```
   yarn workspace app add @internal/plugin-agent-tryit
   ```

2. Register the card on Components of type=agent in `packages/app/src/components/catalog/EntityPage.tsx`:
   ```tsx
   import { TryItCard } from '@internal/plugin-agent-tryit';

   const agentEntityPage = (
     <EntityLayout>
       <EntityLayout.Route path="/" title="Overview">
         <Grid container spacing={3}>
           <Grid item md={6}><TryItCard /></Grid>
           <Grid item md={6}><EntityAboutCard /></Grid>
           <Grid item md={12}><EntityKubernetesContent /></Grid>
         </Grid>
       </EntityLayout.Route>
     </EntityLayout>
   );

   // Add to entityPage:
   // {entity.spec?.type === 'agent' ? agentEntityPage : ...}
   ```

3. Configure the proxy in `app-config.yaml`:
   ```yaml
   proxy:
     endpoints:
       '/agents':
         target: 'http://internal-agents-router.agents.svc.cluster.local'
         changeOrigin: true
       '/langfuse':
         target: 'https://langfuse.<base-domain>'
         headers:
           Authorization: "Basic ${LANGFUSE_AUTH_HEADER}"
   ```

   `LANGFUSE_AUTH_HEADER` is the base64 of `<public_key>:<secret_key>` (staged
   in Vault as `k8s-secrets/langfuse-project`).

4. Rebuild and redeploy the Backstage container image.
````

- [ ] **Step 7: Commit**

```bash
git add services/backstage-plugin-agent-tryit/
git commit -m "feat(backstage-plugin-agent-tryit): per-agent Try-it card + Langfuse traces (Phase 3)"
```

- [ ] **Step 8: Rebuild Backstage image (out-of-this-repo)**

In your Backstage app repo (not this repo):
1. Add the workspace dep, register the card, configure the proxy per the README.
2. Rebuild + push the Backstage Docker image.
3. Bump the image tag in `base-apps/backstage/deployments.yaml`.
4. Commit + ArgoCD picks up.

Verify in browser: navigate to `https://backstage.<base-domain>` → Catalog → `cluster-health-agent` → expected: "Try this agent" card visible. Type a message, click Send. Response from the agent appears.

---

## Task 3.4: Build the embedded skill catalog plugin

**Files:**
- Create: `services/backstage-plugin-skill-catalog/package.json`
- Create: `services/backstage-plugin-skill-catalog/src/plugin.ts`
- Create: `services/backstage-plugin-skill-catalog/src/api/AgentRegistryApi.ts`
- Create: `services/backstage-plugin-skill-catalog/src/components/SkillCatalogPage/SkillCatalogPage.tsx`
- Create: `services/backstage-plugin-skill-catalog/src/index.ts`
- Create: `services/backstage-plugin-skill-catalog/README.md`

**ADAPTATION NOTE:** The actual API paths and response shapes for agentregistry come from Phase 0 Preflight 2. Open `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md` and use the documented endpoints in `AgentRegistryApi.ts`. The code below assumes a standard REST pattern — update field names if Preflight 2 found different.

- [ ] **Step 1: Scaffold the plugin**

```bash
cd services/
npx @backstage/cli new --select frontend-plugin --option id=skill-catalog
```

- [ ] **Step 2: Implement the AgentRegistryApi**

`services/backstage-plugin-skill-catalog/src/api/AgentRegistryApi.ts`:
```typescript
import { createApiRef, DiscoveryApi, FetchApi } from '@backstage/core-plugin-api';

export interface Skill {
  name: string;
  type: 'mcp-server' | 'skill' | 'agent' | 'prompt';
  version: string;
  description: string;
  tags: string[];
  ociRef: string;
}

export interface AgentRegistryApi {
  list(filters?: { type?: string; tag?: string }): Promise<Skill[]>;
}

export const agentRegistryApiRef = createApiRef<AgentRegistryApi>({
  id: 'plugin.skill-catalog.agentregistry',
});

export class AgentRegistryApiClient implements AgentRegistryApi {
  constructor(
    private readonly discovery: DiscoveryApi,
    private readonly fetch: FetchApi,
  ) {}

  async list(filters: { type?: string; tag?: string } = {}): Promise<Skill[]> {
    const proxyBase = await this.discovery.getBaseUrl('proxy');
    const params = new URLSearchParams();
    if (filters.type) params.set('type', filters.type);
    if (filters.tag) params.set('tag', filters.tag);
    // ADAPT: replace /api/v1/skills with the actual endpoint from Preflight 2.
    const res = await this.fetch.fetch(`${proxyBase}/agentregistry/api/v1/skills?${params}`);
    if (!res.ok) throw new Error(`agentregistry returned ${res.status}`);
    const body = await res.json();
    // ADAPT: shape per Preflight 2.
    return (body.items ?? body.data ?? []).map((s: any) => ({
      name: s.name,
      type: s.type ?? 'mcp-server',
      version: s.latestVersion ?? s.version ?? 'unknown',
      description: s.description ?? '',
      tags: s.tags ?? [],
      ociRef: `oci://agentregistry.agentregistry.svc/skills/${s.name}:${s.latestVersion ?? s.version}`,
    }));
  }
}
```

- [ ] **Step 3: Implement the catalog page**

`services/backstage-plugin-skill-catalog/src/components/SkillCatalogPage/SkillCatalogPage.tsx`:
```tsx
import React, { useEffect, useState } from 'react';
import {
  Page, Header, Content, ContentHeader,
  InfoCard, Table, TableColumn,
} from '@backstage/core-components';
import { useApi } from '@backstage/core-plugin-api';
import { Chip, IconButton, Tooltip } from '@material-ui/core';
import FileCopyIcon from '@material-ui/icons/FileCopy';
import { agentRegistryApiRef, Skill } from '../../api/AgentRegistryApi';

const columns = (): TableColumn<Skill>[] => [
  { title: 'Name',        field: 'name',        defaultSort: 'asc' },
  { title: 'Type',        field: 'type',
    render: (s) => <Chip label={s.type} size="small" /> },
  { title: 'Version',     field: 'version' },
  { title: 'Description', field: 'description' },
  { title: 'Tags',
    render: (s) => s.tags.map(t => <Chip key={t} label={t} size="small" />) },
  { title: 'OCI ref',
    render: (s) => (
      <span style={{ fontFamily: 'monospace', fontSize: 12 }}>
        {s.ociRef}
        <Tooltip title="Copy">
          <IconButton size="small" onClick={() => navigator.clipboard.writeText(s.ociRef)}>
            <FileCopyIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </span>
    ),
  },
];

export const SkillCatalogPage: React.FC = () => {
  const api = useApi(agentRegistryApiRef);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.list().then(setSkills).catch(e => setErr(e.message)).finally(() => setLoading(false));
  }, [api]);

  return (
    <Page themeId="tool">
      <Header title="Skill Catalog" subtitle="agentregistry-backed catalog of skills, MCP servers, agents, prompts" />
      <Content>
        <ContentHeader title="All artifacts" />
        {err ? (
          <InfoCard title="Failed to load">
            <pre>{err}</pre>
          </InfoCard>
        ) : (
          <Table
            columns={columns()}
            data={skills}
            options={{ search: true, paging: true, pageSize: 20 }}
            isLoading={loading}
          />
        )}
      </Content>
    </Page>
  );
};
```

- [ ] **Step 4: Plugin entry + route**

`services/backstage-plugin-skill-catalog/src/plugin.ts`:
```typescript
import {
  createPlugin,
  createApiFactory,
  discoveryApiRef,
  fetchApiRef,
  createRouteRef,
} from '@backstage/core-plugin-api';
import { createRoutableExtension } from '@backstage/core-plugin-api';
import { agentRegistryApiRef, AgentRegistryApiClient } from './api/AgentRegistryApi';

export const rootRouteRef = createRouteRef({ id: 'skill-catalog' });

export const skillCatalogPlugin = createPlugin({
  id: 'skill-catalog',
  apis: [
    createApiFactory({
      api: agentRegistryApiRef,
      deps: { discovery: discoveryApiRef, fetch: fetchApiRef },
      factory: ({ discovery, fetch }) => new AgentRegistryApiClient(discovery, fetch),
    }),
  ],
  routes: { root: rootRouteRef },
});

export const SkillCatalogIndexPage = skillCatalogPlugin.provide(
  createRoutableExtension({
    name: 'SkillCatalogIndexPage',
    component: () => import('./components/SkillCatalogPage/SkillCatalogPage').then(m => m.SkillCatalogPage),
    mountPoint: rootRouteRef,
  }),
);
```

`services/backstage-plugin-skill-catalog/src/index.ts`:
```typescript
export { skillCatalogPlugin, SkillCatalogIndexPage } from './plugin';
```

- [ ] **Step 5: README — wiring into Backstage app**

`services/backstage-plugin-skill-catalog/README.md`:
````markdown
# @internal/plugin-skill-catalog

Top-level page at `/skills` showing artifacts from the in-cluster
agentregistry instance.

## Wiring

1. `yarn workspace app add @internal/plugin-skill-catalog`
2. In `packages/app/src/App.tsx`:
   ```tsx
   import { SkillCatalogIndexPage } from '@internal/plugin-skill-catalog';
   // inside <FlatRoutes>:
   <Route path="/skills" element={<SkillCatalogIndexPage />} />
   ```
3. Add a sidebar item in `packages/app/src/components/Root/Root.tsx`:
   ```tsx
   <SidebarItem icon={ExtensionIcon} to="skills" text="Skills" />
   ```
4. Configure the proxy in `app-config.yaml` (already added in agent-tryit Task 3.3 README):
   ```yaml
   proxy:
     endpoints:
       '/agentregistry':
         target: 'http://agentregistry.agentregistry.svc.cluster.local:12121'
         changeOrigin: true
   ```
5. Rebuild Backstage image, bump `base-apps/backstage/deployments.yaml`.
````

- [ ] **Step 6: Commit**

```bash
git add services/backstage-plugin-skill-catalog/
git commit -m "feat(backstage-plugin-skill-catalog): /skills page backed by agentregistry (Phase 3)"
```

- [ ] **Step 7: Rebuild Backstage image; verify in browser**

(Same out-of-repo Backstage rebuild as Task 3.3 Step 8.)

After deploy: open `https://backstage.<base-domain>/skills`. Expected: a table with the four POC skills (kubernetes-mcp, prometheus-mcp, github-mcp, k8s-yaml-lint), search and tag filtering work, "Copy OCI ref" buttons work.

---

## Task 3.5: End-to-end Phase 3 verification

**Files:**
- Modify: `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md`

- [ ] **Step 1: Verify the full Backstage agent journey**

In Backstage:
1. Open `/skills`. Find a skill, copy its OCI ref.
2. Click Create → "Create New Agent". Fill the form, paste the OCI ref into Skills.
3. Submit. PR opens.
4. Merge the PR.
5. After ~2 min, the agent appears in the Catalog.
6. Open the agent's page. The "Try this agent" card is visible.
7. Type a message, click Send. Response appears.
8. Recent traces section shows at least one trace.

If all eight steps work: Phase 3 is complete.

- [ ] **Step 2: Append Phase 3 status to preflight-results.md**

```markdown
## Phase 3 — Status

- [x] "Create New Agent" Software Template scaffolds two-file PRs.
- [x] echo-test agent created end-to-end via the template.
- [x] backstage-plugin-agent-tryit deployed; Try-it card works on agent pages.
- [x] backstage-plugin-skill-catalog deployed; /skills page lists agentregistry artifacts.
- [x] Backstage proxy configured for /agents and /langfuse and /agentregistry.

**Phase 4 ready to start.**
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md
git commit -m "docs(golden-poc): Phase 3 verification complete"
```

Phase 3 complete. Engineers self-serve agents through Backstage; the demo's "front door" is real. Phase 4 is demo polish.
