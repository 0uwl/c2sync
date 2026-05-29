#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${HOME}/.local/share/c2sync"
BIN_DIR="/usr/local/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── helpers ────────────────────────────────────────────────────────────────────

die() { echo "Error: $*" >&2; exit 1; }

# ── preflight ─────────────────────────────────────────────────────────────────

command -v python3 &>/dev/null || die "python3 is required but not found."

python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" \
    || die "Python 3.9+ required (found $(python3 --version))."

WHEEL=$(ls "${SCRIPT_DIR}/wheels/c2sync-"*.whl 2>/dev/null | head -1)
[ -n "${WHEEL}" ] || die "c2sync wheel not found in wheels/. Re-run build.sh."

# ── sudo for /usr/local/bin ───────────────────────────────────────────────────

write_bin() {
    cat << 'EOF'
#!/usr/bin/env bash
exec "${HOME}/.local/share/c2sync/venv/bin/c2sync" "$@"
EOF
}

if [ -w "${BIN_DIR}" ]; then
    USE_SUDO=0
elif command -v sudo &>/dev/null; then
    USE_SUDO=1
    echo "sudo required to write to ${BIN_DIR}"
else
    die "Cannot write to ${BIN_DIR} and sudo is unavailable. Run as root."
fi

# ── install ───────────────────────────────────────────────────────────────────

echo "Installing to ${INSTALL_DIR} ..."

rm -rf "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}/wheels"

cp "${SCRIPT_DIR}/wheels/"*.whl "${INSTALL_DIR}/wheels/"

python3 -m venv "${INSTALL_DIR}/venv"

PIP="${INSTALL_DIR}/venv/bin/pip"

echo "Installing dependencies..."
"${PIP}" install --quiet --no-index \
    --find-links="${INSTALL_DIR}/wheels" \
    pyserial watchdog pytest click rich gitpython

echo "Installing c2sync..."
"${PIP}" install --quiet --no-index --no-deps "${INSTALL_DIR}/wheels/$(basename "${WHEEL}")"

echo "Creating launcher at ${BIN_DIR}/c2sync ..."
if [ "${USE_SUDO}" -eq 1 ]; then
    write_bin | sudo tee "${BIN_DIR}/c2sync" > /dev/null
    sudo chmod +x "${BIN_DIR}/c2sync"
else
    write_bin > "${BIN_DIR}/c2sync"
    chmod +x "${BIN_DIR}/c2sync"
fi

echo ""
echo "c2sync ${VERSION:-} installed successfully."
echo "Run: c2sync --help"
