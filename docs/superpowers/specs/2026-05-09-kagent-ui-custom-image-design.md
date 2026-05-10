# Custom kagent UI Image for Older x86-64 CPUs

**Status:** Design — pending user review
**Date:** 2026-05-09
**Owner:** Ari Sela
**Related issue:** [kagent-dev/kagent#1505](https://github.com/kagent-dev/kagent/issues/1505)

## Problem

The kagent UI pod (v0.8.6) crashes immediately with `SIGILL (Illegal Instruction)` on the old HP server's k3s node. The Next.js process exits on every HTTP request with `WARN exited: nextjs (terminated by SIGILL (core dumped); not expected)`, restarts under supervisord, and crashes again — making the UI unreachable.

**Root cause:** Between v0.7.x and v0.8.x, kagent-dev replaced the UI's runtime from Bun to Node.js 24 (`ui/Dockerfile` switched from `bun install && bun run build` to `npm ci && npm run build`, and the runtime stage now installs `nodejs~24.13.0` from Chainguard's `wolfi-base`). Node.js v23+ binaries are compiled with `-march=x86-64-v2` baseline, which requires CPU instructions (POPCNT, SSE4.2, etc.) that the HP server's CPU lacks. Same bug class as upstream issue #1505 (filed for ARM64 Raspberry Pi).

**Confirmation:**
- Upstream maintainer comments on #1505 attribute the regression to "switching away from bun" and "node using some native modules that are not built for the proper platform."
- Reporter confirms: "Version 0.7.23 works correctly on the same hardware."
- Dockerfile diff `v0.7.23 → v0.8.6` shows: `bun` → `npm`, `BUN_INSTALL` env stack removed, `nodejs~24.13.0` apk pin added.

## Goal

Get the kagent UI running on the existing HP server k3s node, without rolling the entire kagent stack back to v0.7.x. Keep all other kagent components (controller, agents, CRDs) on the official upstream v0.8.6 images.

## Non-goals

- Not tracking upstream kagent UI releases. This is a one-shot patch pinned to v0.8.6.
- Not maintaining a permanent fork of `kagent-dev/kagent`.
- Not setting up CI to auto-rebuild on every kagent tag.
- Not multi-arch (amd64 only).
- Not addressing other unrelated UI bugs (#1614 wizard tool selection, #1767 cluster-domain, #1785 IPv6 RemoteMCPServer).

## Decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | One-shot patch (no CI, no upstream tracking) | Lowest overhead. Rebuild manually if/when v0.8.6 needs a refresh; delete the patch when upstream fixes #1505. |
| 2 | Substitute Node 20 LTS on Debian (`node:20-bookworm-slim`) | Distro-built Node binary, no `x86-64-v2` baseline. Highest compatibility guarantee. Next.js 16.2.2 requires Node ≥20.9, so 20 LTS is the floor of viability. |
| 3 | Publish to AWS ECR | Matches existing repo conventions (CLAUDE.md references ECR for images). |
| 4 | `linux/amd64` only | The HP server is the only target; multi-arch buildx adds emulation cost for no benefit. |
| 5 | Dockerfile + build script vendored in this repo, manual local build | Two files (~80 lines). No CI, no submodules, no separate repo. |

## Architecture

### What lands in this repo

```
base-apps/kagent/
├── build/
│   ├── Dockerfile               # ~50 lines (patched copy of upstream ui/Dockerfile)
│   ├── build.sh                 # ~25 lines (clone + build + push)
│   ├── README.md                # ~10 lines (prereqs + usage)
│   ├── nginx.conf.patch         # OPTIONAL — only added if Risk A materializes
│   └── supervisord.conf.patch   # OPTIONAL — only added if Risk B materializes
└── kagent.yaml                  # +5 lines: ui.image override block
```

### What does NOT change

- `kagent` Helm chart version (still 0.8.6)
- `kagent-crds` Application
- `kagent-secrets` Application
- Controller / agent / kmcp / tools images (stay on `cr.kagent.dev`)
- Vault, External Secrets, model configs, post-deploy patches

### Flow

```
[laptop]                              [ECR]                          [cluster]
   │                                    │                              │
   │ ./build.sh                         │                              │
   │  ├─ git clone --depth 1 \          │                              │
   │  │    --branch v0.8.6 \            │                              │
   │  │    kagent-dev/kagent ───── (read upstream v0.8.6) ─────────────┤
   │  ├─ cp Dockerfile ui/Dockerfile    │                              │
   │  ├─ docker buildx build \          │                              │
   │  │    --platform linux/amd64 \     │                              │
   │  └─ docker push ─────────────────► <acct>.dkr.ecr.<region>.       │
   │                                    amazonaws.com/kagent-ui:       │
   │                                    0.8.6-node20                   │
   │                                    │                              │
   │ git commit base-apps/kagent.yaml   │                              │
   │ git push ────────────────► ArgoCD detects diff ─► UI Deployment ──►│
   │                              syncs values                  pulls  │
   │                                                            from ECR
```

## Component 1: Patched `ui/Dockerfile`

The diff against upstream `ui/Dockerfile@v0.8.6` is mechanical — five concrete changes:

| # | Change | Why |
|---|---|---|
| 1 | `FROM chainguard/wolfi-base:latest` → `FROM node:20-bookworm-slim` (both `deps` and `final` stages) | Node 20 binary built without `x86-64-v2` baseline. Runs on POPCNT-less CPUs. |
| 2 | Remove `ARG TOOLS_NODE_VERSION=24.13.0` and the `nodejs~${TOOLS_NODE_VERSION}` apk pin | Node + npm now ship in the base image. Drop the explicit pin. |
| 3 | `apk add --no-cache curl bash openssl unzip ca-certificates nginx supervisor` → `apt-get update && apt-get install -y --no-install-recommends curl bash openssl unzip ca-certificates nginx supervisor && rm -rf /var/lib/apt/lists/*` | Debian package manager. Build stage adds `python3 build-essential` for any node-gyp native compiles. |
| 4 | `addgroup -g 1001 nginx && adduser -u 1001 -G nginx -s /bin/bash -D nextjs && adduser -u 1002 -G nginx -s /bin/bash -D nginx` → `groupadd -g 1001 nginx && useradd -u 1001 -g nginx -s /bin/bash -M nextjs && useradd -u 1002 -g nginx -s /bin/bash -M nginx` | Debian's `useradd` instead of Alpine's `adduser`. Same UIDs/GIDs preserved (1001/1002) so file ownership and the `USER 1001` directive at the bottom continue to work. |
| 5 | `&& rm -rf /var/lib/apt/lists/*` after each `apt-get install` | Debian cleanliness; not needed with apk. |

### Everything else stays byte-for-byte identical to upstream

- All three stages (`deps`, `builder`, `final`)
- The `npm ci` / `npm run build` commands
- The `COPY --from=builder` lines pulling `.next/standalone`, `.next/static`, `public/`, `package.json`, `next.config.ts`
- The nginx + supervisord + init.sh wiring (`conf/nginx.conf`, `conf/supervisord.conf`, `scripts/init.sh` all copied from upstream as-is)
- `EXPOSE 8080`, `USER 1001`, `CMD ["/usr/local/bin/init.sh"]`

### Full patched Dockerfile

```dockerfile
### STAGE 1: Dependencies
ARG BUILDPLATFORM
FROM --platform=$BUILDPLATFORM node:20-bookworm-slim AS deps

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV DO_NOT_TRACK=1
ENV NEXT_TELEMETRY_DISABLED=1
ENV CYPRESS_INSTALL_BINARY=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       curl bash openssl unzip ca-certificates nginx supervisor python3 build-essential \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/ui

COPY package*.json ./
RUN --mount=type=cache,target=/root/.npm,rw \
    npm ci

### STAGE 2: Build
FROM --platform=$BUILDPLATFORM deps AS builder

COPY . .

RUN --mount=type=cache,target=/root/.npm,rw \
    --mount=type=cache,target=/app/ui/.next/cache,rw \
    export NEXT_TELEMETRY_DEBUG=1 \
    && npm run build \
    && mkdir -p /app/ui/public

### STAGE 3: Runtime
FROM node:20-bookworm-slim AS final

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV NODE_ENV=production

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       curl bash openssl unzip ca-certificates nginx supervisor \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app/ui/public \
        /tmp/nginx/client_temp /tmp/nginx/proxy_temp /tmp/nginx/fastcgi_temp \
        /tmp/nginx/uwsgi_temp /tmp/nginx/scgi_temp \
    && groupadd -g 1001 nginx \
    && useradd -u 1001 -g nginx -s /bin/bash -M nextjs \
    && useradd -u 1002 -g nginx -s /bin/bash -M nginx \
    && chown -R nextjs:nginx /app/ui \
    && chown -R nextjs:nginx /tmp/nginx/

WORKDIR /app
COPY conf/nginx.conf /etc/nginx/nginx.conf
COPY conf/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY scripts/init.sh /usr/local/bin/init.sh

WORKDIR /app/ui
COPY --from=builder /app/ui/next.config.ts ./
COPY --from=builder /app/ui/public ./public
COPY --from=builder /app/ui/package.json ./package.json
COPY --from=builder --chown=nextjs:nginx /app/ui/.next/standalone ./
COPY --from=builder --chown=nextjs:nginx /app/ui/.next/static ./.next/static

RUN chown -R nextjs:nginx /app/ui \
    && chmod -R 755 /app \
    && chmod +x /usr/local/bin/init.sh

EXPOSE 8080

LABEL org.opencontainers.image.source=https://github.com/arigsela/kubernetes
LABEL org.opencontainers.image.description="Patched kagent UI v0.8.6 — Node 20 LTS for older x86-64 CPUs"

USER 1001
CMD ["/usr/local/bin/init.sh"]
```

### Known risks

**Risk A — Debian nginx vs wolfi nginx path differences.** Debian's nginx defaults to `/var/log/nginx` and `/var/lib/nginx`, which conflict with `readOnlyRootFilesystem: true`. Upstream's `conf/nginx.conf` likely already redirects logs to `/tmp/nginx/` (the upstream image runs read-only too), but this needs verification at test time.

Mitigation if it breaks: bind a writable `emptyDir` over `/var/log/nginx` via helm `ui.volumes`, OR ship a `conf/nginx.conf` patch via `build.sh` (see Component 2 below).

**Risk B — `supervisord.conf` binary paths.** If upstream's supervisord config uses absolute paths like `/usr/sbin/nginx` (Debian) vs `/usr/bin/nginx` (wolfi/Alpine), supervisor will fail to start one of the children.

Mitigation: same shape as A — patch `conf/supervisord.conf` post-clone in `build.sh`.

Both risks have a single mitigation pattern: **post-clone patches applied during build**. The build script includes a commented-out hook for this so we can enable it without restructuring.

## Component 2: `build.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

# --- config ---
KAGENT_VERSION="0.8.6"
ECR_REGISTRY="<account>.dkr.ecr.<region>.amazonaws.com"   # to be filled in during impl
ECR_REGION="<region>"                                     # to be filled in during impl
IMAGE_NAME="kagent-ui"
IMAGE_TAG="${KAGENT_VERSION}-node20"
PLATFORM="linux/amd64"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

# --- 1. Clone upstream at the pinned tag ---
git clone --depth 1 --branch "v${KAGENT_VERSION}" \
  https://github.com/kagent-dev/kagent.git "$WORK_DIR/kagent"

# --- 2. Swap in the patched Dockerfile ---
cp "$SCRIPT_DIR/Dockerfile" "$WORK_DIR/kagent/ui/Dockerfile"

# --- (optional) Risk A/B contingency: patch nginx.conf / supervisord.conf if needed ---
# Enable these by un-commenting and dropping the corresponding .patch file in this dir.
# if [[ -f "$SCRIPT_DIR/nginx.conf.patch" ]]; then
#   patch "$WORK_DIR/kagent/ui/conf/nginx.conf" < "$SCRIPT_DIR/nginx.conf.patch"
# fi
# if [[ -f "$SCRIPT_DIR/supervisord.conf.patch" ]]; then
#   patch "$WORK_DIR/kagent/ui/conf/supervisord.conf" < "$SCRIPT_DIR/supervisord.conf.patch"
# fi

# --- 3. ECR login ---
aws ecr get-login-password --region "$ECR_REGION" \
  | docker login --username AWS --password-stdin "$ECR_REGISTRY"

# --- 4. Build & push (single-platform amd64) ---
docker buildx build \
  --platform "$PLATFORM" \
  --tag "$ECR_REGISTRY/$IMAGE_NAME:$IMAGE_TAG" \
  --push \
  "$WORK_DIR/kagent/ui"

echo "Pushed: $ECR_REGISTRY/$IMAGE_NAME:$IMAGE_TAG"
```

### Why this shape

- **`mktemp -d` + cleanup trap**: nothing pollutes the working tree, no submodules, no vendored upstream code in the repo. Re-running is idempotent.
- **`--depth 1 --branch`**: fast, deterministically pinned. Bumping `KAGENT_VERSION` automatically pulls matching upstream sources.
- **`docker buildx build` (not `docker build`)**: lets the user build amd64 from a Mac/M-series laptop (uses qemu under the hood) without changing the script. On a Linux/amd64 host, buildx still works natively.
- **`--push` not `--load`**: amd64-only on a non-amd64 host can't be `docker load`ed locally anyway.

### Prerequisites (one-time)

1. `aws configure` (or `AWS_PROFILE`) with ECR push perms in the target account/region
2. `aws ecr create-repository --repository-name kagent-ui --region <region>` (one-time, or via Terraform)
3. `docker buildx create --use` (one-time, only if not already configured)

### Expected runtime

3–5 minutes on a modern laptop. `npm ci` + `next build` dominates; clone and push are small.

## Component 3: Helm Values Override

Diff against `base-apps/kagent.yaml` (insert under `helm.valuesObject`):

```yaml
        # LLM provider: use Anthropic with existing secret
        providers:
          default: anthropic
          ...

+       # UI image override: custom Node 20 build for older HP server CPUs.
+       # Upstream cr.kagent.dev/kagent-dev/kagent/ui:0.8.6 ships Node 24 with
+       # x86-64-v2 baseline → SIGILL on this hardware.
+       # Track upstream issue: https://github.com/kagent-dev/kagent/issues/1505
+       ui:
+         image:
+           registry: "<account>.dkr.ecr.<region>.amazonaws.com"
+           repository: "kagent-ui"
+           tag: "0.8.6-node20"
+           pullPolicy: IfNotPresent

        # Right-sized agent resources (Task 3.3)
        agents:
          ...
```

### Why this shape

- **No chart fork.** The kagent helm chart already exposes `ui.image.{registry,repository,tag,pullPolicy}` (verified in `helm/kagent/values.yaml@v0.8.6`).
- **Surgically replaces only the UI container.** Controller, agents, kmcp, tools all keep the upstream `cr.kagent.dev` images.
- **`pullPolicy: IfNotPresent`** matches the chart default.

### ECR pull credentials — open question

The `kagent` namespace needs to pull from ECR. Two paths depending on existing cluster wiring (to be verified in implementation, not designed up-front):

1. **Node-level IAM with ECR read perms** — common on AWS-hosted nodes; nothing else needed.
2. **`imagePullSecrets` referencing an ECR pull secret** — common on on-prem or external clusters. Either: add `imagePullSecrets` to the helm values, or copy the existing pull secret into the `kagent` namespace via External Secrets / a ServiceAccount patch.

The HP server is on-prem, so path 2 is the more likely answer. The implementation plan will check what other ECR-hosted apps in this cluster do today and follow the established pattern.

## Verification

Acceptance tests after deployment, in order:

1. **Image pulled successfully.**
   ```bash
   kubectl -n kagent describe pod -l app.kubernetes.io/name=kagent-ui | grep -A2 "Image:"
   # expect: <account>.dkr.ecr.<region>.amazonaws.com/kagent-ui:0.8.6-node20
   ```
   `ImagePullBackOff` here = ECR pull creds issue.

2. **No SIGILL — process stays up.** *(Headline acceptance test.)*
   ```bash
   kubectl -n kagent logs deploy/kagent-ui --tail=50
   kubectl -n kagent get pod -l app.kubernetes.io/name=kagent-ui
   # expect: STATUS=Running, RESTARTS not climbing
   # expect logs: NO "terminated by SIGILL" or "Illegal instruction"
   # expect logs: see Next.js "Ready in <Xms>" startup line
   ```

3. **Risk A — nginx paths.**
   ```bash
   kubectl -n kagent logs deploy/kagent-ui 2>&1 | grep -iE "nginx|permission denied|read-only|EROFS"
   # expect: no permission/EROFS errors
   ```

4. **Risk B — supervisord paths.**
   ```bash
   kubectl -n kagent exec deploy/kagent-ui -- supervisorctl status
   # expect: both nginx and nextjs in RUNNING state
   ```

5. **End-to-end smoke — UI loads in browser.**
   ```bash
   kubectl port-forward -n kagent svc/kagent-ui 8080:8080
   ```
   Hit `http://localhost:8080`, click through Agents list and Model Configs pages. Catches Next.js 16-on-Node 20 runtime issues that wouldn't show up in static checks.

If Risk A or B bites: add the corresponding `nginx.conf.patch` or `supervisord.conf.patch` to `base-apps/kagent/build/`, uncomment the patching block in `build.sh`, rebuild, push, ArgoCD re-syncs.

## Rollback

| Trigger | Action | Recovery time |
|---|---|---|
| Custom image broken, want upstream back | Remove the `ui:` block from `base-apps/kagent.yaml`, push. ArgoCD reverts to upstream image. | ~2 min |
| Whole 0.8.6 line is the problem | Roll the chart back: `targetRevision: 0.7.23` in `kagent.yaml` and `kagent-crds.yaml`. CRD downgrades can be tricky — test in a branch first. | ~5 min |
| Nuclear option | Delete the kagent ArgoCD Application; redeploy with whatever values needed | ~10 min, **destroys kagent Postgres data** unless PVC reclaim policy is Retain |

The first row is the realistic rollback. The custom image is purely additive — pulling it out leaves the rest of the deployment exactly as it is today.

## Cleanup (when upstream fixes #1505)

If kagent-dev fixes [#1505](https://github.com/kagent-dev/kagent/issues/1505) in v0.8.7 / v0.9.x:

1. Remove the `ui:` block from `base-apps/kagent.yaml`
2. Bump `targetRevision` in `kagent.yaml` and `kagent-crds.yaml`
3. (optional) Delete `base-apps/kagent/build/` from the repo

## Out-of-scope reminders

These were called out as separate issues and explicitly *not* addressed by this design:

- [#1614](https://github.com/kagent-dev/kagent/issues/1614) — Quick-builder wizard "Select Tools" page broken on k3s in 0.8.3 (UI bug, separate)
- [#1767](https://github.com/kagent-dev/kagent/issues/1767) — `cluster.local` hardcoded in helm chart (only matters with custom cluster-domain)
- [#1785](https://github.com/kagent-dev/kagent/issues/1785) — Controller hangs registering RemoteMCPServer on IPv4-only k3s with AAAA records
