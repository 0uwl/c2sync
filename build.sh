#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

VERSION=$(grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')
DIST_NAME="c2sync-${VERSION}"
BUILD_DIR=$(mktemp -d)
STAGE="${BUILD_DIR}/${DIST_NAME}"

cleanup() { rm -rf "${BUILD_DIR}"; }
trap cleanup EXIT

echo "Building c2sync ${VERSION}..."

mkdir -p "${STAGE}/wheels"

# Build a wheel for c2sync itself
python3 -m pip wheel . --no-deps -w "${STAGE}/wheels" -q

# Download all runtime dependencies (including transitive deps) as wheels
python3 -m pip download \
    pyserial watchdog pytest click rich gitpython \
    -d "${STAGE}/wheels" -q

# Bundle the install script
cp "${SCRIPT_DIR}/install.sh" "${STAGE}/install.sh"
chmod +x "${STAGE}/install.sh"

mkdir -p "${SCRIPT_DIR}/dist"
OUTPUT="${SCRIPT_DIR}/dist/${DIST_NAME}.tar.gz"
tar -czf "${OUTPUT}" -C "${BUILD_DIR}" "${DIST_NAME}"

echo "Created dist/${DIST_NAME}.tar.gz"
echo "Install with: tar -xzf dist/${DIST_NAME}.tar.gz && cd ${DIST_NAME} && ./install.sh"
