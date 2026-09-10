#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/c2sync"
BIN_DIR="${HOME}/.local/bin"
LAUNCHER="${BIN_DIR}/c2sync"

ASSUME_YES=0
[ "${1:-}" = "-y" ] && ASSUME_YES=1

# -e is false for a dangling symlink, so -L is tested too: a launcher left
# pointing at an already-deleted install directory still needs cleaning up.
launcher_exists() { [ -L "${LAUNCHER}" ] || [ -e "${LAUNCHER}" ]; }

if [ ! -d "${INSTALL_DIR}" ] && ! launcher_exists; then
    echo "c2sync is not installed."
    exit 0
fi

# Read the link target up front. Resolving it after INSTALL_DIR is removed would
# leave a dangling symlink that no longer looks like ours.
LINK_TARGET=""
[ -L "${LAUNCHER}" ] && LINK_TARGET=$(readlink "${LAUNCHER}")

echo "This will remove:"
[ -d "${INSTALL_DIR}" ] && echo "  ${INSTALL_DIR}"
launcher_exists         && echo "  ${LAUNCHER}"
echo
echo "Project directories (./.c2sync/ and their git history) are left untouched."
echo

if [ "${ASSUME_YES}" -eq 0 ]; then
    read -r -p "Continue? [y/N] " REPLY
    [[ "${REPLY}" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

if [ -d "${INSTALL_DIR}" ]; then
    rm -rf "${INSTALL_DIR}"
    echo "Removed ${INSTALL_DIR}"
fi

# Only remove a launcher that points into our own install directory, so a
# c2sync installed some other way (pip install --user, say) is left alone.
if [ -n "${LINK_TARGET}" ]; then
    case "${LINK_TARGET}" in
        "${INSTALL_DIR}"/*) rm -f "${LAUNCHER}"; echo "Removed ${LAUNCHER}" ;;
        *) echo "Left ${LAUNCHER} alone (points at ${LINK_TARGET}, not ${INSTALL_DIR})" ;;
    esac
elif [ -e "${LAUNCHER}" ]; then
    echo "Left ${LAUNCHER} alone (not a symlink created by this installer)"
fi

echo
echo "c2sync uninstalled."
