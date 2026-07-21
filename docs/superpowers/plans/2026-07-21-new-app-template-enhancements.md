# New Application Template Enhancements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a name-collision guard, Vault secret provisioning, and an ingress IP-whitelist form field to the New Application scaffolder template.

**Architecture:** One backstage backend action (`newapp:validate-name`, mirroring `kagent:agent:validate-name`) → needs a new image. Two GitOps-only changes in the kubernetes repo (a `vault:setup` step + a `whitelist` param, both using things already in the deployed image). Land together — the validate-name step errors until the action is deployed.

**Tech Stack:** Backstage scaffolder (Nunjucks + custom actions, TypeScript + jest), the existing `vault:setup` action, the render-test harness.

## Global Constraints

- **Mirror `packages/backend/src/modules/scaffolder/kagentValidateNameAction.ts`** exactly for the new action (Octokit `repos.getContent`, `fileExists`, `throw` on collision, reads `GITHUB_TOKEN`). OWNER=`arigsela`, REPO=`kubernetes`.
- Collision check: `base-apps/<name>` (directory — `getContent` returns 200 if it exists) **OR** `base-apps/<name>.yaml` (Argo app). Throw if either exists.
- **`vault:setup` inputs** (verified in `vaultSetupAction.ts`): `vaultRole`, `namespace`, `enableAuth`, `enableKnowledge`, `serviceAccountNames`. Always pass `enableAuth: true` (seeds a `jwt-secret` placeholder → ExternalSecret green); `enableKnowledge: ${{ parameters.seedOpenaiKey }}`.
- **Whitelist default** (dex admin allowlist, verbatim): `73.7.190.154/32,170.85.56.189/32,170.85.130.202/32,104.28.177.82/32,10.0.0.0/8`.
- Skeleton templating: Backstage Nunjucks (`${{ }}` vars). The render-test harness (Jinja2 + faithful filters) must stay green; add `whitelist` to its sample.
- **Deploy ordering:** v1.4.12 (with the action) must deploy BEFORE the kubernetes template.yaml validate-name step reaches `main` (the live template is fetched from `main`; an unknown action id fails scaffolding).
- Commit trailers on every commit:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b`.

## File Structure

**backstage** (branch `new-app-validate-name`, from `homepage` — the deployed source of truth):
- Create `packages/backend/src/modules/scaffolder/newAppValidateNameAction.ts`.
- Create `packages/backend/src/modules/scaffolder/newAppValidateNameAction.test.ts`.
- Modify `packages/backend/src/modules/scaffolder/index.ts` (import + register).

**kubernetes** (branch `new-app-template-enhancements`, already has the spec):
- Modify `templates/new-app/template.yaml` (2 params, validate-name step, vault-setup step, `&vals` additions).
- Modify `templates/new-app/skeleton-ingress/base-apps/${{ values.name }}/nginx-ingress.yaml` (whitelist).
- Modify `tests/new-app-template/test_render_new_app.py` (sample gains `whitelist`).

---

## Task 1: Collision-guard action (backstage)

**Files:** create the action + test; modify `index.ts`.

- [ ] **Step 1: Create the branch**

```bash
cd /Users/arisela/git/backstage
git checkout homepage && git pull --ff-only
git checkout -b new-app-validate-name
```

- [ ] **Step 2: Create `packages/backend/src/modules/scaffolder/newAppValidateNameAction.ts`**

```ts
/**
 * Custom Scaffolder Action: newapp:validate-name
 * ===============================================
 * Fails the New Application wizard if base-apps/<name> already exists (as a
 * directory OR an Argo Application file base-apps/<name>.yaml), before
 * publish:github:pull-request would merge into / overwrite an existing app.
 * Reads process.env.GITHUB_TOKEN (same token as the other Octokit actions).
 */
import { createTemplateAction } from '@backstage/plugin-scaffolder-node';
import { Octokit } from '@octokit/rest';

const OWNER = 'arigsela';
const REPO = 'kubernetes';

function isHttpError(err: unknown): err is { status: number; message?: string } {
  return (
    typeof err === 'object' &&
    err !== null &&
    'status' in err &&
    typeof (err as any).status === 'number'
  );
}

async function pathExists(octokit: Octokit, path: string): Promise<boolean> {
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

export function createNewAppValidateNameAction() {
  return createTemplateAction({
    id: 'newapp:validate-name',
    description:
      'Fails if base-apps/<name> already exists (directory or base-apps/<name>.yaml Argo app).',
    schema: {
      input: {
        name: z =>
          z
            .string()
            .regex(/^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/)
            .describe('Proposed application name (kebab-case).'),
      },
    },
    async handler(ctx) {
      const { name } = ctx.input as { name: string };

      const token = process.env.GITHUB_TOKEN;
      if (!token) {
        throw new Error(
          'GITHUB_TOKEN env var is not set. Required for newapp:validate-name.',
        );
      }

      const octokit = new Octokit({ auth: token });
      const dirPath = `base-apps/${name}`;
      const appPath = `base-apps/${name}.yaml`;

      ctx.logger.info(`newapp:validate-name — checking for collisions on '${name}'`);

      if (await pathExists(octokit, dirPath)) {
        throw new Error(
          `Application '${name}' already exists at ${dirPath}. Choose a different name.`,
        );
      }
      if (await pathExists(octokit, appPath)) {
        throw new Error(
          `Application '${name}' already exists at ${appPath}. Choose a different name.`,
        );
      }

      ctx.logger.info(`newapp:validate-name — name '${name}' is available.`);
    },
  });
}
```

- [ ] **Step 3: Register in `packages/backend/src/modules/scaffolder/index.ts`**

Add the import next to the other action imports:
```ts
import { createNewAppValidateNameAction } from './newAppValidateNameAction';
```
Add it to the `scaffolderActions.addActions( ... )` list (e.g. after `createKagentValidateNameAction(),`):
```ts
          createNewAppValidateNameAction(),
```

- [ ] **Step 4: Create `packages/backend/src/modules/scaffolder/newAppValidateNameAction.test.ts`**

Mirror `kagentValidateNameAction.test.ts` (jest, `jest.mock('@octokit/rest')`, manual `createMockActionContext`). Cases:

```ts
import { createNewAppValidateNameAction } from './newAppValidateNameAction';

jest.mock('@octokit/rest');
import { Octokit } from '@octokit/rest';

const MockedOctokit = Octokit as jest.MockedClass<typeof Octokit>;

function buildMockOctokit() {
  return { repos: { getContent: jest.fn() } } as any;
}
function createMockActionContext(opts: { input: Record<string, unknown> }) {
  return {
    input: opts.input,
    output: jest.fn(),
    logger: { info: jest.fn(), warn: jest.fn(), error: jest.fn(), debug: jest.fn(), child: jest.fn().mockReturnThis() },
    workspacePath: '/tmp/t', checkpoint: jest.fn(),
    createTemporaryDirectory: jest.fn().mockResolvedValue('/tmp/t2'),
    getInitiatorCredentials: jest.fn(), task: { id: 't' },
  } as any;
}
function http(status: number) { const e: any = new Error(`HTTP ${status}`); e.status = status; return e; }

describe('newapp:validate-name', () => {
  let orig: string | undefined;
  beforeEach(() => { orig = process.env.GITHUB_TOKEN; process.env.GITHUB_TOKEN = 'x'; MockedOctokit.mockClear(); });
  afterEach(() => { if (orig === undefined) delete process.env.GITHUB_TOKEN; else process.env.GITHUB_TOKEN = orig; });

  it('passes when neither path exists', async () => {
    const oc = buildMockOctokit();
    oc.repos.getContent.mockRejectedValue(http(404));
    MockedOctokit.mockImplementation(() => oc);
    await expect(
      createNewAppValidateNameAction().handler(createMockActionContext({ input: { name: 'brand-new' } })),
    ).resolves.toBeUndefined();
  });

  it('throws when the app directory exists', async () => {
    const oc = buildMockOctokit();
    oc.repos.getContent.mockImplementation(async ({ path }: any) =>
      path === 'base-apps/whoami-test' ? { data: [] } : Promise.reject(http(404)),
    );
    MockedOctokit.mockImplementation(() => oc);
    await expect(
      createNewAppValidateNameAction().handler(createMockActionContext({ input: { name: 'whoami-test' } })),
    ).rejects.toThrow(/already exists at base-apps\/whoami-test/);
  });

  it('throws when only the Argo app file exists', async () => {
    const oc = buildMockOctokit();
    oc.repos.getContent.mockImplementation(async ({ path }: any) =>
      path === 'base-apps/foo.yaml' ? { data: {} } : Promise.reject(http(404)),
    );
    MockedOctokit.mockImplementation(() => oc);
    await expect(
      createNewAppValidateNameAction().handler(createMockActionContext({ input: { name: 'foo' } })),
    ).rejects.toThrow(/already exists at base-apps\/foo\.yaml/);
  });

  it('throws when GITHUB_TOKEN is missing', async () => {
    delete process.env.GITHUB_TOKEN;
    await expect(
      createNewAppValidateNameAction().handler(createMockActionContext({ input: { name: 'x' } })),
    ).rejects.toThrow(/GITHUB_TOKEN/);
  });
});
```

- [ ] **Step 5: Type-check + test**

Run:
```bash
cd /Users/arisela/git/backstage
corepack prepare yarn@4.4.1 --activate >/dev/null 2>&1 || true
yarn tsc
yarn workspace backend test newAppValidateNameAction
```
Expected: tsc clean; the 4 tests pass. If `yarn workspace backend test <name>` filter differs, use `yarn workspace backend backstage-cli package test --watch=false newAppValidateNameAction`.

- [ ] **Step 6: Commit**

```bash
cd /Users/arisela/git/backstage
git add packages/backend/src/modules/scaffolder/newAppValidateNameAction.ts packages/backend/src/modules/scaffolder/newAppValidateNameAction.test.ts packages/backend/src/modules/scaffolder/index.ts
git commit -m "$(printf 'feat(scaffolder): newapp:validate-name collision guard\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b')"
```

---

## Task 2: template.yaml steps/params + skeleton + render test (kubernetes)

**Files:** `templates/new-app/template.yaml`, `templates/new-app/skeleton-ingress/base-apps/${{ values.name }}/nginx-ingress.yaml`, `tests/new-app-template/test_render_new_app.py`.

Work from `/Users/arisela/git/kubernetes` on branch `new-app-template-enhancements` (already checked out).

- [ ] **Step 1: Add two params to `template.yaml`** — under the "Networking & Config" `properties:`, add:

```yaml
        seedOpenaiKey:
          title: Also seed an openai-api-key placeholder in Vault
          type: boolean
          default: false
        whitelist:
          title: Ingress source-IP allowlist (comma-separated CIDRs)
          type: string
          default: "73.7.190.154/32,170.85.56.189/32,170.85.130.202/32,104.28.177.82/32,10.0.0.0/8"
```

- [ ] **Step 2: Add `whitelist` to the `&vals` block** (so the ingress skeleton can use it), right after the `host:` line:

```yaml
          whitelist: ${{ parameters.whitelist }}
```
(`seedOpenaiKey` is NOT added to `&vals` — it's consumed directly by the vault-setup step below.)

- [ ] **Step 3: Add the `validate-name` step as the FIRST step** (before `fetch-core`):

```yaml
    - id: validate-name
      name: Check the name is available
      action: newapp:validate-name
      input:
        name: ${{ parameters.name }}
```

- [ ] **Step 4: Add the `vault-setup` step** (after `fetch-config`, before `publish`):

```yaml
    - id: vault-setup
      name: Provision Vault role + secrets
      if: ${{ parameters.needsSecrets }}
      action: vault:setup
      input:
        vaultRole: ${{ parameters.name }}
        namespace: ${{ parameters.namespace if parameters.namespace else parameters.name }}
        enableAuth: true
        enableKnowledge: ${{ parameters.seedOpenaiKey }}
        serviceAccountNames: default
```

- [ ] **Step 5: Use the whitelist in the ingress skeleton** — in `templates/new-app/skeleton-ingress/base-apps/${{ values.name }}/nginx-ingress.yaml`, change:

```yaml
    nginx.ingress.kubernetes.io/whitelist-source-range: "10.0.0.0/8"
```
to:
```yaml
    nginx.ingress.kubernetes.io/whitelist-source-range: "${{ values.whitelist }}"
```

- [ ] **Step 6: Add `whitelist` to the render-test SAMPLE** — in `tests/new-app-template/test_render_new_app.py`, add to the `SAMPLE` dict:

```python
    "whitelist": "10.0.0.0/8",
```

- [ ] **Step 7: Verify — render + yamllint + full suite**

Run:
```bash
cd /Users/arisela/git/kubernetes
python3 -m pytest tests/new-app-template/ -q
python3 scripts/render-new-app.py --template templates/new-app --values '{"name":"wtest","description":"x","image":"traefik/whoami","containerPort":80,"replicas":1,"namespace":"wtest","system":"default/platform-tooling","owner":"group:default/platform","tags":["a","b"],"exposeIngress":true,"host":"wtest","needsConfig":false,"needsSecrets":false,"whitelist":"1.2.3.4/32,10.0.0.0/8","cpuRequest":"100m","cpuLimit":"500m","memRequest":"128Mi","memLimit":"256Mi"}' --out /tmp/wtest --ingress
grep whitelist-source-range /tmp/wtest/base-apps/wtest/nginx-ingress.yaml
yamllint -c .yamllint.yaml /tmp/wtest/base-apps/wtest/nginx-ingress.yaml && echo "ingress yamllint clean"
python3 -c "import yaml; yaml.safe_load(open('templates/new-app/template.yaml')); print('template.yaml parses')"
yamllint -c .yamllint.yaml templates/new-app/template.yaml && echo "template.yaml yamllint clean"
```
Expected: pytest passes; the rendered ingress shows `whitelist-source-range: "1.2.3.4/32,10.0.0.0/8"`, yamllint clean; template.yaml parses + yamllint clean (block style).

- [ ] **Step 8: Commit**

```bash
cd /Users/arisela/git/kubernetes
git add templates/new-app/template.yaml templates/new-app/skeleton-ingress tests/new-app-template/test_render_new_app.py
git commit -m "$(printf 'feat(new-app): validate-name + vault:setup steps, ingress whitelist field\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b')"
```

---

## Delivery (controller, after Tasks 1–2 pass review)

1. **Build v1.4.12** from `/Users/arisela/git/backstage` branch `new-app-validate-name`:
   `yarn tsc && yarn build:all` → `docker buildx build --platform linux/amd64 -t …/backstage-portal:v1.4.12 --push --provenance=false .` → confirm `linux/amd64`.
2. **backstage PR** (`new-app-validate-name` → main).
3. **kubernetes deploy PR**: bump `deployments.yaml` to v1.4.12.
4. **Order matters:** merge + deploy v1.4.12 (the action exists) **before** merging the kubernetes template PR (whose validate-name step references it). If they merge out of order, the live template's validate-name step 400s until the image is up.

## Verification (post-deploy)

- Scaffold with an existing name (`whoami-test`) → wizard **fails** at "Check the name is available" with a clear message.
- Scaffold a new app with **Secrets on** → after merge/deploy the `ExternalSecret` is **Ready** (the `jwt-secret` placeholder synced; Vault role/policy created by `vault:setup`).
- Scaffold with **Ingress on** → the generated `nginx-ingress.yaml` carries the admin allowlist (or your edited value).

## Self-Review

- **Spec coverage:** collision guard → Task 1 action + Task 2 step; Vault provisioning → Task 2 vault-setup step (always `enableAuth: true`, optional `seedOpenaiKey`); whitelist → Task 2 param + skeleton. Delivery + ordering covered.
- **Placeholders:** complete action/test code, exact template.yaml/skeleton edits, exact verify commands.
- **Consistency:** `newapp:validate-name` id matches between the action, its registration, and the template step; `whitelist` flows param → `&vals` → skeleton → render-test sample; `vault:setup` inputs match `vaultSetupAction.ts`.
