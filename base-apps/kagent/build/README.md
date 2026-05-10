# kagent UI custom image build

Builds a Node-20-on-Debian replacement for `cr.kagent.dev/kagent-dev/kagent/ui:0.8.6` so the Next.js process doesn't `SIGILL` on the HP server's older x86-64 CPU.

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
1. `git clone --depth 1 --branch v0.8.6 https://github.com/kagent-dev/kagent.git` into a tmp dir
2. Copy our patched `Dockerfile` into `ui/`
3. `docker buildx build --platform linux/amd64 --push` to `852893458518.dkr.ecr.us-east-2.amazonaws.com/kagent-ui:0.8.6-node20`
4. Clean up the tmp dir on exit

Expected runtime: 3–5 min.

## Bumping `KAGENT_VERSION`

If you need to rebuild this patch against a newer kagent release (because upstream issue #1505 still isn't fixed):

1. Edit `KAGENT_VERSION` at the top of `build.sh`
2. Update `IMAGE_TAG` if you want a different suffix
3. Re-run `./build.sh`
4. Verify upstream's `ui/Dockerfile` hasn't changed in ways our patch is incompatible with — if it has, re-merge the upstream changes into our `Dockerfile` here

## Risk A/B contingency: nginx / supervisord path patches

If the `kagent-ui` pod fails to start because Debian's nginx or supervisord paths differ from the wolfi originals, drop a patch file in this directory:

- `nginx.conf.patch` — patches `ui/conf/nginx.conf` (likely needed if logs hit `/var/log/nginx` instead of `/tmp/nginx/`)
- `supervisord.conf.patch` — patches `ui/conf/supervisord.conf` (likely needed if it references absolute binary paths)

Then uncomment the matching block in `build.sh` and re-run.

## Cleanup

When upstream fixes [#1505](https://github.com/kagent-dev/kagent/issues/1505):

1. Remove the `ui:` block from `base-apps/kagent.yaml`
2. Bump `targetRevision` in `base-apps/kagent.yaml` and `base-apps/kagent-crds.yaml` to the fixed version
3. Delete this directory
