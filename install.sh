#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${HOME}/.local/share/c2sync"
BIN_DIR="/usr/local/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── helpers ────────────────────────────────────────────────────────────────────

die() { echo "Error: $*" >&2; exit 1; }

# ── system dependency check ───────────────────────────────────────────────────

command -v python3 &>/dev/null || die "python3 is required but not found."

python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" \
    || die "Python 3.10+ required (found $(python3 --version))."

install_system_deps() {
    local pkgs=("$@")
    if command -v apt-get &>/dev/null; then
        local apt_pkgs=()
        for p in "${pkgs[@]}"; do
            case "${p}" in
                git)          apt_pkgs+=("git") ;;
                python3-venv) apt_pkgs+=("python3-venv") ;;
            esac
        done
        sudo apt-get install -y "${apt_pkgs[@]}"
    elif command -v dnf &>/dev/null; then
        local dnf_pkgs=()
        for p in "${pkgs[@]}"; do
            case "${p}" in
                git)          dnf_pkgs+=("git") ;;
                python3-venv) dnf_pkgs+=("python3") ;;
            esac
        done
        sudo dnf install -y "${dnf_pkgs[@]}"
    elif command -v pacman &>/dev/null; then
        local pac_pkgs=()
        for p in "${pkgs[@]}"; do
            case "${p}" in
                git)          pac_pkgs+=("git") ;;
                python3-venv) pac_pkgs+=("python") ;;
            esac
        done
        sudo pacman -S --noconfirm "${pac_pkgs[@]}"
    elif command -v zypper &>/dev/null; then
        local zy_pkgs=()
        for p in "${pkgs[@]}"; do
            case "${p}" in
                git)          zy_pkgs+=("git") ;;
                python3-venv) zy_pkgs+=("python3-virtualenv") ;;
            esac
        done
        sudo zypper install -y "${zy_pkgs[@]}"
    else
        die "No supported package manager found. Install manually: ${pkgs[*]}"
    fi
}

MISSING=()
command -v git &>/dev/null          || MISSING+=("git")
python3 -m venv --help &>/dev/null  || MISSING+=("python3-venv")

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "Missing system dependencies: ${MISSING[*]}"
    read -r -p "Install them now? [y/N] " REPLY
    if [[ "${REPLY}" =~ ^[Yy]$ ]]; then
        install_system_deps "${MISSING[@]}"
    else
        die "Cannot proceed without required dependencies."
    fi
fi

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
dep_wheels=()
for w in "${INSTALL_DIR}/wheels/"*.whl; do
    [[ "$(basename "$w")" != c2sync-* ]] && dep_wheels+=("$w")
done
"${PIP}" install --quiet "${dep_wheels[@]}"

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
