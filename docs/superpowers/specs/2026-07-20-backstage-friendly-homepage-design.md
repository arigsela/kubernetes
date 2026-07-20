# Design: Friendly Backstage home page

**Date:** 2026-07-20
**Status:** design — pending user review
**Topic:** Replace Backstage's default `/` → `/catalog` redirect with a composed
home page (a "launchpad") built on `@backstage/plugin-home`.

## Goal

Turn the Backstage landing page from a bare catalog redirect into a friendly
launchpad: a search bar, quick-launch tiles to the tools used daily, and
personal context (starred + recently visited entities). Frontend-only change in
`arigsela/backstage`, delivered as a new image and a deploy tag bump.

## Decisions (settled during brainstorming)

| Decision | Choice |
|---|---|
| Foundation | **`@backstage/plugin-home`** — compose a custom `HomePage` on a card grid |
| Cards | Search bar, Toolkit, Starred + Recently-visited, Welcome/featured-docs |
| Toolkit tiles | **Core ops** (ArgoCD, Grafana, coroot, Vault) + **Internal** (Docs, API Explorer, Catalog Graph) |
| Header | **Static title** "Homelab Platform" (no identity dependency) |
| Delivery | backstage-repo PR (frontend) + image **v1.4.8** (amd64 buildx) + kubernetes deploy PR |

## Grounding (verified)

- `@backstage/plugin-home` is **not** installed (`packages/app/package.json`).
- `packages/app/src/App.tsx` currently has `<Route path="/" element={<Navigate to="catalog" />} />`.
- `@backstage/plugin-search` (source of `HomePageSearchBar`) and `@material-ui/icons`
  are already installed.
- No existing Home component.

## Design

### `HomePage` component — `packages/app/src/components/home/HomePage.tsx`

`Page themeId="home"` → static `Header title="Homelab Platform"` → `Content` with a
responsive grid:

- **Row 1:** full-width `HomePageSearchBar` (wrapped in a `SearchContextProvider`).
- **Row 2:** `HomePageToolkit` (left) + Welcome `InfoCard` (right).
- **Row 3:** `HomePageStarredEntities` (left) + `HomePageRecentlyVisited` (right).

Responsive: each row is 12-col on small screens, split 6/6 on `md+`. Cards must
scroll internally, never the page body horizontally.

### Toolkit tiles

External (open in the same or new tab):

| Label | URL |
|---|---|
| ArgoCD | `https://argocd.arigsela.com` |
| Grafana | `https://grafana.arigsela.com` |
| coroot | `https://coroot.arigsela.com` |
| Vault | `https://vault.arigsela.com` |

Internal (Backstage routes):

| Label | URL |
|---|---|
| Docs | `/docs` |
| API Explorer | `/api-docs` |
| Catalog Graph | `/catalog-graph` |

Icons from `@material-ui/icons` (already installed) — e.g. `Sync` (ArgoCD),
`BarChart` (Grafana), `Timeline` (coroot), `Lock` (Vault), `MenuBook` (Docs),
`Extension` (APIs), `AccountTree` (Graph). Exact mapping decided at build time;
no brand icons required.

### Welcome card

A small `InfoCard title="Welcome"` with a one-line intro ("Your homelab developer
portal — search, browse the catalog, read the docs.") and quick links to
`/catalog`, `/docs`, and `/create`.

### Wiring (`packages/app`)

- **`App.tsx`** — replace the `/` redirect with `<Route path="/" element={<HomePage />} />`;
  mount `<VisitListener />` (required by the Recently-visited card).
- **`apis.ts`** — register the visits API (`VisitsWebStorageApi.create(...)`) so
  Recently-visited has storage (browser-local; fine for single-user homelab).
- **Sidebar** (`components/Root/Root.tsx`) — add a "Home" `SidebarItem` (icon
  `HomeIcon`, `to="/"`) at the top, above Catalog.
- **`package.json`** — add `@backstage/plugin-home`.

## Delivery

1. **`arigsela/backstage`** PR (branch `homepage`): the above frontend changes.
2. **Image** `backstage-portal:v1.4.8` — built for **linux/amd64** with
   `docker buildx --platform linux/amd64 --provenance=false` (the established
   recipe; the host `yarn tsc && yarn build:backend` produces the bundle).
3. **`arigsela/kubernetes`** deploy PR: bump `base-apps/backstage/deployments.yaml`
   v1.4.7 → v1.4.8.

## Verification

- `yarn tsc` + `yarn lint` clean; local `yarn start` renders the home page.
- Post-deploy: `/` shows the launchpad; search works; toolkit tiles open the
  right targets; starred/recently-visited populate as you use the portal.

## Risks / out of scope

- Frontend-only: worst case the home page errors while the rest of Backstage is
  unaffected (isolated route + component).
- Recently-visited storage is browser-local (no server persistence) — acceptable
  for a single-user homelab; a `VisitsStorageApi` backend is out of scope.
- No announcements, org chart, greeting, or delivery/automation tiles this pass
  (deferred; easy to add later).
