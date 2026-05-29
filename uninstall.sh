#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${HOME}/.local/share/c2sync"
BIN_DIR="/usr/local/bin"

die() { echo "Error: $*" >&2; exit 1; }

# ── check anything is actually installed ──────────────────────────────────────

if [ ! -d "${INSTALL_DIR}" ] && [ ! -f "${BIN_DIR}/c2sync" ]; then
    echo "c2sync is not installed."
    exit 0
fi

# ── sudo for /usr/local/bin ───────────────────────────────────────────────────

if [ -w "${BIN_DIR}" ]; then
    USE_SUDO=0
elif command -v sudo &>/dev/null; then
    USE_SUDO=1
else
    die "Cannot write to ${BIN_DIR} and sudo is unavailable. Run as root."
fi

# ── confirm ───────────────────────────────────────────────────────────────────

echo "This will remove:"
[ -d "${INSTALL_DIR}" ] && echo "  ${INSTALL_DIR}"
[ -f "${BIN_DIR}/c2sync" ] && echo "  ${BIN_DIR}/c2sync"
echo ""
read -r -p "Continue? [y/N] " REPLY
[[ "${REPLY}" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# ── remove ────────────────────────────────────────────────────────────────────

if [ -d "${INSTALL_DIR}" ]; then
    rm -rf "${INSTALL_DIR}"
    echo "Removed ${INSTALL_DIR}"
fi

if [ -f "${BIN_DIR}/c2sync" ]; then
    if [ "${USE_SUDO}" -eq 1 ]; then
        sudo rm "${BIN_DIR}/c2sync"
    else
        rm "${BIN_DIR}/c2sync"
    fi
    echo "Removed ${BIN_DIR}/c2sync"
fi

echo ""
echo "c2sync uninstalled."
