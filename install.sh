#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/c2sync"
BIN_DIR="${HOME}/.local/bin"

MIN_PY_MAJOR=3
MIN_PY_MINOR=11

die() { echo "Error: $*" >&2; exit 1; }

VERSION="unknown"
[ -f "${SCRIPT_DIR}/VERSION" ] && VERSION=$(cat "${SCRIPT_DIR}/VERSION")

if [ "${EUID}" -eq 0 ]; then
    echo "Warning: running as root installs into root's home (${INSTALL_DIR})."
    echo "         c2sync installs per-user and needs no root privileges."
    echo
fi

# ── prerequisites ─────────────────────────────────────────────────────────────
#
# This installer never calls sudo and never invokes a package manager. If
# something is missing it says what to install and stops, leaving the decision
# to whoever administers the box.

pkg_hint() {
    # $1: apt name, $2: dnf name, $3: pacman name, $4: zypper name
    if   command -v apt-get &>/dev/null; then echo "sudo apt-get install $1"
    elif command -v dnf     &>/dev/null; then echo "sudo dnf install $2"
    elif command -v pacman  &>/dev/null; then echo "sudo pacman -S $3"
    elif command -v zypper  &>/dev/null; then echo "sudo zypper install $4"
    else echo "install the '$1' package for your distribution"
    fi
}

command -v python3 &>/dev/null \
    || die "python3 is required but not found. Try: $(pkg_hint python3 python3 python python3)"

python3 -c "import sys; sys.exit(0 if sys.version_info >= (${MIN_PY_MAJOR}, ${MIN_PY_MINOR}) else 1)" \
    || die "Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ required (found $(python3 --version 2>&1 | awk '{print $2}'))."

PY_TAG=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')

if [ -f "${SCRIPT_DIR}/PYTHON_VERSIONS" ]; then
    if ! grep -qx "${PY_TAG}" "${SCRIPT_DIR}/PYTHON_VERSIONS"; then
        echo "Warning: this archive bundles wheels for Python $(tr '\n' ' ' < "${SCRIPT_DIR}/PYTHON_VERSIONS")"
        echo "         but python3 is ${PY_TAG}. Install may fail on packages that"
        echo "         ship version-specific binary wheels."
        echo
    fi
fi

python3 -c "import ensurepip, venv" 2>/dev/null \
    || die "Python venv support is missing. Try: $(pkg_hint python3-venv python3 python python3-virtualenv)"

# git_ops.py shells out to the git binary, so git is a hard runtime requirement.
command -v git &>/dev/null \
    || die "git is required but not found. Try: $(pkg_hint git git git git)"

[ -d "${SCRIPT_DIR}/wheels" ] || die "wheels/ not found. This archive is incomplete."

WHEEL=$(find "${SCRIPT_DIR}/wheels" -maxdepth 1 -name 'c2sync-*.whl' | head -1)
[ -n "${WHEEL}" ] || die "c2sync wheel not found in wheels/. Re-run build.sh."

# ── verify ────────────────────────────────────────────────────────────────────

if [ -f "${SCRIPT_DIR}/SHA256SUMS" ] && command -v sha256sum &>/dev/null; then
    echo "Verifying wheels..."
    ( cd "${SCRIPT_DIR}/wheels" && sha256sum --quiet -c ../SHA256SUMS ) \
        || die "Checksum verification failed. The archive is corrupt or was tampered with."
fi

# ── install ───────────────────────────────────────────────────────────────────

[ -d "${INSTALL_DIR}" ] && echo "Replacing existing installation at ${INSTALL_DIR}"

echo "Installing c2sync ${VERSION} to ${INSTALL_DIR} ..."

rm -rf "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}" "${BIN_DIR}"

python3 -m venv "${INSTALL_DIR}/venv" \
    || { rm -rf "${INSTALL_DIR}"; die "Failed to create virtual environment."; }

# Installing the c2sync wheel by path with the wheelhouse as the only index lets
# pip resolve c2sync's own dependencies and pick the wheels matching this venv's
# interpreter. Passing every dependency wheel by filename instead would force
# pip to install wheels built for other Python minors, which cannot work.
# Installed straight from the archive rather than from a copy under
# INSTALL_DIR: the wheelhouse is ~14MB and the archive is already the
# reinstall path, so keeping a second copy around buys nothing.
echo "Installing dependencies (offline)..."
"${INSTALL_DIR}/venv/bin/pip" install --quiet \
    --no-index --find-links "${SCRIPT_DIR}/wheels" "${WHEEL}" \
    || { rm -rf "${INSTALL_DIR}"; die "Installation failed."; }

echo "Linking ${BIN_DIR}/c2sync ..."
ln -sfn "${INSTALL_DIR}/venv/bin/c2sync" "${BIN_DIR}/c2sync"

# ── report ────────────────────────────────────────────────────────────────────

echo
echo "c2sync ${VERSION} installed successfully."

case ":${PATH}:" in
    *":${BIN_DIR}:"*)
        echo "Run: c2sync"
        ;;
    *)
        echo
        echo "Note: ${BIN_DIR} is not on your PATH. Add it with:"
        echo
        echo "  echo 'export PATH=\"\${HOME}/.local/bin:\${PATH}\"' >> ~/.bashrc"
        echo "  source ~/.bashrc"
        echo
        echo "Or run it directly: ${BIN_DIR}/c2sync"
        ;;
esac
