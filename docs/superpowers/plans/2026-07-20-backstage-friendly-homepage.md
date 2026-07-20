# Friendly Backstage Home Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Backstage's `/` → `/catalog` redirect with a composed home-page launchpad (search, toolkit, starred, recently-visited, welcome) on `@backstage/plugin-home`.

**Architecture:** Frontend-only change in `arigsela/backstage` `packages/app`: a new `HomePage` component wired to `/`, the visits API registered for recently-visited, and the sidebar updated. Then a new image (`v1.4.8`, amd64) + a kubernetes deploy tag bump.

**Tech Stack:** Backstage frontend (React 18, Material-UI v4, `@backstage/*` ~1.4x-era packages), `@backstage/plugin-home`, `@backstage/plugin-search` (+ `-react`).

## Global Constraints

- Match the existing `packages/app` patterns (`App.tsx`, `apis.ts`, `components/Root/Root.tsx`). Follow their import/comment style.
- **Header is a static title** `"Homelab Platform"` — no identity/greeting dependency.
- **Toolkit tiles (exact):** external `https://argocd.arigsela.com` (ArgoCD), `https://grafana.arigsela.com` (Grafana), `https://coroot.arigsela.com` (coroot), `https://vault.arigsela.com` (Vault); internal `/docs` (Docs), `/api-docs` (API Explorer), `/catalog-graph` (Catalog Graph).
- **Verify version-specific API shapes against `node_modules/@backstage/plugin-home`** after adding the dep — the exact export names (`HomePageRecentlyVisited`, `visitsApiRef`, `VisitsWebStorageApi`, `VisitListener`) and `VisitsWebStorageApi.create(...)` signature must match the installed version; the code below is the expected shape, adjust if the installed version differs.
- Local verification is `yarn tsc` + `yarn lint` + `yarn build:all` (no unit-test harness for this UI). Run yarn via `corepack prepare yarn@4.4.1 --activate` first if `yarn` isn't on PATH.
- Image built for **linux/amd64** with `docker buildx --platform linux/amd64 --provenance=false` (native pip/apt steps segfault under emulation only for the mkdocs layer, which is already cached; this change doesn't touch it).
- Commit trailers on every commit:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b`.

## File Structure (`arigsela/backstage`, working dir `/Users/arisela/git/backstage`, branch `homepage`)

- Create: `packages/app/src/components/home/HomePage.tsx` — the composed home page.
- Modify: `packages/app/src/apis.ts` — register the visits API.
- Modify: `packages/app/src/App.tsx` — `/` route → `HomePage`; mount `<VisitListener/>`; drop the now-unused `Navigate` import.
- Modify: `packages/app/src/components/Root/Root.tsx` — repoint "Home" to `/`, add a "Catalog" item.
- Modify: `packages/app/package.json` — add `@backstage/plugin-home`.

---

## Task 1: Add plugin-home + HomePage component

**Files:**
- Modify: `packages/app/package.json`
- Modify: `packages/app/src/apis.ts`
- Create: `packages/app/src/components/home/HomePage.tsx`

**Interfaces:**
- Produces: `HomePage` (default-styled React component, no props) consumed by App.tsx in Task 2; a registered `visitsApiRef` factory consumed by `HomePageRecentlyVisited`.

- [ ] **Step 1: Create the branch + install the plugin**

```bash
cd /Users/arisela/git/backstage
git checkout main && git pull --ff-only
git checkout -b homepage
corepack prepare yarn@4.4.1 --activate >/dev/null 2>&1 || true
yarn --cwd packages/app add @backstage/plugin-home
```
Expected: `package.json` gains `@backstage/plugin-home` at a version compatible with the installed `@backstage/*` packages; `yarn.lock` updates.

- [ ] **Step 2: Confirm the exported names against the installed version**

Run:
```bash
cd /Users/arisela/git/backstage
node -e "const h=require('@backstage/plugin-home'); console.log(['HomePageToolkit','HomePageStarredEntities','HomePageRecentlyVisited','VisitListener','visitsApiRef','VisitsWebStorageApi'].map(k=>k+':'+(k in h)).join('\n'))"
```
Expected: every name prints `:true`. If any is `false`, find the correct export (`grep -rE 'HomePageRecentlyVisited|VisitsWebStorageApi' node_modules/@backstage/plugin-home/dist/index.d.ts`) and use it below.

- [ ] **Step 3: Register the visits API in `apis.ts`**

Add the imports (extend the existing `@backstage/core-plugin-api` import; add a plugin-home import):

```ts
import {
  AnyApiFactory,
  configApiRef,
  createApiFactory,
  identityApiRef,
  errorApiRef,
} from '@backstage/core-plugin-api';
import { visitsApiRef, VisitsWebStorageApi } from '@backstage/plugin-home';
```

Add this factory to the `apis` array (after `ScmAuth.createDefaultApiFactory()`):

```ts
  /**
   * VISITS API (home plugin):
   * Backs the "Recently visited" home-page card. VisitsWebStorageApi stores the
   * visit history in the browser (per-user via identityApi) — no server persistence,
   * which is fine for a single-user homelab.
   */
  createApiFactory({
    api: visitsApiRef,
    deps: { identityApi: identityApiRef, errorApi: errorApiRef },
    factory: ({ identityApi, errorApi }) =>
      VisitsWebStorageApi.create({ identityApi, errorApi }),
  }),
```

- [ ] **Step 4: Create `packages/app/src/components/home/HomePage.tsx`**

```tsx
import {
  Content,
  Header,
  InfoCard,
  Link,
  Page,
} from '@backstage/core-components';
import {
  HomePageToolkit,
  HomePageStarredEntities,
  HomePageRecentlyVisited,
} from '@backstage/plugin-home';
import { HomePageSearchBar } from '@backstage/plugin-search';
import { SearchContextProvider } from '@backstage/plugin-search-react';
import { Grid, makeStyles } from '@material-ui/core';
import SyncIcon from '@material-ui/icons/Sync';
import BarChartIcon from '@material-ui/icons/BarChart';
import TimelineIcon from '@material-ui/icons/Timeline';
import LockIcon from '@material-ui/icons/Lock';
import MenuBookIcon from '@material-ui/icons/MenuBook';
import ExtensionIcon from '@material-ui/icons/Extension';
import AccountTreeIcon from '@material-ui/icons/AccountTree';

const useStyles = makeStyles(theme => ({
  searchBar: {
    display: 'flex',
    maxWidth: '60vw',
    boxShadow: theme.shadows[1],
    borderRadius: '50px',
    margin: 'auto',
  },
}));

// Quick-launch tiles: the tools opened daily + in-portal destinations.
const tools = [
  { url: 'https://argocd.arigsela.com', label: 'ArgoCD', icon: <SyncIcon /> },
  { url: 'https://grafana.arigsela.com', label: 'Grafana', icon: <BarChartIcon /> },
  { url: 'https://coroot.arigsela.com', label: 'coroot', icon: <TimelineIcon /> },
  { url: 'https://vault.arigsela.com', label: 'Vault', icon: <LockIcon /> },
  { url: '/docs', label: 'Docs', icon: <MenuBookIcon /> },
  { url: '/api-docs', label: 'API Explorer', icon: <ExtensionIcon /> },
  { url: '/catalog-graph', label: 'Catalog Graph', icon: <AccountTreeIcon /> },
];

export const HomePage = () => {
  const classes = useStyles();
  return (
    <Page themeId="home">
      <Header title="Homelab Platform" />
      <Content>
        <Grid container spacing={3}>
          <Grid item xs={12}>
            <SearchContextProvider>
              <HomePageSearchBar
                classes={{ root: classes.searchBar }}
                placeholder="Search the catalog, docs, and APIs…"
              />
            </SearchContextProvider>
          </Grid>
          <Grid item xs={12} md={6}>
            <HomePageToolkit title="Tools" tools={tools} />
          </Grid>
          <Grid item xs={12} md={6}>
            <InfoCard title="Welcome">
              Your homelab developer portal — search, browse the{' '}
              <Link to="/catalog">catalog</Link>, read the{' '}
              <Link to="/docs">docs</Link>, or{' '}
              <Link to="/create">create something new</Link>.
            </InfoCard>
          </Grid>
          <Grid item xs={12} md={6}>
            <HomePageStarredEntities />
          </Grid>
          <Grid item xs={12} md={6}>
            <HomePageRecentlyVisited />
          </Grid>
        </Grid>
      </Content>
    </Page>
  );
};
```

- [ ] **Step 5: Type-check**

Run: `cd /Users/arisela/git/backstage && yarn tsc`
Expected: no errors. If `HomePageToolkit` rejects the `tools` prop shape or `HomePageSearchBar` rejects `classes`/`placeholder` in the installed version, adjust to the version's prop names (check `node_modules/@backstage/plugin-home/dist/index.d.ts` / `@backstage/plugin-search`).

- [ ] **Step 6: Commit**

```bash
cd /Users/arisela/git/backstage
git add packages/app/package.json packages/app/src/apis.ts packages/app/src/components/home/HomePage.tsx yarn.lock
git commit -m "$(printf 'feat(home): add plugin-home + HomePage launchpad component\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b')"
```

---

## Task 2: Wire the route + sidebar

**Files:**
- Modify: `packages/app/src/App.tsx`
- Modify: `packages/app/src/components/Root/Root.tsx`

**Interfaces:**
- Consumes: `HomePage` from Task 1; `VisitListener` from `@backstage/plugin-home`.

- [ ] **Step 1: Point `/` at HomePage and mount VisitListener (`App.tsx`)**

Change the router-dom import (drop `Navigate`, now unused):
```ts
import { Route } from 'react-router-dom';
```
Add imports (near the other local-module / plugin imports):
```ts
import { VisitListener } from '@backstage/plugin-home';
import { HomePage } from './components/home/HomePage';
```
Replace the default route:
```tsx
    {/* Default route: redirect to the catalog as the home page */}
    <Route path="/" element={<Navigate to="catalog" />} />
```
with:
```tsx
    {/* Home: composed launchpad (search, tools, starred, recently visited) */}
    <Route path="/" element={<HomePage />} />
```
Mount `VisitListener` inside `AppRouter` so "Recently visited" records navigation:
```tsx
    <AppRouter>
      <VisitListener />
      <Root>{routes}</Root>
    </AppRouter>
```

- [ ] **Step 2: Update the sidebar (`components/Root/Root.tsx`)**

The existing item `<SidebarItem icon={HomeIcon} to="catalog" text="Home" />` points "Home" at the catalog. Repoint it to the new home page and add a dedicated Catalog item. Add an icon import near the other `@material-ui/icons` imports:
```ts
import CategoryIcon from '@material-ui/icons/Category';
```
Replace:
```tsx
        <SidebarItem icon={HomeIcon} to="catalog" text="Home" />
```
with:
```tsx
        <SidebarItem icon={HomeIcon} to="/" text="Home" />
        <SidebarItem icon={CategoryIcon} to="catalog" text="Catalog" />
```

- [ ] **Step 3: Type-check, lint, build**

Run:
```bash
cd /Users/arisela/git/backstage
yarn tsc
yarn lint
yarn build:all
```
Expected: all clean. `yarn lint` must not report an unused `Navigate` (removed in Step 1). If `build:all` is too heavy, `yarn workspace app build` compiles the frontend specifically.

- [ ] **Step 4: Commit**

```bash
cd /Users/arisela/git/backstage
git add packages/app/src/App.tsx packages/app/src/components/Root/Root.tsx
git commit -m "$(printf 'feat(home): route / to HomePage + sidebar Home/Catalog split\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01DKxovp1bSYJgVSJ4nU5E1b')"
```

---

## Delivery (controller, after Tasks 1–2 pass review)

1. **Build the image** from `/Users/arisela/git/backstage` on branch `homepage`:
   - `yarn tsc && yarn build:backend` (produces `packages/backend/dist/{skeleton,bundle}.tar.gz`).
   - `aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin 852893458518.dkr.ecr.us-east-2.amazonaws.com`
   - `docker buildx build --platform linux/amd64 -f packages/backend/Dockerfile -t 852893458518.dkr.ecr.us-east-2.amazonaws.com/backstage-portal:v1.4.8 --push --provenance=false .`
   - Verify: `docker buildx imagetools inspect --format '{{.Image.OS}}/{{.Image.Architecture}}' …:v1.4.8` → `linux/amd64`.
2. **backstage PR** (`homepage` → main).
3. **kubernetes deploy PR**: bump `base-apps/backstage/deployments.yaml` v1.4.7 → v1.4.8 on a branch; PR. Merging rolls the pod (imagePullPolicy: Always).

## Verification (post-deploy, browser)

- `/` (and the sidebar "Home") shows the "Homelab Platform" launchpad.
- Search bar returns catalog/docs/API hits.
- Toolkit tiles open ArgoCD/Grafana/coroot/Vault and the internal Docs/APIs/Graph pages.
- "Starred" and "Recently visited" populate as you star entities / navigate.
- "Catalog" sidebar item still reaches the catalog.

## Self-Review

- **Spec coverage:** search bar, toolkit (4 external + 3 internal), starred, recently-visited, welcome card, static header → Task 1's `HomePage`. `/` route + VisitListener → Task 2. Sidebar Home/Catalog → Task 2. Visits API → Task 1. Image + deploy → Delivery. All spec items covered.
- **Placeholders:** none — complete code for `HomePage.tsx`, exact diffs for `apis.ts`/`App.tsx`/`Root.tsx`, exact commands with expected output. Version-shape verification is an explicit step, not a TODO.
- **Consistency:** `HomePage` export name, `visitsApiRef` registration, and `VisitListener` usage line up across Tasks 1–2; toolkit URLs match the spec verbatim.
