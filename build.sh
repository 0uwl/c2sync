#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

VERSION=$(grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')
DIST_NAME="c2sync-${VERSION}"
BUILD_DIR=$(mktemp -d)
STAGE="${BUILD_DIR}/${DIST_NAME}"

cleanup() { rm -rf "${BUILD_DIR}" "${SCRIPT_DIR}/build"; }
trap cleanup EXIT

echo "Running tests..."
python3 -m pytest c2sync/tests/ -q || { echo "Tests failed — aborting build."; exit 1; }

echo "Building c2sync ${VERSION}..."

mkdir -p "${STAGE}/wheels"

# Build a wheel for c2sync itself
python3 -m pip wheel . --no-deps -w "${STAGE}/wheels" -q

# Read runtime dependencies from pyproject.toml
DEPS=$(python3 - <<'PYEOF'
import re, sys

try:
    import tomllib
    with open('pyproject.toml', 'rb') as f:
        data = tomllib.load(f)
    deps = data.get('project', {}).get('dependencies', [])
except ImportError:
    # Fallback for Python < 3.11
    content = open('pyproject.toml').read()
    m = re.search(r'(?s)\[project\].*?^dependencies\s*=\s*\[(.*?)\]', content, re.MULTILINE)
    if not m:
        sys.exit('Could not find [project].dependencies in pyproject.toml')
    deps = re.findall(r'"([^"]+)"', m.group(1))

if not deps:
    sys.exit('No dependencies found in [project].dependencies')

# Strip version specifiers (e.g. "click>=8.0" -> "click")
names = [re.split(r'[>=<!;\s\[]', d)[0].strip() for d in deps]
print(' '.join(n for n in names if n))
PYEOF
)

# Download all runtime dependencies (including transitive deps) as wheels
# Target Python 3.10 so wheels are compatible with the deployment machine.
# shellcheck disable=SC2086
python3 -m pip download $DEPS \
    -d "${STAGE}/wheels" \
    --python-version 3.10 \
    --only-binary :all: \
    -q

# Bundle the install/uninstall scripts
cp "${SCRIPT_DIR}/install.sh" "${STAGE}/install.sh"
cp "${SCRIPT_DIR}/uninstall.sh" "${STAGE}/uninstall.sh"
chmod +x "${STAGE}/install.sh" "${STAGE}/uninstall.sh"

mkdir -p "${SCRIPT_DIR}/dist"
OUTPUT="${SCRIPT_DIR}/dist/${DIST_NAME}.tar.gz"
tar -czf "${OUTPUT}" -C "${BUILD_DIR}" "${DIST_NAME}"

echo "Created dist/${DIST_NAME}.tar.gz"

echo "Running Docker install test (Python 3.10.12)..."
docker build -f "${SCRIPT_DIR}/Dockerfile.test" -t c2sync-test:latest "${SCRIPT_DIR}" \
    || { echo "Docker install test failed."; exit 1; }
echo "Docker install test passed."

echo "Install with: tar -xzf dist/${DIST_NAME}.tar.gz && cd ${DIST_NAME} && ./install.sh"
