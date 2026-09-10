#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Python minors the shipped wheelhouse supports. Most of c2sync's dependency
# tree is pure-python or abi3, but cffi and pyyaml publish version-specific ABI
# wheels, so a single-version wheelhouse only installs on the exact minor it was
# built for. Downloading each minor into one directory lets the target's own pip
# pick the matching tags; the extra cost is ~2MB total.
PY_VERSIONS=(3.11 3.12 3.13)

PYTHON="${PYTHON:-python3}"
RUN_TESTS=1
TEST_INSTALL=0

usage() {
    cat <<'EOF'
Usage: ./build.sh [options]

Builds dist/c2sync-<version>.tar.gz: a self-contained archive holding a wheel
for c2sync, wheels for every runtime dependency, and an install script.

Options:
  --skip-tests     Do not run the test suite before building.
  --test-install   After building, install the archive in a clean Docker
                   container for each supported Python version and smoke-test
                   the result. Requires docker.
  -h, --help       Show this help.

Environment:
  PYTHON           Interpreter used to build and download wheels; must have pip
                   (default: python3).
  PYTEST           Command used to run the test suite (default: auto-detected).
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-tests)   RUN_TESTS=0 ;;
        --test-install) TEST_INSTALL=1 ;;
        -h|--help)      usage; exit 0 ;;
        *)              echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

die() { echo "Error: $*" >&2; exit 1; }

command -v "${PYTHON}" &>/dev/null || die "${PYTHON} not found."
"${PYTHON}" -m pip --version &>/dev/null \
    || die "${PYTHON} has no pip. Set PYTHON to an interpreter that does, e.g. PYTHON=/usr/bin/python3 ./build.sh"

# The interpreter that vendors wheels needs pip; the one that runs the tests
# needs pytest and the runtime dependencies. Those are often not the same
# interpreter (a uv-created .venv, for instance, has no working pip), so they
# are resolved independently rather than assuming one can do both.
resolve_pytest() {
    if [ -n "${PYTEST:-}" ]; then
        echo "${PYTEST}"
    elif "${PYTHON}" -c "import pytest" &>/dev/null; then
        echo "${PYTHON} -m pytest"
    elif [ -x "${SCRIPT_DIR}/.venv/bin/python" ] \
         && "${SCRIPT_DIR}/.venv/bin/python" -c "import pytest" &>/dev/null; then
        # The -m form, not the bare pytest executable: it puts the repo root on
        # sys.path, which is what lets `from c2sync import ...` resolve in the
        # tests without c2sync being installed.
        echo "${SCRIPT_DIR}/.venv/bin/python -m pytest"
    elif command -v pytest &>/dev/null; then
        echo "pytest"
    fi
}

VERSION=$("${PYTHON}" - <<'PYEOF'
import tomllib
with open('pyproject.toml', 'rb') as f:
    print(tomllib.load(f)['project']['version'])
PYEOF
)
[ -n "${VERSION}" ] || die "Could not read [project].version from pyproject.toml"

# The archive is built for the build host's architecture (see the --platform
# note below), so the name says which. Naming it now means adding another arch
# later is additive rather than a rename of already-published assets.
ARCH="$(uname -m)"
DIST_NAME="c2sync-${VERSION}-linux-${ARCH}"
BUILD_DIR=$(mktemp -d)
STAGE="${BUILD_DIR}/${DIST_NAME}"

# Only clean up a build/ directory that pip's setuptools backend created here,
# never one that already existed.
BUILD_PREEXISTED=0
[ -d "${SCRIPT_DIR}/build" ] && BUILD_PREEXISTED=1

cleanup() {
    rm -rf "${BUILD_DIR}"
    [ "${BUILD_PREEXISTED}" -eq 0 ] && rm -rf "${SCRIPT_DIR}/build"
    return 0
}
trap cleanup EXIT

if [ "${RUN_TESTS}" -eq 1 ]; then
    PYTEST_CMD=$(resolve_pytest)
    [ -n "${PYTEST_CMD}" ] || die "pytest not found. Install it (pip install -e '.[dev]'), set PYTEST, or pass --skip-tests."
    echo "Running tests (${PYTEST_CMD})..."
    # shellcheck disable=SC2086
    ${PYTEST_CMD} c2sync/tests -q || die "Tests failed - aborting build."
fi

echo "Building c2sync ${VERSION}..."
mkdir -p "${STAGE}/wheels"

# --no-cache-dir on this call only: pip caches locally-built wheels, and would
# otherwise happily serve a stale c2sync wheel whenever the source changed
# without a version bump. Dependency downloads below are cached as normal.
"${PYTHON}" -m pip wheel . --no-deps --no-cache-dir -w "${STAGE}/wheels" -q

# Runtime dependencies only. Anything under [project.optional-dependencies]
# (pytest and friends) is deliberately excluded from the shipped archive.
mapfile -t DEPS < <("${PYTHON}" - <<'PYEOF'
import re, tomllib
with open('pyproject.toml', 'rb') as f:
    deps = tomllib.load(f)['project'].get('dependencies', [])
if not deps:
    raise SystemExit('No dependencies found in [project].dependencies')
for d in deps:
    name = re.split(r'[>=<!~;\s\[]', d)[0].strip()
    if name:
        print(name)
PYEOF
)
[ "${#DEPS[@]}" -gt 0 ] || die "Could not read [project].dependencies from pyproject.toml"

# No --platform: pip matches platform tags exactly rather than by minimum, and
# the tree mixes manylinux_2_17/_2_28/_2_34 wheels, so pinning one tag makes the
# resolve fail. Inheriting the build host's tags means the archive is built for
# the build host's architecture (x86_64 here).
for v in "${PY_VERSIONS[@]}"; do
    echo "Vendoring dependencies for Python ${v}..."
    "${PYTHON}" -m pip download "${DEPS[@]}" \
        -d "${STAGE}/wheels" \
        --python-version "${v}" \
        --only-binary :all: \
        -q || die "Failed to download dependency wheels for Python ${v}."
done

cp "${SCRIPT_DIR}/install.sh" "${SCRIPT_DIR}/uninstall.sh" "${STAGE}/"
chmod +x "${STAGE}/install.sh" "${STAGE}/uninstall.sh"

# install.sh reads these; it must not have to parse a wheel filename.
printf '%s\n' "${VERSION}" > "${STAGE}/VERSION"
printf '%s\n' "${PY_VERSIONS[@]}" > "${STAGE}/PYTHON_VERSIONS"

( cd "${STAGE}/wheels" && sha256sum ./*.whl > ../SHA256SUMS )

mkdir -p "${SCRIPT_DIR}/dist"
OUTPUT="${SCRIPT_DIR}/dist/${DIST_NAME}.tar.gz"
tar -czf "${OUTPUT}" -C "${BUILD_DIR}" "${DIST_NAME}"

WHEEL_COUNT=$(find "${STAGE}/wheels" -name '*.whl' | wc -l)
echo "Created dist/${DIST_NAME}.tar.gz ($(du -h "${OUTPUT}" | cut -f1), ${WHEEL_COUNT} wheels)"

if [ "${TEST_INSTALL}" -eq 1 ]; then
    command -v docker &>/dev/null || die "--test-install requires docker."
    for v in "${PY_VERSIONS[@]}"; do
        echo "Testing install under Python ${v}..."
        docker build -q \
            -f "${SCRIPT_DIR}/Dockerfile.test" \
            --build-arg "PYTHON_VERSION=${v}" \
            --build-arg "DIST_NAME=${DIST_NAME}" \
            -t "c2sync-test:${v}" "${SCRIPT_DIR}" >/dev/null \
            || die "Install test failed under Python ${v}."
        echo "  Python ${v}: ok"
    done
fi

echo
echo "Install with:"
echo "  tar -xzf dist/${DIST_NAME}.tar.gz && ./${DIST_NAME}/install.sh"
