#!/usr/bin/env bash
set -euo pipefail

# Build and load sandbox base image into Rancher Desktop's containerd
# Rancher Desktop uses containerd (nerdctl) - we can use docker CLI since it's aliased

IMAGE_NAME="sandbox-base"
IMAGE_TAG="local"
FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"

echo "Building ${FULL_IMAGE} for $(uname -m)..."

# Build with docker (aliased to Rancher Desktop's containerd)
docker build \
    --platform linux/arm64 \
    -t "${FULL_IMAGE}" \
    -f images/sandbox-base/Dockerfile \
    images/sandbox-base/

echo "Image built: ${FULL_IMAGE}"

# Verify image exists in containerd
if docker image inspect "${FULL_IMAGE}" >/dev/null 2>&1; then
    echo "✓ Image available in Rancher Desktop"
else
    echo "✗ Image not found in containerd"
    exit 1
fi

# Also tag for k8s reference
docker tag "${FULL_IMAGE}" "localhost:5000/${FULL_IMAGE}" 2>/dev/null || true

echo "Done. Use image: ${FULL_IMAGE} in Sandbox specs"