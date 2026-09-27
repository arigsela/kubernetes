# syntax=docker/dockerfile:1
# nomic-embed-text as an OCI image, mounted by base-apps/ollama as an image volume (k8s 1.36)
# with OLLAMA_MODELS pointing at it. Build with scripts/build-model-image.sh nomic-embed-text.
#
# The pull stage runs on the BUILD platform: model files do not depend on the CPU, so an arm64
# laptop builds the amd64 image without emulation. The final stage is data only.
#
# kagent's embedding ModelConfig asks for `nomic-embed-text`, i.e. :latest. The pinned tag's
# manifest is also written as `latest`, so that name resolves without trusting the registry's
# moving `latest`. The model digest is asserted: a different embedding model would silently
# invalidate every vector already stored in kagent's memory.
FROM --platform=$BUILDPLATFORM ollama/ollama:0.20.5 AS pull
ARG MODEL_TAG
ARG MODEL_DIGEST
RUN set -eu; \
    test -n "$MODEL_TAG" && test -n "$MODEL_DIGEST"; \
    ollama serve >/tmp/serve.log 2>&1 & pid=$!; \
    for i in $(seq 1 60); do ollama list >/dev/null 2>&1 && break; sleep 1; done; \
    ollama pull "nomic-embed-text:${MODEL_TAG}"; \
    kill "$pid"; wait "$pid" 2>/dev/null || true; \
    d=/root/.ollama/models/manifests/registry.ollama.ai/library/nomic-embed-text; \
    grep -q "\"${MODEL_DIGEST}\"" "$d/${MODEL_TAG}" \
      || { echo "model digest != ${MODEL_DIGEST}:"; cat "$d/${MODEL_TAG}"; exit 1; }; \
    [ "$MODEL_TAG" = latest ] || cp "$d/${MODEL_TAG}" "$d/latest"

FROM scratch
COPY --from=pull /root/.ollama/models/ /
