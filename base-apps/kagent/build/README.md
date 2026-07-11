# kagent UI custom image build

Builds a Node-20-on-Debian replacement for `cr.kagent.dev/kagent-dev/kagent/ui:0.9.11` so the Next.js process doesn't `SIGILL` on the HP server's older x86-64 CPU.

**This is a permanent fork.** Upstream closed [#1505](https://github.com/kagent-dev/kagent/issues/1505) as `not_planned` on 2026-05-23 and still ships Node 24 as of v0.9.11, so this image will not go away. **Every kagent chart bump requires a matching rebuild here** — see "Bumping `KAGENT_VERSION`" below.

**Background:** [Design spec](../../../docs/superpowers/specs/2026-05-09-kagent-ui-custom-image-design.md) · [Upstream issue #1505](https://github.com/kagent-dev/kagent/issues/1505)

## Prerequisites (one-time)

1. AWS CLI configured with ECR push permissions in account `852893458518` / region `us-east-2`:
   ```bash
   aws configure   # or: export AWS_PROFILE=<your-profile>
   ```
2. Docker buildx builder (only if not already configured):
   ```bash
   docker buildx create --use
   ```
3. ECR repository `kagent-ui` (created by Phase 1 of the implementation plan; nothing to do here unless it was deleted).

## Usage

From the repo root:

```bash
./base-apps/kagent/build/build.sh
```

This will:
1. `git clone --depth 1 --branch v${KAGENT_VERSION} https://github.com/kagent-dev/kagent.git` into a tmp dir
2. Copy our patched `Dockerfile` into `ui/`
3. `docker buildx build --platform linux/amd64 --push` to `852893458518.dkr.ecr.us-east-2.amazonaws.com/kagent-ui:0.9.11-node20`
4. Clean up the tmp dir on exit

Expected runtime: 3–5 min.

## Bumping `KAGENT_VERSION`

Required on every kagent chart bump. The image tag and the chart `targetRevision` must always match.

1. Edit `KAGENT_VERSION` at the top of `build.sh`
2. Diff upstream's `ui/Dockerfile` between the old and new tags and re-merge anything that affects our patch:
   ```bash
   gh api "repos/kagent-dev/kagent/contents/ui/Dockerfile?ref=v<old>" -q .content | base64 -d > /tmp/old
   gh api "repos/kagent-dev/kagent/contents/ui/Dockerfile?ref=v<new>" -q .content | base64 -d > /tmp/new
   diff -u /tmp/old /tmp/new
   ```
   Our Dockerfile hardcodes upstream's layout: `ui/package*.json`, `npm run build`, `.next/standalone`, `.next/static`, `next.config.ts`, `scripts/init.sh`, uid 1001 `nextjs` / 1002 `nginx`, `EXPOSE 8080`. A restructure upstream breaks the build.
3. Update the `LABEL org.opencontainers.image.description` version in our `Dockerfile`
4. Re-run `./build.sh` — **before** the chart bump merges to main, or `kagent-ui` will `ImagePullBackOff`
5. Update `ui.image.tag` in `base-apps/kagent.yaml` to match

Note: nginx/supervisord config is supplied by the chart (ConfigMap), not baked into this image — chart-side settings like `ui.nginx.proxyReadTimeout` apply to our custom image too.

## Risk A/B contingency: nginx / supervisord path patches

If the `kagent-ui` pod fails to start because Debian's nginx or supervisord paths differ from the wolfi originals, drop a patch file in this directory:

- `nginx.conf.patch` — patches `ui/conf/nginx.conf` (likely needed if logs hit `/var/log/nginx` instead of `/tmp/nginx/`)
- `supervisord.conf.patch` — patches `ui/conf/supervisord.conf` (likely needed if it references absolute binary paths)

Then uncomment the matching block in `build.sh` and re-run.

## Retiring this fork

Upstream closed #1505 as `not_planned`, so there is no fix coming. This directory goes away only if one of these becomes true:

- Upstream moves the UI image off Node 24 (check `ARG TOOLS_NODE_VERSION` in their `ui/Dockerfile` on each bump), **or**
- The cluster's nodes are replaced with CPUs that support the x86-64-v2 baseline.

If either happens: remove the `ui:` block from `base-apps/kagent.yaml`, confirm the stock image starts, then delete this directory.
