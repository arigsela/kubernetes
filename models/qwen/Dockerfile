# syntax=docker/dockerfile:1
# Qwen3.5-0.8B weights as an OCI image, mounted by base-apps/qwen as an image volume (k8s 1.36).
# Build with scripts/build-model-image.sh qwen: it downloads the files from a PINNED Hugging
# Face revision, verifies their sha256, and stages them as the build context. Nothing runs in
# this image; it is data. One COPY per file, so a change to one file reuses the other's layer.
# The files land at the image root, i.e. directly under the volume's mountPath.
FROM scratch
COPY Qwen3.5-0.8B-UD-Q4_K_XL.gguf /
COPY mmproj-F16.gguf /
