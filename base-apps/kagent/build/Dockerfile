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
LABEL org.opencontainers.image.description="Patched kagent UI v0.9.4 — Node 20 LTS for older x86-64 CPUs"

USER 1001
CMD ["/usr/local/bin/init.sh"]
