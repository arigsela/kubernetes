# Design: New Application template — 3 enhancements

**Date:** 2026-07-21
**Status:** design — pending user review
**Topic:** Add a name-collision guard, Vault secret provisioning, and an ingress
IP-whitelist form field to the New Application scaffolder template.

## Goal

Close three gaps found while validating the template against `whoami-test`:
1. Scaffolding a name that already exists in `base-apps/` silently merges into the
   old dir (orphaned resources, missing whitelist). → **collision guard**.
2. The scaffolded `ExternalSecret` is `SecretSyncedError` because nothing provisions
   the Vault role/policy/KV for the app. → **Vault secret provisioning**.
3. The template's ingress hard-codes a minimal `whitelist-source-range: 10.0.0.0/8`,
   so it doesn't carry the admin allowlist. → **whitelist form field**.

## Decisions (settled during brainstorming)

| Decision | Choice |
|---|---|
| Collision check | Fail if **`base-apps/<name>/` OR `base-apps/<name>.yaml`** exists |
| Collision mechanism | New backend action `newapp:validate-name` (mirrors `kagent:agent:validate-name`), first scaffolder step |
| Vault provisioning | `vault:setup` step (if Secrets on); **always seed a jwt-secret placeholder** so the ExternalSecret is green by default; optional openai-api-key toggle |
| Whitelist | Form field `whitelist`, defaulting to the dex admin allowlist, rendered into the ingress |
| Delivery | backstage image (the new action) → v1.4.12 + deploy; kubernetes GitOps (template.yaml + skeleton) — land together |

## Grounding (verified)

- `packages/backend/src/modules/scaffolder/kagentValidateNameAction.ts`
  (`kagent:agent:validate-name`) uses Octokit `repos.getContent` to test path
  existence and `throw`s a clear error, failing the wizard before publish. The new
  action mirrors it for `base-apps/<name>`.
- `vaultSetupAction.ts` (`vault:setup`): creates a Vault policy (`<role>-read`), a
  Kubernetes auth role bound to the namespace's `default` SA, and seeds placeholder
  KV at `k8s-secrets/data/<role>` — `openai-api-key` when `enableKnowledge`,
  `jwt-secret` when `enableAuth` (create-if-not-exists). Reads `VAULT_ADDR` +
  `VAULT_TOKEN` (already in the deployment; the removed application template used it).
- The scaffolded `ExternalSecret` uses `dataFrom: extract` on `k8s-secrets/<name>`,
  so any seeded placeholder syncs.
- dex admin allowlist: `73.7.190.154/32,170.85.56.189/32,170.85.130.202/32,104.28.177.82/32,10.0.0.0/8`.
- The template + skeleton are fetched from `main` at scaffold time (GitOps); only the
  new action needs an image.

## Design

### 1. Collision guard (backstage image + template step)

New action `newapp:validate-name` in `packages/backend/src/modules/scaffolder/newAppValidateNameAction.ts`
(register in `index.ts`). Input `name`. Uses Octokit (GITHUB_TOKEN) to check
`arigsela/kubernetes` for `base-apps/<name>/catalog-info.yaml` (dir marker) and
`base-apps/<name>.yaml` (Argo app); throws
`"Application '<name>' already exists (base-apps/<name>). Choose a different name."`
if either exists. Added as the **first** step in `template.yaml`
(`id: validate-name`, no `if:` guard — always runs).

### 2. Vault secret provisioning (template step, GitOps)

New form fields under "Networking & Config" (or a "Secrets" step):
- `needsSecrets` (existing).
- `seedOpenaiKey` (boolean, default false) → maps to `vault:setup` `enableKnowledge`.

Add a step after the conditional fetches:
```yaml
- id: vault-setup
  name: Provision Vault role + secrets
  if: ${{ parameters.needsSecrets }}
  action: vault:setup
  input:
    vaultRole: ${{ parameters.name }}
    namespace: ${{ parameters.namespace if parameters.namespace else parameters.name }}
    enableAuth: true            # always seed a jwt-secret placeholder -> ExternalSecret green
    enableKnowledge: ${{ parameters.seedOpenaiKey }}
    serviceAccountNames: default
```
Runs at scaffold time → the Vault role/policy/KV exist before the PR merges, so the
deployed `ExternalSecret` authenticates and syncs (placeholder values the author
replaces in Vault). Never puts real secrets through the form.

### 3. Ingress whitelist field (template param + skeleton, GitOps)

Add a form field:
```yaml
whitelist:
  title: Ingress source-IP allowlist (comma-separated CIDRs)
  type: string
  default: "73.7.190.154/32,170.85.56.189/32,170.85.130.202/32,104.28.177.82/32,10.0.0.0/8"
```
Pass through `values` (`whitelist: ${{ parameters.whitelist }}`) and render in
`skeleton-ingress/.../nginx-ingress.yaml`:
```yaml
    nginx.ingress.kubernetes.io/whitelist-source-range: "${{ values.whitelist }}"
```

## Delivery

1. **backstage** (branch off `homepage`): add `newAppValidateNameAction.ts` + register
   in `index.ts`; unit test mirroring `kagentValidateNameAction.test.ts`. Build
   **v1.4.12** (linux/amd64), backstage PR, kubernetes deploy bump.
2. **kubernetes** (this branch): `template.yaml` (validate-name step, vault-setup step,
   `seedOpenaiKey` + `whitelist` params + the `values` mappings) + `skeleton-ingress`
   whitelist. The render-test harness sample gains `whitelist` so rendering stays green.
3. Land together — the `validate-name` step errors until the action is deployed.

## Verification

- Render harness: `whitelist` renders into the ingress; full suite green.
- Backstage action unit test: throws when a `base-apps/<name>` path exists, passes otherwise.
- Post-deploy: scaffolding an existing name (e.g. `whoami-test`) fails in the wizard;
  a new app with Secrets on → the ExternalSecret syncs green; the ingress carries the
  admin allowlist.

## Risks / out of scope

- `vault:setup` seeds only jwt/openai placeholders; apps needing other keys add them
  in Vault post-scaffold (the `dataFrom: extract` ExternalSecret picks them up).
- Collision guard checks `arigsela/kubernetes` `main` via the GitHub API at scaffold
  time (a just-merged app appears immediately; an open unmerged PR's dir does not).
- No change to the `configData`/tags handling (already fixed).
