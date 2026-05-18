# Kagent Agent IDP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Self-service Backstage scaffolder flows to create and decommission kagent.dev declarative Agents (orchestrator-style, agent-delegation only).

**Architecture:** Two scaffolder templates + two custom scaffolder actions in the `arigsela/backstage` repo (working in `docs/reference/backstage/` which is a clone of that repo). Create flow renders one Agent CRD per file into `base-apps/kagent/agents/<name>.yaml` and opens a PR to `arigsela/kubernetes`. Decommission flow uses a custom Octokit-based action that verifies the IDP-management label before deleting the file via a PR. Catalog ingestion uses TeraSky's existing `kubernetesIngestor` (annotation-driven).

**Tech Stack:** TypeScript, Backstage scaffolder, Octokit REST, Jest, Nunjucks (template engine), Yarn workspaces.

**Companion spec:** `docs/superpowers/specs/2026-05-18-kagent-idp-design.md`

**Working directory:** All file paths in this plan are relative to `/Users/arisela/git/kubernetes/docs/reference/backstage/` (the backstage repo clone) UNLESS the path begins with `base-apps/` or `docs/`, in which case it's relative to `/Users/arisela/git/kubernetes/` (this kubernetes repo).

---

## File Structure

### Files to CREATE in `arigsela/backstage` (i.e. `docs/reference/backstage/`)

| File | Responsibility |
|---|---|
| `packages/backend/src/modules/scaffolder/kagentValidateNameAction.ts` | Custom action `kagent:agent:validate-name`. Fails the wizard if the chosen name collides with an existing agent file. |
| `packages/backend/src/modules/scaffolder/kagentValidateNameAction.test.ts` | Jest tests (4 cases). |
| `packages/backend/src/modules/scaffolder/kagentDecommissionAction.ts` | Custom action `kagent:agent:open-decommission-pr`. Existence check + management-label check + branch + file delete + PR (idempotent). |
| `packages/backend/src/modules/scaffolder/kagentDecommissionAction.test.ts` | Jest tests (6 cases). |
| `examples/templates/kagent-agent/template.yaml` | Create wizard — 5 pages. |
| `examples/templates/kagent-agent/content/base-apps/kagent/agents/${{ values.name }}.yaml` | Nunjucks-templated Agent CRD that gets rendered into the workspace. |
| `examples/templates/kagent-agent-decommission/template.yaml` | Decommission wizard — single page. |

### Files to MODIFY in `arigsela/backstage`

| File | Change |
|---|---|
| `packages/backend/src/modules/scaffolder/index.ts` | Add imports + `addActions` entries for both new actions. |
| `app-config.yaml` | Add two new catalog `locations` entries for the new templates. |

### Files this plan does NOT modify

- `base-apps/kagent.yaml` — `ignoreDifferences` block is already in place
- `base-apps/kagent-secrets.yaml` — already syncs `base-apps/kagent/` recursively
- Any kagent chart values

### What the user is responsible for (out of scope of this plan)

- Building + deploying the updated backstage container image (per project convention "I will handle running the builds of images")
- Merging PRs created during E2E smoke tests

---

## Phase 1 — Custom scaffolder actions

### Task 1: Implement `kagent:agent:validate-name` action with tests

**Why:** Prevent the create wizard from silently overwriting `base-apps/kagent/build-orchestrator.yaml` or a prior IDP-created agent. Fail at wizard time, not at PR-conflict time, for clear UX.

**Files:**
- Create: `packages/backend/src/modules/scaffolder/kagentValidateNameAction.test.ts`
- Create: `packages/backend/src/modules/scaffolder/kagentValidateNameAction.ts`

- [ ] **Step 1: Create the test file**

Write `packages/backend/src/modules/scaffolder/kagentValidateNameAction.test.ts`:

```typescript
/**
 * Unit tests for kagent:agent:validate-name action.
 *
 * Tests use jest.mock('@octokit/rest') to mock the Octokit client.
 * ActionContext is mocked manually (createMockActionContext is not exported
 * by @backstage/plugin-scaffolder-node@0.12.5).
 *
 * Test cases:
 *   1. happy_path_name_available
 *   2. name_collides_in_agents_subdir
 *   3. name_collides_at_top_level (build-orchestrator collision)
 *   4. missing_github_token_throws
 */

import { createKagentValidateNameAction } from './kagentValidateNameAction';

jest.mock('@octokit/rest');
import { Octokit } from '@octokit/rest';

const MockedOctokit = Octokit as jest.MockedClass<typeof Octokit>;

function buildMockOctokit() {
  return {
    repos: {
      getContent: jest.fn(),
    },
  } as any;
}

function createMockActionContext(opts: { input: Record<string, unknown> }) {
  return {
    input: opts.input,
    output: jest.fn(),
    logger: {
      info: jest.fn(),
      warn: jest.fn(),
      error: jest.fn(),
      debug: jest.fn(),
      child: jest.fn().mockReturnThis(),
    },
    workspacePath: '/tmp/test-workspace',
    checkpoint: jest.fn(),
    createTemporaryDirectory: jest.fn().mockResolvedValue('/tmp/test-temp'),
    getInitiatorCredentials: jest.fn(),
    task: { id: 'test-task-id' },
  } as any;
}

describe('kagent:agent:validate-name', () => {
  let originalToken: string | undefined;

  beforeEach(() => {
    originalToken = process.env.GITHUB_TOKEN;
    process.env.GITHUB_TOKEN = 'fake-test-token';
    MockedOctokit.mockClear();
  });

  afterEach(() => {
    if (originalToken === undefined) {
      delete process.env.GITHUB_TOKEN;
    } else {
      process.env.GITHUB_TOKEN = originalToken;
    }
  });

  it('happy_path_name_available: both lookups 404, no error', async () => {
    const mock = buildMockOctokit();
    MockedOctokit.mockImplementation(() => mock);

    // Both candidate paths return 404 — name is available
    mock.repos.getContent
      .mockRejectedValueOnce({ status: 404 })
      .mockRejectedValueOnce({ status: 404 });

    const ctx = createMockActionContext({ input: { name: 'release-coordinator' } });
    const action = createKagentValidateNameAction();
    await action.handler(ctx);

    expect(mock.repos.getContent).toHaveBeenCalledTimes(2);
    expect(mock.repos.getContent).toHaveBeenCalledWith(
      expect.objectContaining({ path: 'base-apps/kagent/release-coordinator.yaml' }),
    );
    expect(mock.repos.getContent).toHaveBeenCalledWith(
      expect.objectContaining({ path: 'base-apps/kagent/agents/release-coordinator.yaml' }),
    );
  });

  it('name_collides_in_agents_subdir: throws with clear error', async () => {
    const mock = buildMockOctokit();
    MockedOctokit.mockImplementation(() => mock);

    // Top-level 404, agents-subdir hit
    mock.repos.getContent
      .mockRejectedValueOnce({ status: 404 })
      .mockResolvedValueOnce({ data: { sha: 'existing-sha' } });

    const ctx = createMockActionContext({ input: { name: 'duplicate-agent' } });
    const action = createKagentValidateNameAction();

    await expect(action.handler(ctx)).rejects.toThrow(
      "Agent 'duplicate-agent' already exists at base-apps/kagent/agents/duplicate-agent.yaml. Choose a different name.",
    );
  });

  it('name_collides_at_top_level: throws with clear error', async () => {
    const mock = buildMockOctokit();
    MockedOctokit.mockImplementation(() => mock);

    // Top-level hit (matches build-orchestrator collision case)
    mock.repos.getContent.mockResolvedValueOnce({ data: { sha: 'existing-sha' } });

    const ctx = createMockActionContext({ input: { name: 'build-orchestrator' } });
    const action = createKagentValidateNameAction();

    await expect(action.handler(ctx)).rejects.toThrow(
      "Agent 'build-orchestrator' already exists at base-apps/kagent/build-orchestrator.yaml. Choose a different name.",
    );
  });

  it('missing_github_token_throws: clear operator-facing error', async () => {
    delete process.env.GITHUB_TOKEN;

    const ctx = createMockActionContext({ input: { name: 'foo' } });
    const action = createKagentValidateNameAction();

    await expect(action.handler(ctx)).rejects.toThrow(
      'GITHUB_TOKEN env var is not set. Required for kagent:agent:validate-name.',
    );
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run from the backstage repo root:

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
yarn workspace backend test --testPathPattern=kagentValidateNameAction
```

Expected: All 4 tests FAIL with `Cannot find module './kagentValidateNameAction'`.

- [ ] **Step 3: Create the action implementation**

Write `packages/backend/src/modules/scaffolder/kagentValidateNameAction.ts`:

```typescript
/**
 * Custom Scaffolder Action: kagent:agent:validate-name
 * =====================================================
 *
 * Validates that the proposed kagent Agent name does not collide with an
 * existing file at either:
 *   - base-apps/kagent/<name>.yaml         (hand-crafted agents, e.g. build-orchestrator)
 *   - base-apps/kagent/agents/<name>.yaml  (prior IDP-created agents)
 *
 * Throws with a clear error if either file exists. This fails the wizard
 * before publish:github:pull-request would conflict, giving a much better UX.
 *
 * AUTHENTICATION:
 * Reads process.env.GITHUB_TOKEN. Same token used by other Octokit actions.
 *
 * Companion spec: docs/superpowers/specs/2026-05-18-kagent-idp-design.md
 */

import { createTemplateAction } from '@backstage/plugin-scaffolder-node';
import { Octokit } from '@octokit/rest';

const OWNER = 'arigsela';
const REPO = 'kubernetes';
const TOP_LEVEL_DIR = 'base-apps/kagent';
const AGENTS_DIR = 'base-apps/kagent/agents';

function isHttpError(err: unknown): err is { status: number; message?: string } {
  return (
    typeof err === 'object' &&
    err !== null &&
    'status' in err &&
    typeof (err as any).status === 'number'
  );
}

async function fileExists(octokit: Octokit, path: string): Promise<boolean> {
  try {
    await octokit.repos.getContent({ owner: OWNER, repo: REPO, path });
    return true;
  } catch (err) {
    if (isHttpError(err) && err.status === 404) {
      return false;
    }
    throw err;
  }
}

export function createKagentValidateNameAction() {
  return createTemplateAction({
    id: 'kagent:agent:validate-name',
    description:
      'Fails if a kagent Agent with the given name already exists at either base-apps/kagent/<name>.yaml or base-apps/kagent/agents/<name>.yaml.',
    schema: {
      input: {
        name: z =>
          z
            .string()
            .regex(/^[a-z][a-z0-9-]{2,38}[a-z0-9]$/)
            .describe(
              'Proposed kagent Agent name (lowercase, hyphens, 4-40 chars).',
            ),
      },
    },

    async handler(ctx) {
      const { name } = ctx.input as { name: string };

      const token = process.env.GITHUB_TOKEN;
      if (!token) {
        throw new Error(
          'GITHUB_TOKEN env var is not set. Required for kagent:agent:validate-name.',
        );
      }

      const octokit = new Octokit({ auth: token });
      const topLevelPath = `${TOP_LEVEL_DIR}/${name}.yaml`;
      const agentsPath = `${AGENTS_DIR}/${name}.yaml`;

      ctx.logger.info(
        `kagent:validate-name — Checking for collisions on '${name}'`,
      );

      if (await fileExists(octokit, topLevelPath)) {
        throw new Error(
          `Agent '${name}' already exists at ${topLevelPath}. Choose a different name.`,
        );
      }

      if (await fileExists(octokit, agentsPath)) {
        throw new Error(
          `Agent '${name}' already exists at ${agentsPath}. Choose a different name.`,
        );
      }

      ctx.logger.info(`kagent:validate-name — Name '${name}' is available.`);
    },
  });
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
yarn workspace backend test --testPathPattern=kagentValidateNameAction
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
git add packages/backend/src/modules/scaffolder/kagentValidateNameAction.ts \
        packages/backend/src/modules/scaffolder/kagentValidateNameAction.test.ts
git commit -m "feat(scaffolder): add kagent:agent:validate-name action

Fails the create wizard when a chosen agent name collides with an existing
file under base-apps/kagent/ (either top-level hand-crafted or prior
IDP-created in agents/). 4 unit tests covering happy path + both collision
paths + missing-token.

Refs: docs/superpowers/specs/2026-05-18-kagent-idp-design.md (companion spec
in arigsela/kubernetes)"
```

---

### Task 2: Implement `kagent:agent:open-decommission-pr` action with tests

**Why:** Open a teardown PR that deletes `base-apps/kagent/agents/<name>.yaml`, but only after verifying the file (a) exists and (b) is IDP-managed (carries `app.kubernetes.io/managed-by: backstage-scaffolder` label). Prevents accidental deletion of hand-crafted agents.

**Files:**
- Create: `packages/backend/src/modules/scaffolder/kagentDecommissionAction.test.ts`
- Create: `packages/backend/src/modules/scaffolder/kagentDecommissionAction.ts`

- [ ] **Step 1: Create the test file**

Write `packages/backend/src/modules/scaffolder/kagentDecommissionAction.test.ts`:

```typescript
/**
 * Unit tests for kagent:agent:open-decommission-pr action.
 *
 * 6 test cases:
 *   1. happy_path_idp_managed_agent
 *   2. agent_not_found_404
 *   3. agent_not_idp_managed_throws
 *   4. branch_already_exists_reuses
 *   5. pr_already_open_returns_existing
 *   6. missing_github_token_throws
 */

import { createKagentDecommissionAction } from './kagentDecommissionAction';

jest.mock('@octokit/rest');
import { Octokit } from '@octokit/rest';

const MockedOctokit = Octokit as jest.MockedClass<typeof Octokit>;

function buildMockOctokit() {
  return {
    repos: {
      getContent: jest.fn(),
      getBranch: jest.fn(),
      deleteFile: jest.fn(),
    },
    git: {
      getRef: jest.fn(),
      createRef: jest.fn(),
    },
    pulls: {
      list: jest.fn(),
      create: jest.fn(),
    },
  } as any;
}

function createMockActionContext(opts: { input: Record<string, unknown> }) {
  return {
    input: opts.input,
    output: jest.fn(),
    logger: {
      info: jest.fn(),
      warn: jest.fn(),
      error: jest.fn(),
      debug: jest.fn(),
      child: jest.fn().mockReturnThis(),
    },
    workspacePath: '/tmp/test-workspace',
    checkpoint: jest.fn(),
    createTemporaryDirectory: jest.fn().mockResolvedValue('/tmp/test-temp'),
    getInitiatorCredentials: jest.fn(),
    task: { id: 'test-task-id' },
  } as any;
}

/**
 * Helper: build a base64-encoded YAML body that matches GitHub's getContent
 * response shape for a single-file fetch.
 */
function encodeYamlForGetContent(yaml: string, sha = 'agent-sha') {
  return {
    data: {
      type: 'file',
      encoding: 'base64',
      content: Buffer.from(yaml, 'utf-8').toString('base64'),
      sha,
    },
  };
}

const IDP_MANAGED_YAML = `apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: release-coordinator
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/managed-by: backstage-scaffolder
spec:
  description: test agent
`;

const HAND_CRAFTED_YAML = `apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: build-orchestrator
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
spec:
  description: hand-crafted agent
`;

describe('kagent:agent:open-decommission-pr', () => {
  let originalToken: string | undefined;

  beforeEach(() => {
    originalToken = process.env.GITHUB_TOKEN;
    process.env.GITHUB_TOKEN = 'fake-test-token';
    MockedOctokit.mockClear();
  });

  afterEach(() => {
    if (originalToken === undefined) {
      delete process.env.GITHUB_TOKEN;
    } else {
      process.env.GITHUB_TOKEN = originalToken;
    }
  });

  it('happy_path_idp_managed_agent: opens PR after deleting file', async () => {
    const mock = buildMockOctokit();
    MockedOctokit.mockImplementation(() => mock);

    // First getContent: existence + label check
    mock.repos.getContent.mockResolvedValueOnce(
      encodeYamlForGetContent(IDP_MANAGED_YAML, 'sha-1'),
    );
    // Branch doesn't exist
    mock.repos.getBranch.mockRejectedValueOnce({ status: 404 });
    mock.git.getRef.mockResolvedValueOnce({
      data: { object: { sha: 'main-sha' } },
    });
    mock.git.createRef.mockResolvedValueOnce({});
    // SHA fetch on the branch for the delete
    mock.repos.getContent.mockResolvedValueOnce(
      encodeYamlForGetContent(IDP_MANAGED_YAML, 'sha-2-on-branch'),
    );
    mock.repos.deleteFile.mockResolvedValueOnce({});
    mock.pulls.list.mockResolvedValueOnce({ data: [] });
    mock.pulls.create.mockResolvedValueOnce({
      data: {
        html_url: 'https://github.com/arigsela/kubernetes/pull/400',
        number: 400,
      },
    });

    const ctx = createMockActionContext({
      input: { name: 'release-coordinator' },
    });
    const action = createKagentDecommissionAction();
    await action.handler(ctx);

    expect(mock.repos.getContent).toHaveBeenCalledWith(
      expect.objectContaining({
        path: 'base-apps/kagent/agents/release-coordinator.yaml',
      }),
    );
    expect(mock.git.createRef).toHaveBeenCalledWith(
      expect.objectContaining({
        ref: 'refs/heads/scaffolder/decommission-kagent-release-coordinator',
      }),
    );
    expect(mock.repos.deleteFile).toHaveBeenCalledWith(
      expect.objectContaining({
        path: 'base-apps/kagent/agents/release-coordinator.yaml',
        sha: 'sha-2-on-branch',
        branch: 'scaffolder/decommission-kagent-release-coordinator',
      }),
    );
    expect(mock.pulls.create).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'chore(kagent): decommission agent release-coordinator',
        base: 'main',
      }),
    );
    expect(ctx.output).toHaveBeenCalledWith(
      'remoteUrl',
      'https://github.com/arigsela/kubernetes/pull/400',
    );
    expect(ctx.output).toHaveBeenCalledWith('prNumber', 400);
    expect(ctx.output).toHaveBeenCalledWith(
      'branchName',
      'scaffolder/decommission-kagent-release-coordinator',
    );
  });

  it('agent_not_found_404: throws with clear error', async () => {
    const mock = buildMockOctokit();
    MockedOctokit.mockImplementation(() => mock);

    mock.repos.getContent.mockRejectedValueOnce({ status: 404 });

    const ctx = createMockActionContext({ input: { name: 'does-not-exist' } });
    const action = createKagentDecommissionAction();

    await expect(action.handler(ctx)).rejects.toThrow(
      "Agent 'does-not-exist' not found at base-apps/kagent/agents/does-not-exist.yaml. Either it was already decommissioned or it is hand-crafted (only IDP-managed agents under base-apps/kagent/agents/ can be torn down via the IDP).",
    );
    expect(mock.git.createRef).not.toHaveBeenCalled();
  });

  it('agent_not_idp_managed_throws: refuses to delete hand-crafted agent', async () => {
    const mock = buildMockOctokit();
    MockedOctokit.mockImplementation(() => mock);

    // File exists but YAML lacks managed-by: backstage-scaffolder
    mock.repos.getContent.mockResolvedValueOnce(
      encodeYamlForGetContent(HAND_CRAFTED_YAML),
    );

    const ctx = createMockActionContext({ input: { name: 'build-orchestrator' } });
    const action = createKagentDecommissionAction();

    await expect(action.handler(ctx)).rejects.toThrow(
      "Agent 'build-orchestrator' is not IDP-managed (missing label app.kubernetes.io/managed-by=backstage-scaffolder). Tear down by hand to avoid removing unrelated files.",
    );
    expect(mock.git.createRef).not.toHaveBeenCalled();
    expect(mock.repos.deleteFile).not.toHaveBeenCalled();
  });

  it('branch_already_exists_reuses: skips git.createRef', async () => {
    const mock = buildMockOctokit();
    MockedOctokit.mockImplementation(() => mock);

    mock.repos.getContent.mockResolvedValueOnce(
      encodeYamlForGetContent(IDP_MANAGED_YAML),
    );
    // Branch exists
    mock.repos.getBranch.mockResolvedValueOnce({
      data: { name: 'scaffolder/decommission-kagent-foo' },
    });
    mock.repos.getContent.mockResolvedValueOnce(
      encodeYamlForGetContent(IDP_MANAGED_YAML, 'sha-on-branch'),
    );
    mock.repos.deleteFile.mockResolvedValueOnce({});
    mock.pulls.list.mockResolvedValueOnce({ data: [] });
    mock.pulls.create.mockResolvedValueOnce({
      data: {
        html_url: 'https://github.com/arigsela/kubernetes/pull/401',
        number: 401,
      },
    });

    const ctx = createMockActionContext({ input: { name: 'foo' } });
    const action = createKagentDecommissionAction();
    await action.handler(ctx);

    expect(mock.git.createRef).not.toHaveBeenCalled();
    expect(mock.repos.deleteFile).toHaveBeenCalled();
  });

  it('pr_already_open_returns_existing: skips pulls.create', async () => {
    const mock = buildMockOctokit();
    MockedOctokit.mockImplementation(() => mock);

    mock.repos.getContent.mockResolvedValueOnce(
      encodeYamlForGetContent(IDP_MANAGED_YAML),
    );
    mock.repos.getBranch.mockResolvedValueOnce({
      data: { name: 'scaffolder/decommission-kagent-bar' },
    });
    mock.repos.getContent.mockResolvedValueOnce(
      encodeYamlForGetContent(IDP_MANAGED_YAML, 'sha-on-branch'),
    );
    mock.repos.deleteFile.mockResolvedValueOnce({});
    mock.pulls.list.mockResolvedValueOnce({
      data: [
        {
          html_url: 'https://github.com/arigsela/kubernetes/pull/402',
          number: 402,
        },
      ],
    });

    const ctx = createMockActionContext({ input: { name: 'bar' } });
    const action = createKagentDecommissionAction();
    await action.handler(ctx);

    expect(mock.pulls.create).not.toHaveBeenCalled();
    expect(ctx.output).toHaveBeenCalledWith(
      'remoteUrl',
      'https://github.com/arigsela/kubernetes/pull/402',
    );
    expect(ctx.output).toHaveBeenCalledWith('prNumber', 402);
  });

  it('missing_github_token_throws: clear operator-facing error', async () => {
    delete process.env.GITHUB_TOKEN;

    const ctx = createMockActionContext({ input: { name: 'foo' } });
    const action = createKagentDecommissionAction();

    await expect(action.handler(ctx)).rejects.toThrow(
      'GITHUB_TOKEN env var is not set. Required for kagent:agent:open-decommission-pr.',
    );
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
yarn workspace backend test --testPathPattern=kagentDecommissionAction
```

Expected: All 6 tests FAIL with `Cannot find module './kagentDecommissionAction'`.

- [ ] **Step 3: Create the action implementation**

Write `packages/backend/src/modules/scaffolder/kagentDecommissionAction.ts`:

```typescript
/**
 * Custom Scaffolder Action: kagent:agent:open-decommission-pr
 * ============================================================
 *
 * Opens a teardown PR for an IDP-managed kagent.dev Agent by:
 *   1. Verifying the file exists at base-apps/kagent/agents/<name>.yaml
 *   2. Verifying the YAML carries the management label
 *      (app.kubernetes.io/managed-by: backstage-scaffolder) — refuses to
 *      touch hand-crafted agents
 *   3. Creating a `scaffolder/decommission-kagent-<name>` branch from main
 *      (idempotent — reuse if exists)
 *   4. Deleting the agent's YAML file on that branch
 *   5. Opening a PR (idempotent — return existing PR if open)
 *
 * Post-merge: ArgoCD's `kagent-secrets` Application (prune: true) removes
 * the Agent CRD from the cluster, and the kagent controller tears down the
 * Deployment + Service automatically. No manual kubectl needed.
 *
 * AUTHENTICATION:
 * Reads process.env.GITHUB_TOKEN. Token needs `repo` scope on
 * arigsela/kubernetes.
 *
 * Companion spec: docs/superpowers/specs/2026-05-18-kagent-idp-design.md
 */

import { createTemplateAction } from '@backstage/plugin-scaffolder-node';
import { Octokit } from '@octokit/rest';

const OWNER = 'arigsela';
const REPO = 'kubernetes';
const BASE_BRANCH = 'main';
const MANAGED_BY_LABEL = 'app.kubernetes.io/managed-by';
const MANAGED_BY_VALUE = 'backstage-scaffolder';

function isHttpError(err: unknown): err is { status: number; message?: string } {
  return (
    typeof err === 'object' &&
    err !== null &&
    'status' in err &&
    typeof (err as any).status === 'number'
  );
}

/**
 * Test whether a YAML body carries the IDP-management label.
 * Uses a regex rather than a YAML parser to avoid a runtime dep and to keep
 * the check tolerant of minor formatting variations (quoted/unquoted value).
 * Our scaffolder always renders the label in a predictable form.
 */
function hasManagedByLabel(yamlBody: string): boolean {
  // Match: app.kubernetes.io/managed-by: backstage-scaffolder
  // Match: app.kubernetes.io/managed-by: "backstage-scaffolder"
  // Match: app.kubernetes.io/managed-by: 'backstage-scaffolder'
  const pattern = new RegExp(
    `${MANAGED_BY_LABEL.replace(/\./g, '\\.').replace(/\//g, '\\/')}:\\s*["']?${MANAGED_BY_VALUE}["']?`,
  );
  return pattern.test(yamlBody);
}

function buildPrBody(name: string): string {
  return [
    `Decommissioning kagent Agent \`${name}\`. After merge:`,
    '',
    '1. ArgoCD `kagent-secrets` app prunes the Agent CRD within ~3 min.',
    '2. The kagent controller automatically tears down the agent\'s',
    '   Deployment and Service.',
    '3. Backstage catalog removes the Component entity within ~120s',
    '   (via TeraSky kubernetesIngestor).',
    '',
    'No manual `kubectl delete` is required.',
    '',
    'Generated by Backstage `kagent-agent-decommission` template.',
  ].join('\n');
}

export function createKagentDecommissionAction() {
  return createTemplateAction({
    id: 'kagent:agent:open-decommission-pr',
    description:
      'Opens a teardown PR for an IDP-managed kagent Agent. Verifies the agent is IDP-managed (carries app.kubernetes.io/managed-by=backstage-scaffolder label) before deleting. Idempotent: reuses existing branch + PR if found.',
    schema: {
      input: {
        name: z =>
          z
            .string()
            .regex(/^[a-z][a-z0-9-]{2,38}[a-z0-9]$/)
            .describe(
              'Agent name to tear down — must match an existing IDP-managed agent under base-apps/kagent/agents/',
            ),
      },
      output: {
        remoteUrl: z => z.string().describe('The PR URL on GitHub'),
        prNumber: z => z.number().describe('The PR number'),
        branchName: z =>
          z.string().describe('The branch the teardown PR was opened from'),
      },
    },

    async handler(ctx) {
      const { name } = ctx.input as { name: string };

      const token = process.env.GITHUB_TOKEN;
      if (!token) {
        throw new Error(
          'GITHUB_TOKEN env var is not set. Required for kagent:agent:open-decommission-pr.',
        );
      }

      const octokit = new Octokit({ auth: token });
      const branchName = `scaffolder/decommission-kagent-${name}`;
      const agentPath = `base-apps/kagent/agents/${name}.yaml`;

      ctx.logger.info(`kagent:decommission — Starting decommission for ${name}`);

      // Step 1: Existence + management-label check
      let yamlBody: string;
      try {
        const resp = await octokit.repos.getContent({
          owner: OWNER,
          repo: REPO,
          path: agentPath,
        });
        const data = resp.data as {
          type?: string;
          encoding?: string;
          content?: string;
        };
        if (data.type !== 'file' || !data.content) {
          throw new Error(
            `kagent:decommission — Unexpected response shape for ${agentPath}`,
          );
        }
        yamlBody = Buffer.from(data.content, 'base64').toString('utf-8');
      } catch (err) {
        if (isHttpError(err) && err.status === 404) {
          throw new Error(
            `Agent '${name}' not found at ${agentPath}. Either it was already decommissioned or it is hand-crafted (only IDP-managed agents under base-apps/kagent/agents/ can be torn down via the IDP).`,
          );
        }
        throw new Error(
          `kagent:decommission — Failed to fetch ${agentPath}: ${err instanceof Error ? err.message : String(err)}. Verify GITHUB_TOKEN has 'repo' scope on arigsela/kubernetes.`,
        );
      }

      if (!hasManagedByLabel(yamlBody)) {
        throw new Error(
          `Agent '${name}' is not IDP-managed (missing label ${MANAGED_BY_LABEL}=${MANAGED_BY_VALUE}). Tear down by hand to avoid removing unrelated files.`,
        );
      }

      // Step 2: Branch create or reuse
      let branchExists = false;
      try {
        await octokit.repos.getBranch({
          owner: OWNER,
          repo: REPO,
          branch: branchName,
        });
        branchExists = true;
        ctx.logger.info(
          `kagent:decommission — Reusing existing branch ${branchName}`,
        );
      } catch (err) {
        if (!isHttpError(err) || err.status !== 404) {
          throw err;
        }
      }

      if (!branchExists) {
        const mainRef = await octokit.git.getRef({
          owner: OWNER,
          repo: REPO,
          ref: `heads/${BASE_BRANCH}`,
        });
        await octokit.git.createRef({
          owner: OWNER,
          repo: REPO,
          ref: `refs/heads/${branchName}`,
          sha: mainRef.data.object.sha,
        });
        ctx.logger.info(`kagent:decommission — Created branch ${branchName}`);
      }

      // Step 3: Delete the file from the branch
      // Fetch SHA on the BRANCH (not main) because branch reuse may show a
      // different SHA after previous deletes.
      try {
        const fileOnBranch = await octokit.repos.getContent({
          owner: OWNER,
          repo: REPO,
          path: agentPath,
          ref: branchName,
        });
        const sha = (fileOnBranch.data as { sha?: string }).sha;
        if (!sha) {
          throw new Error(
            `kagent:decommission — Could not determine SHA for ${agentPath} on branch ${branchName}`,
          );
        }
        await octokit.repos.deleteFile({
          owner: OWNER,
          repo: REPO,
          path: agentPath,
          message: `chore(kagent): decommission agent ${name}`,
          sha,
          branch: branchName,
        });
        ctx.logger.info(`kagent:decommission — Deleted ${agentPath}`);
      } catch (err) {
        if (isHttpError(err) && err.status === 404) {
          ctx.logger.warn(
            `kagent:decommission — File ${agentPath} already missing from branch ${branchName}; continuing to PR step`,
          );
        } else {
          throw err;
        }
      }

      // Step 4: Create or reuse PR
      const existingPrs = await octokit.pulls.list({
        owner: OWNER,
        repo: REPO,
        head: `${OWNER}:${branchName}`,
        state: 'open',
      });

      if (existingPrs.data.length > 0) {
        const pr = existingPrs.data[0];
        ctx.logger.info(
          `kagent:decommission — Reusing existing PR #${pr.number}`,
        );
        ctx.output('remoteUrl', pr.html_url);
        ctx.output('prNumber', pr.number);
        ctx.output('branchName', branchName);
        return;
      }

      try {
        const newPr = await octokit.pulls.create({
          owner: OWNER,
          repo: REPO,
          head: branchName,
          base: BASE_BRANCH,
          title: `chore(kagent): decommission agent ${name}`,
          body: buildPrBody(name),
        });
        ctx.logger.info(`kagent:decommission — Created PR #${newPr.data.number}`);
        ctx.output('remoteUrl', newPr.data.html_url);
        ctx.output('prNumber', newPr.data.number);
        ctx.output('branchName', branchName);
      } catch (err) {
        if (
          isHttpError(err) &&
          err.status === 422 &&
          err.message &&
          /already exists/i.test(err.message)
        ) {
          ctx.logger.warn(
            `kagent:decommission — PR create raced with another caller; re-listing`,
          );
          const recheck = await octokit.pulls.list({
            owner: OWNER,
            repo: REPO,
            head: `${OWNER}:${branchName}`,
            state: 'open',
          });
          if (recheck.data.length > 0) {
            const racePr = recheck.data[0];
            ctx.output('remoteUrl', racePr.html_url);
            ctx.output('prNumber', racePr.number);
            ctx.output('branchName', branchName);
            return;
          }
        }
        throw new Error(
          `kagent:decommission — Failed to create PR: ${err instanceof Error ? err.message : String(err)}. Verify GITHUB_TOKEN has 'repo' scope on arigsela/kubernetes.`,
        );
      }
    },
  });
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
yarn workspace backend test --testPathPattern=kagentDecommissionAction
```

Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
git add packages/backend/src/modules/scaffolder/kagentDecommissionAction.ts \
        packages/backend/src/modules/scaffolder/kagentDecommissionAction.test.ts
git commit -m "feat(scaffolder): add kagent:agent:open-decommission-pr action

Opens a teardown PR for an IDP-managed kagent Agent by deleting its YAML
file at base-apps/kagent/agents/<name>.yaml. Refuses to touch hand-crafted
agents (verifies app.kubernetes.io/managed-by=backstage-scaffolder label
before deleting). Idempotent: reuses existing branch + PR if found. 6 unit
tests covering happy path + 404 + non-IDP-managed + branch/PR reuse +
missing-token.

Refs: docs/superpowers/specs/2026-05-18-kagent-idp-design.md (companion spec
in arigsela/kubernetes)"
```

---

### Task 3: Register both new actions in `index.ts`

**Why:** Backstage's backend DI system needs to know about the new actions; without registration, the template engine can't call them.

**Files:**
- Modify: `packages/backend/src/modules/scaffolder/index.ts`

- [ ] **Step 1: Read the existing file to get current state**

(Familiar from earlier exploration; current imports include `createDecommissionPullRequestAction` etc. and `addActions(...)` registers them.)

- [ ] **Step 2: Edit the file**

Apply two changes to `packages/backend/src/modules/scaffolder/index.ts`:

**Change A — add imports** (after the existing `createDecommissionPullRequestAction` import on line 33):

```typescript
import { createKagentValidateNameAction } from './kagentValidateNameAction';
import { createKagentDecommissionAction } from './kagentDecommissionAction';
```

**Change B — register the actions** (inside the existing `scaffolderActions.addActions(...)` call):

Replace:
```typescript
        scaffolderActions.addActions(
          createPublishFileAction(),
          createEcrCreateAction(),
          createEcrBuildPushAction(),
          createVaultSetupAction(),
          createDecommissionPullRequestAction(),
        );
```

with:

```typescript
        scaffolderActions.addActions(
          createPublishFileAction(),
          createEcrCreateAction(),
          createEcrBuildPushAction(),
          createVaultSetupAction(),
          createDecommissionPullRequestAction(),
          createKagentValidateNameAction(),
          createKagentDecommissionAction(),
        );
```

**Change C — update the header comment block** (lines 16-21) to list the new actions:

After the existing `crossplane:teardown:open-decommission-pr` line, add:
```
 * - kagent:agent:validate-name — Fails the wizard on kagent agent name collisions
 * - kagent:agent:open-decommission-pr — Opens a teardown PR for an IDP-managed kagent Agent
```

- [ ] **Step 3: Type-check the file**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
yarn workspace backend tsc
```

Expected: No errors. (If there's a type error, it's usually a missing import or a typo in a function name.)

- [ ] **Step 4: Run the full backend test suite**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
yarn workspace backend test
```

Expected: All tests PASS, including the previously-existing ones for the decommission and ECR actions.

- [ ] **Step 5: Commit**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
git add packages/backend/src/modules/scaffolder/index.ts
git commit -m "feat(scaffolder): register kagent validate-name + decommission actions

Wires the two new kagent actions into the backend DI extension point so
they're callable from scaffolder templates."
```

---

## Phase 2 — Scaffolder templates

### Task 4: Add create template `kagent-agent/template.yaml`

**Why:** Backstage template definition that drives the create wizard. References the validate-name action and `publish:github:pull-request` for the PR step.

**Files:**
- Create: `examples/templates/kagent-agent/template.yaml`

- [ ] **Step 1: Create the template file**

Write `examples/templates/kagent-agent/template.yaml`:

```yaml
# ==============================================================================
# Kagent Declarative Agent — Backstage Software Template
# ==============================================================================
#
# Self-service wizard for creating an orchestrator-style kagent.dev Agent.
# Renders one Agent CRD YAML into base-apps/kagent/agents/<name>.yaml in the
# arigsela/kubernetes repo. ArgoCD's `kagent-secrets` app auto-syncs the new
# file with prune: true and selfHeal: true.
#
# Companion spec: docs/superpowers/specs/2026-05-18-kagent-idp-design.md
# Wizard pages:
#   1. Identity (name, description, owner)
#   2. Behavior (systemMessage, includeBuiltinPrompts, delegateAgents)
#   3. A2A Skills (optional, repeating)
#   4. Resources (optional, all defaulted)
#   5. Publish (dryRun toggle)
# ==============================================================================

apiVersion: scaffolder.backstage.io/v1beta3
kind: Template
metadata:
  name: kagent-agent-template
  title: Kagent Declarative Agent
  description: >-
    Scaffold a new orchestrator-style kagent.dev Agent. Renders a single
    Agent CRD into base-apps/kagent/agents/<name>.yaml in the kubernetes
    GitOps repo and opens a PR for review.
  tags:
    - kagent
    - ai-agent
    - recommended

spec:
  owner: group:platform-engineering
  type: service

  parameters:
    # --- WIZARD PAGE 1: Identity ---
    - title: Identity
      required: [name, description, owner]
      properties:
        name:
          title: Agent name
          type: string
          description: >-
            Lowercase, hyphens, 4-40 chars. Becomes both the file name under
            base-apps/kagent/agents/ and the CRD metadata.name.
          pattern: "^[a-z][a-z0-9-]{2,38}[a-z0-9]$"
          ui:autofocus: true
          ui:help: "Example: release-coordinator"
        description:
          title: Description
          type: string
          description: One sentence describing what this agent does.
          ui:options:
            rows: 2
        owner:
          title: Owner
          type: string
          description: Backstage Group or User that owns this agent.
          ui:field: EntityPicker
          ui:options:
            catalogFilter:
              kind: [Group, User]

    # --- WIZARD PAGE 2: Behavior ---
    - title: Behavior
      required: [systemMessage, delegateAgents]
      properties:
        systemMessage:
          title: System message
          type: string
          description: >-
            The agent's prompt. Can use {{ "{{include \"builtin/...\"}}" }} directives
            if "Include builtin prompts" is enabled below.
          ui:widget: textarea
          ui:options:
            rows: 12
          ui:help: >-
            Example: "You are a release coordinator. Delegate helm work to
            helm-agent, rollout management to argo-rollouts-conversion-agent,
            and cluster checks to k8s-agent."
        includeBuiltinPrompts:
          title: Include builtin prompts (safety + k8s context)
          type: boolean
          description: >-
            When enabled, the agent can use {{ "{{include \"builtin/...\"}}" }} directives
            in its system message to pull in shared snippets like
            "builtin/kubernetes-context", "builtin/safety-guardrails", and
            "builtin/tool-usage-best-practices" from the kagent-builtin-prompts
            ConfigMap.
          default: true
        delegateAgents:
          title: Delegate to these agents
          type: array
          description: >-
            One or more agents this agent can delegate tasks to (A2A protocol).
            Must select at least one.
          minItems: 1
          uniqueItems: true
          items:
            type: string
            enum:
              - k8s-agent
              - helm-agent
              - istio-agent
              - kgateway-agent
              - argo-rollouts-conversion-agent
              - observability-agent
          ui:widget: checkboxes

    # --- WIZARD PAGE 3: A2A Skills (optional) ---
    - title: A2A Skills (optional)
      description: >-
        Define A2A skill metadata so the kagent UI and other agents can
        discover this agent's capabilities. Leave empty if you don't need
        the agent to advertise itself.
      properties:
        skills:
          title: Skills
          type: array
          default: []
          items:
            type: object
            required: [id, name, description]
            properties:
              id:
                type: string
                title: Skill ID (kebab-case)
                pattern: "^[a-z][a-z0-9-]*$"
              name:
                type: string
                title: Display name
              description:
                type: string
                title: Description (one sentence)
              examples:
                type: array
                title: Example prompts
                items:
                  type: string
              tags:
                type: array
                title: Tags
                items:
                  type: string

    # --- WIZARD PAGE 4: Resources (optional, all defaulted) ---
    - title: Resources (optional)
      properties:
        cpuRequest:
          type: string
          title: CPU request
          default: "100m"
        cpuLimit:
          type: string
          title: CPU limit
          default: "1000m"
        memoryRequest:
          type: string
          title: Memory request
          default: "256Mi"
        memoryLimit:
          type: string
          title: Memory limit
          default: "1Gi"
        compactionInterval:
          type: integer
          title: Compaction interval (turns)
          default: 5
          minimum: 1
        overlapSize:
          type: integer
          title: Compaction overlap size (turns)
          default: 2
          minimum: 0

    # --- WIZARD PAGE 5: Publish ---
    - title: Publish
      properties:
        dryRun:
          title: Dry run (testing mode)
          type: boolean
          description: >-
            When enabled, writes the rendered YAML to /tmp/backstage-scaffolder/<name>/
            instead of opening a PR. Use for testing the template.
          default: false

  steps:
    # Step 1: Reject duplicate names BEFORE rendering or opening a PR.
    - id: validate-name
      name: Verify agent name is available
      action: kagent:agent:validate-name
      input:
        name: ${{ parameters.name | trim }}

    # Step 2: Render the Agent CRD into the workspace.
    # The content/ directory mirrors the target repo's path structure exactly:
    # content/base-apps/kagent/agents/${{ values.name }}.yaml → workspace/base-apps/kagent/agents/<name>.yaml
    - id: fetch
      name: Render manifest
      action: fetch:template
      input:
        url: ./content
        values:
          name: ${{ parameters.name | trim }}
          description: ${{ parameters.description | trim }}
          owner: ${{ parameters.owner | trim }}
          systemMessage: ${{ parameters.systemMessage }}
          includeBuiltinPrompts: ${{ parameters.includeBuiltinPrompts }}
          delegateAgents: ${{ parameters.delegateAgents }}
          skills: ${{ parameters.skills }}
          cpuRequest: ${{ parameters.cpuRequest | trim }}
          cpuLimit: ${{ parameters.cpuLimit | trim }}
          memoryRequest: ${{ parameters.memoryRequest | trim }}
          memoryLimit: ${{ parameters.memoryLimit | trim }}
          compactionInterval: ${{ parameters.compactionInterval }}
          overlapSize: ${{ parameters.overlapSize }}

    # Step 3 (production): Open PR against arigsela/kubernetes.
    # No sourcePath / targetPath — workspace root maps to repo root (matches
    # application-template pattern).
    - id: publish
      name: Open PR to arigsela/kubernetes
      if: ${{ not parameters.dryRun }}
      action: publish:github:pull-request
      input:
        repoUrl: github.com?owner=arigsela&repo=kubernetes
        branchName: scaffolder/add-kagent-${{ parameters.name | trim }}
        title: "feat(kagent): add ${{ parameters.name | trim }} agent"
        description: |
          Adds a new IDP-managed kagent.dev Agent: `${{ parameters.name | trim }}`.

          ${{ parameters.description }}

          Generated by Backstage `kagent-agent-template`.

    # Step 4 (dry run): Write to /tmp for testing.
    - id: publish-local
      name: Write to local filesystem (dry run)
      if: ${{ parameters.dryRun }}
      action: publish:file
      input:
        path: /tmp/backstage-scaffolder/${{ parameters.name | trim }}

  output:
    links:
      - title: Pull request
        url: ${{ steps.publish.output.remoteUrl }}
      - title: Dry run output
        url: file:///tmp/backstage-scaffolder/${{ parameters.name }}
```

- [ ] **Step 2: Validate YAML parses cleanly**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
npx yaml@2.3.4 -- examples/templates/kagent-agent/template.yaml > /dev/null && echo "OK"
```

Expected: prints `OK` with no errors. (If `yaml` CLI isn't available, use `node -e "require('js-yaml').load(require('fs').readFileSync('examples/templates/kagent-agent/template.yaml','utf-8'))" && echo OK`.)

- [ ] **Step 3: Commit**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
git add examples/templates/kagent-agent/template.yaml
git commit -m "feat(scaffolder): add kagent-agent-template

Five-page wizard for creating an orchestrator-style kagent.dev Agent:
Identity / Behavior / A2A Skills / Resources / Publish. Calls
kagent:agent:validate-name to fail on collisions, then publish:github:pull-request
to PR the rendered YAML to arigsela/kubernetes.

Companion content/ directory is added in the next commit."
```

---

### Task 5: Add the create template's content (`content/base-apps/kagent/agents/${{ values.name }}.yaml`)

**Why:** The Nunjucks-templated Agent CRD that `fetch:template` renders into the workspace. The filename uses `${{ values.name }}` so it resolves to e.g. `release-coordinator.yaml` at render time.

**Files:**
- Create: `examples/templates/kagent-agent/content/base-apps/kagent/agents/${{ values.name }}.yaml`

- [ ] **Step 1: Create the directory and templated file**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
mkdir -p 'examples/templates/kagent-agent/content/base-apps/kagent/agents'
```

Then write `examples/templates/kagent-agent/content/base-apps/kagent/agents/${{ values.name }}.yaml`:

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: ${{ values.name }}
  namespace: kagent
  labels:
    app.kubernetes.io/part-of: kagent
    app.kubernetes.io/managed-by: backstage-scaffolder
  annotations:
    terasky.backstage.io/add-to-catalog: "true"
    terasky.backstage.io/component-type: kagent-agent
    backstage.io/managed-by-location: url:https://github.com/arigsela/kubernetes/blob/main/base-apps/kagent/agents/${{ values.name }}.yaml
    backstage.io/owner: ${{ values.owner }}
spec:
  description: ${{ values.description }}
  type: Declarative
  declarative:
    modelConfig: default-model-config
    memory:
      modelConfig: embedding-model-config
    runtime: python
    stream: true
{%- if values.includeBuiltinPrompts %}
    promptTemplate:
      dataSources:
      - alias: builtin
        kind: ConfigMap
        name: kagent-builtin-prompts
{%- endif %}
    context:
      compaction:
        compactionInterval: ${{ values.compactionInterval }}
        overlapSize: ${{ values.overlapSize }}
    systemMessage: |
      ${{ values.systemMessage | indent(6) }}
{%- if values.skills | length > 0 %}
    a2aConfig:
      skills:
{%- for skill in values.skills %}
      - id: ${{ skill.id }}
        name: ${{ skill.name }}
        description: ${{ skill.description }}
{%- if skill.examples and skill.examples | length > 0 %}
        examples:
{%- for example in skill.examples %}
        - ${{ example }}
{%- endfor %}
{%- endif %}
{%- if skill.tags and skill.tags | length > 0 %}
        tags:
{%- for tag in skill.tags %}
        - ${{ tag }}
{%- endfor %}
{%- endif %}
{%- endfor %}
{%- endif %}
    tools:
{%- for agentName in values.delegateAgents %}
    - type: Agent
      agent:
        name: ${{ agentName }}
{%- endfor %}
    deployment:
      resources:
        requests:
          cpu: ${{ values.cpuRequest }}
          memory: ${{ values.memoryRequest }}
        limits:
          cpu: ${{ values.cpuLimit }}
          memory: ${{ values.memoryLimit }}
```

**Why no `| parseJson`:** Confirmed during smoke test — this Backstage version's Nunjucks scope does NOT expose `parseJson`, so `{% set skillsList = values.skills | parseJson %}` fails at render time with `Error: filter not found: parseJson`. The fix is to pass `skills` through as a native array (no `| dump` in `template.yaml`, no `| parseJson` here) and iterate directly. See the Findings section at the end of this plan.

- [ ] **Step 2: Commit**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
git add 'examples/templates/kagent-agent/content/'
git commit -m "feat(scaffolder): add kagent-agent content template

Nunjucks-templated Agent CRD rendered into the workspace at
base-apps/kagent/agents/<name>.yaml. Conditional emission for
promptTemplate (builtinPrompts toggle) and a2aConfig (skills array empty).
Carries the app.kubernetes.io/managed-by=backstage-scaffolder label that
the decommission action uses to verify IDP-management."
```

---

### Task 6: Add decommission template `kagent-agent-decommission/template.yaml`

**Why:** Single-page wizard that just collects the agent name and invokes the custom decommission action.

**Files:**
- Create: `examples/templates/kagent-agent-decommission/template.yaml`

- [ ] **Step 1: Create the template file**

Write `examples/templates/kagent-agent-decommission/template.yaml`:

```yaml
# ==============================================================================
# Kagent Declarative Agent — Decommission
# ==============================================================================
#
# Opens a teardown PR that removes a Backstage-managed kagent Agent CRD.
# Refuses to delete agents that don't carry the IDP-management label.
#
# Companion spec: docs/superpowers/specs/2026-05-18-kagent-idp-design.md
# ==============================================================================

apiVersion: scaffolder.backstage.io/v1beta3
kind: Template
metadata:
  name: kagent-agent-decommission
  title: Decommission Kagent Agent
  description: >-
    Opens a teardown PR that removes a Backstage-managed kagent Agent. Refuses
    to delete agents that are not IDP-managed.
  tags:
    - kagent
    - ai-agent
    - decommission

spec:
  owner: group:platform-engineering
  type: service

  parameters:
    - title: Identity
      required: [name]
      properties:
        name:
          type: string
          title: Agent name to tear down
          description: >-
            Must match an existing IDP-managed agent under base-apps/kagent/agents/.
          pattern: "^[a-z][a-z0-9-]{2,38}[a-z0-9]$"

  steps:
    - id: publish
      name: Open teardown PR
      action: kagent:agent:open-decommission-pr
      input:
        name: ${{ parameters.name | trim }}

  output:
    links:
      - title: Teardown PR
        url: ${{ steps.publish.output.remoteUrl }}
```

- [ ] **Step 2: Validate YAML parses cleanly**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
node -e "require('js-yaml').load(require('fs').readFileSync('examples/templates/kagent-agent-decommission/template.yaml','utf-8'))" && echo OK
```

Expected: prints `OK`.

- [ ] **Step 3: Commit**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
git add examples/templates/kagent-agent-decommission/template.yaml
git commit -m "feat(scaffolder): add kagent-agent-decommission template

Single-page wizard that invokes kagent:agent:open-decommission-pr to open
a teardown PR for an IDP-managed kagent Agent."
```

---

### Task 7: Register both templates in `app-config.yaml` AND `app-config.production.yaml`

**Why:** Without these `catalog.locations` entries, the new templates won't appear in Backstage's `/create` page.

**CRITICAL:** Backstage's config layering MERGES objects but REPLACES arrays. The production config has its own `catalog.locations` block, so adding the new locations to `app-config.yaml` alone is silently dropped at startup when the production overlay loads. The new entries MUST appear in BOTH files. (Confirmed during the v1.6 deploy — the production override was the root cause of the kagent templates not appearing initially. See the Findings section.)

**Files:**
- Modify: `app-config.yaml`
- Modify: `app-config.production.yaml`

- [ ] **Step 1: Add two new entries to `app-config.yaml`**

The relevant section is `catalog.locations:` (around line 213-260). After the `decommission-application` entry (around line 251), insert:

```yaml
    # Kagent Declarative Agent — create new kagent.dev orchestrator-style agents.
    # See: examples/templates/kagent-agent/template.yaml
    - type: file
      target: ../../examples/templates/kagent-agent/template.yaml
      rules:
        - allow: [Template]

    # Decommission Kagent Agent — tear down an IDP-managed kagent agent.
    # See: examples/templates/kagent-agent-decommission/template.yaml
    - type: file
      target: ../../examples/templates/kagent-agent-decommission/template.yaml
      rules:
        - allow: [Template]
```

- [ ] **Step 2: Add the SAME two entries to `app-config.production.yaml`**

The production config has its own `catalog.locations` block. After the `decommission` entry, insert the exact same two entries — but note that paths in production config are `./examples/...` (relative to `/app` in the container), not `../../examples/...`:

```yaml
    # Kagent Declarative Agent — create new kagent.dev orchestrator-style agents.
    # See: examples/templates/kagent-agent/template.yaml
    - type: file
      target: ./examples/templates/kagent-agent/template.yaml
      rules:
        - allow: [Template]

    # Decommission Kagent Agent — tear down an IDP-managed kagent agent.
    # See: examples/templates/kagent-agent-decommission/template.yaml
    - type: file
      target: ./examples/templates/kagent-agent-decommission/template.yaml
      rules:
        - allow: [Template]
```

- [ ] **Step 3: Restart Backstage locally and verify the templates appear**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
# Make sure local postgres is running per the README
docker compose up -d
# Start the dev server
yarn dev
```

Then open http://localhost:3000/create in a browser. Verify:
- **"Kagent Declarative Agent"** appears in the template list
- **"Decommission Kagent Agent"** appears in the template list

Stop the dev server with Ctrl-C when verified.

- [ ] **Step 4: Commit**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
git add app-config.yaml app-config.production.yaml
git commit -m "feat(idp): register kagent create + decommission templates

Adds two catalog locations to BOTH app-config.yaml and app-config.production.yaml
so the kagent-agent and kagent-agent-decommission templates appear in the /create
page. The production config must be updated separately because Backstage replaces
(rather than merges) array values in layered configs."
```

---

## Phase 3 — End-to-end smoke tests

These tests require a running Backstage instance (either local dev or
deployed). They are exploratory tests that verify the end-to-end flow works
as designed. Document anything unexpected.

### Task 8: Create — dry-run smoke test

**Why:** Verifies the wizard parameters, template rendering, and the custom validation action all behave correctly without touching GitHub.

- [ ] **Step 1: Start Backstage locally (if not already running)**

```bash
cd /Users/arisela/git/kubernetes/docs/reference/backstage
docker compose up -d
yarn dev
```

Wait for `Listening on :3000` in the logs.

- [ ] **Step 2: Walk through the wizard with dryRun enabled**

In a browser:
1. Open http://localhost:3000/create
2. Click **Kagent Declarative Agent → Choose**
3. Fill in:
   - **Name:** `idp-smoke-test`
   - **Description:** `IDP smoke test agent — safe to delete`
   - **Owner:** any catalog Group/User
   - **System message:** `You are a test agent. Delegate everything to k8s-agent.`
   - **Include builtin prompts:** ON
   - **Delegate agents:** k8s-agent (only)
   - **Skills:** add one skill — id=`smoke-test`, name=`Smoke Test`, description=`Test skill`, examples=`["ping"]`, tags=`["test"]`
   - **Resources page:** accept all defaults
   - **Publish page:** check **Dry run**
4. Click **Review → Create**

Expected: Job runs to completion with no errors. The two key steps to verify:
- `validate-name` succeeds (because `idp-smoke-test` does not exist in either path)
- `publish-local` writes to `/tmp/backstage-scaffolder/idp-smoke-test/`

- [ ] **Step 3: Inspect the rendered output**

```bash
cat /tmp/backstage-scaffolder/idp-smoke-test/base-apps/kagent/agents/idp-smoke-test.yaml
```

Verify the rendered YAML:
- Has `metadata.name: idp-smoke-test`, `metadata.namespace: kagent`
- Has both labels (`app.kubernetes.io/part-of: kagent`, `app.kubernetes.io/managed-by: backstage-scaffolder`)
- Has all 4 annotations including `terasky.backstage.io/add-to-catalog: "true"`
- Has `promptTemplate.dataSources` (because builtinPrompts was on)
- Has `a2aConfig.skills` with the one skill
- Has `tools[0].agent.name: k8s-agent`
- Has default resource limits (`cpu: 1000m`, `memory: 1Gi`)

- [ ] **Step 4: Test the duplicate-name validation**

In the wizard, restart and use **Name:** `build-orchestrator` (the existing hand-crafted agent).

Expected: The `validate-name` step fails with the error:
`Agent 'build-orchestrator' already exists at base-apps/kagent/build-orchestrator.yaml. Choose a different name.`

- [ ] **Step 5: Document observations**

Note any issues found (rendering bugs, validation errors, wizard UX issues). If everything passed, no action needed.

---

### Task 9: Create — production smoke test (against the real cluster)

**Why:** Validates the full GitOps loop: PR creation → merge → ArgoCD sync → kagent reconcile → catalog ingestion.

**Prerequisites:**
- The updated Backstage image is deployed in the cluster (user handles this — see "out of scope" section above).
- `GITHUB_TOKEN` is configured in the deployed Backstage with `repo` scope on `arigsela/kubernetes`.

- [ ] **Step 1: Walk through the wizard in production Backstage**

At https://backstage.arigsela.com/create (or wherever it's deployed):

1. Choose **Kagent Declarative Agent**
2. Fill in:
   - **Name:** `idp-e2e-test`
   - **Description:** `End-to-end IDP smoke test — safe to delete`
   - **Owner:** your user
   - **System message:** `You are a test agent. Delegate cluster queries to k8s-agent.`
   - **Include builtin prompts:** ON
   - **Delegate agents:** `k8s-agent`
   - **Skills:** (leave empty)
   - **Resources:** defaults
   - **Publish:** **Dry run OFF**
3. Create

Expected: PR opened against `arigsela/kubernetes` at a URL like
`https://github.com/arigsela/kubernetes/pull/XXX`.

- [ ] **Step 2: Review and merge the PR**

In GitHub:
1. Open the PR
2. Verify the diff shows ONE new file: `base-apps/kagent/agents/idp-e2e-test.yaml`
3. Verify the file contents match expectations from Task 8 Step 3
4. Merge

- [ ] **Step 3: Verify ArgoCD sync**

Wait ~3 minutes, then:

```bash
kubectl get application kagent-secrets -n argo-cd -o jsonpath='{.status.sync.status}'
```

Expected: `Synced`.

- [ ] **Step 4: Verify the Agent CRD is accepted and ready**

```bash
kubectl get agent -n kagent idp-e2e-test -o jsonpath='{.status.conditions}'
```

Expected: Both conditions present with `status: "True"`:
- `type: Accepted, reason: Reconciled`
- `type: Ready, reason: DeploymentReady`

- [ ] **Step 5: Verify the agent's pod is running**

```bash
kubectl get pods -n kagent -l kagent.dev/agent=idp-e2e-test
```

Expected: One pod in `Running` state.

- [ ] **Step 6: Verify catalog ingestion**

Wait ~2 minutes after the agent is Ready, then refresh `https://backstage.arigsela.com/catalog`.

Expected: A new Component entity named `idp-e2e-test` (or similar — TeraSky's
naming may include a prefix/suffix).

- [ ] **Step 7: Functional check via kagent UI**

Open `https://kagent.arigsela.com`, find `idp-e2e-test`, send it a test query
like "list the pods in the kagent namespace". Verify it delegates the request
to `k8s-agent` and returns a sensible answer.

---

### Task 10: Decommission — end-to-end smoke test

**Why:** Validates the decommission action's safety checks and the full
teardown loop (PR → merge → ArgoCD prune → kagent + catalog cleanup).

- [ ] **Step 1: Test the "agent not found" path first**

In production Backstage `/create`:
1. Choose **Decommission Kagent Agent**
2. Name: `does-not-exist-xyz`
3. Create

Expected: Job fails at the `publish` step with the error:
`Agent 'does-not-exist-xyz' not found at base-apps/kagent/agents/does-not-exist-xyz.yaml. ...`

- [ ] **Step 2: Test the "not IDP-managed" safety check**

1. Choose **Decommission Kagent Agent**
2. Name: `build-orchestrator`
3. Create

Expected: Job fails with:
`Agent 'build-orchestrator' is not IDP-managed (missing label app.kubernetes.io/managed-by=backstage-scaffolder). ...`

(This is critical — verifies hand-crafted agents are protected.)

- [ ] **Step 3: Decommission the real test agent created in Task 9**

1. Choose **Decommission Kagent Agent**
2. Name: `idp-e2e-test`
3. Create

Expected: PR opened at `https://github.com/arigsela/kubernetes/pull/YYY` with:
- Title: `chore(kagent): decommission agent idp-e2e-test`
- Single file deletion: `base-apps/kagent/agents/idp-e2e-test.yaml`

- [ ] **Step 4: Test idempotency — re-run the same decommission**

Without merging the PR, re-run the decommission wizard with the same name.

Expected: Job succeeds and the output PR URL is the SAME as before (the
action reuses the existing branch + PR).

- [ ] **Step 5: Merge the teardown PR**

Merge the PR in GitHub.

- [ ] **Step 6: Verify ArgoCD prunes the agent**

Wait ~3 minutes, then:

```bash
kubectl get agent -n kagent idp-e2e-test 2>&1
```

Expected: `Error from server (NotFound): agents.kagent.dev "idp-e2e-test" not found`

- [ ] **Step 7: Verify kagent controller cleans up Deployment + Service**

```bash
kubectl get deploy,svc -n kagent | grep idp-e2e-test
```

Expected: No matches.

- [ ] **Step 8: Verify catalog removal**

Wait ~2 minutes after the agent CRD is gone. Refresh
`https://backstage.arigsela.com/catalog`.

Expected: The `idp-e2e-test` Component entity is no longer listed.

- [ ] **Step 9: Document any deviations from expected behavior**

Note anything unexpected in a follow-up issue. The implementation is
considered complete when all of Phase 1, Phase 2, and Phase 3 succeed.

---

## Done criteria

- All Phase 1 + Phase 2 tasks committed (10 commits total)
- All backend tests pass: `yarn workspace backend test` returns zero failures
- Phase 3 Task 8 dry-run succeeds and produces correct YAML
- Phase 3 Tasks 9 + 10 succeed end-to-end against the real cluster
- The deployed Backstage shows both new templates at `/create`
- An IDP-created agent has been successfully created AND decommissioned

## Known limitations carried from the spec

1. **`ignoreDifferences` block** in `base-apps/kagent.yaml` ignores
   `/spec/declarative/memory` for all Agent CRDs. IDP-rendered agents include
   the correct `memory.modelConfig` at creation, but ArgoCD won't drift-detect
   manual edits.
2. **Hardcoded delegate-agents enum** — adding a newly-enabled chart agent
   requires editing `kagent-agent/template.yaml`. v1.1 follow-up: dynamic
   field extension that lists `Agent` CRDs from the cluster.
3. **No automatic `kubectl apply --dry-run=server` validation step** — relies
   on ArgoCD to catch malformed manifests at sync time. v1.1 follow-up if
   the failure UX proves bad.

## Findings from production deployment

Two issues surfaced during the v1.6 smoke test in `arigsela/backstage` PR #19
that the plan didn't anticipate. The plan above has been updated to incorporate
the fixes (Task 5 and Task 7) so re-running this plan from scratch will not
hit them. They're recorded here so future template authors recognize the
patterns.

### 1. `app-config.production.yaml` overrides `catalog.locations` entirely

The container starts with
`node packages/backend --config app-config.yaml --config app-config.production.yaml`.
Backstage merges objects but **replaces** arrays. The production config has
its own `catalog.locations` block, so the two new kagent entries added only
to `app-config.yaml` were silently dropped at startup — the production
overlay's `catalog.locations` replaced the base config's entirely.

**Symptom:** Pod deploys cleanly, scaffolder action registration succeeds,
template YAML files are present on disk, `app-config.yaml` inside the pod
has the new locations — but `/create` shows the original templates only.

**Diagnosis tool:**
```bash
kubectl exec -n backstage <pod> -- node -e \
  "fetch('http://localhost:7007/api/catalog/entities?filter=kind=Location').then(r=>r.json()).then(d=>d.forEach(l=>console.log(l.spec?.target)))"
```
Compare against the list of locations in both `app-config.yaml` and
`app-config.production.yaml`. Anything in app-config.yaml but missing from
production was clobbered.

**Fix:** Add the new entries to both files. Task 7 above now reflects this.

### 2. `parseJson` Nunjucks filter not in this Backstage version

The original plan passed `skills` (array of objects) into `fetch:template`
as `${{ parameters.skills | dump }}` (JSON-stringified) and parsed back
inside the content template with `{% set skillsList = values.skills | parseJson %}`.
This Backstage version's Nunjucks scope does not expose `parseJson`, so the
template fails to render at the `fetch` step with:

```
Error: filter not found: parseJson
    at render (<isolated-vm>:10437:13)
```

**Diagnosis tool:**
```bash
kubectl exec -n backstage <pod> -- node -e \
  "fetch('http://localhost:7007/api/scaffolder/v2/tasks/<task-id>/events').then(r=>r.json()).then(d=>d.filter(e=>e.type==='completion').forEach(e=>console.log(JSON.stringify(e.body.error||{}))))"
```

**Fix:** Pass the array through directly with `skills: ${{ parameters.skills }}`
in `template.yaml` (no `| dump`), and iterate with
`{% for skill in values.skills %}` in the content template (no `parseJson`).
Tasks 4 and 5 above now reflect this.

### 3. ArgoCD Directory source needs `recurse: true`

The plan assumed `base-apps/kagent-secrets.yaml` already synced
`base-apps/kagent/` recursively. It did not — ArgoCD's Directory source
defaults to `recurse: false`, so files in `base-apps/kagent/agents/` were
silently skipped at sync time even after ArgoCD reconciled to the
post-merge commit.

**Symptom:** the agent's PR merges cleanly, ArgoCD reconciles to the
post-merge commit, but the Agent CRD never appears (`kubectl get agent
-n kagent <name>` returns NotFound) and is missing from the Application's
resources tree.

**Diagnosis tool:**
```bash
kubectl get application kagent-secrets -n argo-cd -o yaml | yq '.status.resources[].name'
```

**Required pre-work for this plan:** before running Phase 3 smoke tests
(or before merging any IDP-created agent PR), `base-apps/kagent-secrets.yaml`
in `arigsela/kubernetes` must include `directory.recurse: true`:

```yaml
spec:
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/kagent
    directory:
      recurse: true   # REQUIRED for the agents/ subdir to be synced
```

This was done in `arigsela/kubernetes` PR #279.
