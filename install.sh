#!/usr/bin/env bash
# ClipShot install script — idempotent, curl-pipe-able.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/florinhrib/clipshot/main/install.sh | bash
#   # or, after cloning:
#   ./install.sh
#
# What it does:
#   1. Installs system packages (PyGObject/GTK4/libadwaita, wl-clipboard, tesseract)
#      via your distro's package manager.
#   2. pip-installs the ClipShot Python package to ~/.local.
#   3. Registers GNOME keyboard shortcuts.
#   4. Installs and enables the systemd user service.
#   5. Installs the GNOME Shell extension (optional fidelity layer).
#   6. Copies the autostart .desktop entry.
#
# Every step is idempotent; re-running is safe.

set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
_ok()   { printf '\033[0;32m  ✔  %s\033[0m\n' "$*"; }
_info() { printf '\033[0;34m  ▶  %s\033[0m\n' "$*"; }
_warn() { printf '\033[0;33m  ⚠  %s\033[0m\n' "$*"; }
_err()  { printf '\033[0;31m  ✘  %s\033[0m\n' "$*" >&2; }

# ── locate the repo root ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$SCRIPT_DIR"

_info "ClipShot installer — working directory: $SCRIPT_DIR"

# ── detect package manager ───────────────────────────────────────────────────
HAS_SUDO=false
if command -v sudo &>/dev/null && sudo -n true 2>/dev/null; then
    HAS_SUDO=true
fi

PM=""
if   command -v dnf  &>/dev/null; then PM="dnf"
elif command -v apt  &>/dev/null; then PM="apt"
elif command -v pacman &>/dev/null; then PM="pacman"
fi

# ── helper: run or warn ──────────────────────────────────────────────────────
run_sudo() {
    if $HAS_SUDO; then
        sudo "$@"
    else
        _warn "sudo not available — skipping: $*"
        _warn "Run manually:  sudo $*"
    fi
}

# ── 1. system dependencies ───────────────────────────────────────────────────
_info "Installing system dependencies via $PM (requires sudo)…"

case "$PM" in
  dnf)
    PKGS=(
        python3-gobject          # PyGObject — MUST be system, not pip
        gtk4
        libadwaita
        wl-clipboard
        tesseract
        tesseract-langpack-eng
        # optional: input-remapper for mouse-button hotkeys
        # input-remapper
    )
    run_sudo dnf install -y "${PKGS[@]}" || _warn "Some dnf packages failed — continuing"
    ;;
  apt)
    run_sudo apt-get update -qq || true
    PKGS=(
        python3-gi               # PyGObject — MUST be system, not pip
        gir1.2-gtk-4.0
        gir1.2-adw-1
        wl-clipboard
        tesseract-ocr
        tesseract-ocr-eng
        # optional: input-remapper
        # input-remapper
    )
    run_sudo apt-get install -y "${PKGS[@]}" || _warn "Some apt packages failed — continuing"
    ;;
  pacman)
    PKGS=(
        python-gobject           # PyGObject — MUST be system, not pip
        gtk4
        libadwaita
        wl-clipboard
        tesseract
        tesseract-data-eng
        # optional: input-remapper
        # input-remapper
    )
    run_sudo pacman -S --needed --noconfirm "${PKGS[@]}" || _warn "Some pacman packages failed — continuing"
    ;;
  *)
    _warn "No supported package manager detected (dnf/apt/pacman)."
    _warn "Install manually: python3-gobject gtk4 libadwaita wl-clipboard tesseract"
    ;;
esac
_ok "System dependencies done"

# ── 2. pip-install the package ───────────────────────────────────────────────
_info "Installing ClipShot Python package to ~/.local (pip --user)…"
python3 -m pip install --user --quiet . 2>&1 | tail -5 || {
    _err "pip install failed — check the output above"
    exit 1
}
_ok "Python package installed"

# ── ensure ~/.local/bin is on PATH ───────────────────────────────────────────
LOCAL_BIN="$HOME/.local/bin"
if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    _warn "~/.local/bin is NOT in your PATH."
    _warn "Add this to your ~/.bashrc or ~/.zshrc and restart your shell:"
    _warn '  export PATH="$HOME/.local/bin:$PATH"'
fi

# ── 3. GNOME keyboard shortcuts ──────────────────────────────────────────────
_info "Registering GNOME keyboard shortcuts…"
if python3 -c "
from clipshot.config import Config
try:
    from clipshot import shortcuts
    shortcuts.apply_all(Config.load())
    print('shortcuts registered')
except Exception as e:
    print(f'shortcuts skipped: {e}')
" 2>&1 | grep -v '^$'; then
    _ok "GNOME shortcuts registered"
else
    _warn "Shortcut registration skipped (no GNOME session? Run manually after login)"
fi

# ── 4. systemd user service ──────────────────────────────────────────────────
_info "Installing systemd user service…"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"

if [[ -f "data/clipshot.service" ]]; then
    cp "data/clipshot.service" "$SYSTEMD_USER_DIR/clipshot.service"
    _ok "Service file copied to $SYSTEMD_USER_DIR/clipshot.service"
else
    _warn "data/clipshot.service not found — skipping service install"
fi

if command -v systemctl &>/dev/null; then
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now clipshot.service 2>/dev/null && _ok "clipshot.service enabled + started" \
        || _warn "systemctl enable failed — try: systemctl --user enable --now clipshot.service"
else
    _warn "systemctl not found — start manually: clipshot --daemon &"
fi

# ── 5. autostart .desktop entry ─────────────────────────────────────────────
_info "Installing autostart entry…"
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

if [[ -f "data/clipshot-daemon.desktop" ]]; then
    cp "data/clipshot-daemon.desktop" "$AUTOSTART_DIR/clipshot-daemon.desktop"
    _ok "Autostart entry installed at $AUTOSTART_DIR/clipshot-daemon.desktop"
else
    _warn "data/clipshot-daemon.desktop not found — skipping"
fi

# ── 6. GNOME Shell extension ─────────────────────────────────────────────────
_info "Installing GNOME Shell extension (clipshot@florinlab.uk)…"
EXT_UUID="clipshot@florinlab.uk"
EXT_TARGET="$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"

if [[ -d "extension" ]]; then
    mkdir -p "$EXT_TARGET"
    cp -r extension/. "$EXT_TARGET/"
    _ok "Extension files copied to $EXT_TARGET"

    if command -v gnome-extensions &>/dev/null; then
        gnome-extensions enable "$EXT_UUID" 2>/dev/null && _ok "Extension enabled" \
            || _warn "Could not enable extension — log out and in, then: gnome-extensions enable $EXT_UUID"
    else
        _warn "gnome-extensions not found — enable manually after next login"
    fi
else
    _warn "extension/ directory not found — GNOME Shell extension not installed"
    _warn "(ClipShot works fine without it; window-specific capture uses region select as fallback)"
fi

# ── 7. application .desktop ──────────────────────────────────────────────────
_info "Installing application launcher…"
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
if [[ -f "data/clipshot.desktop" ]]; then
    cp "data/clipshot.desktop" "$APPS_DIR/clipshot.desktop"
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
    _ok "Application launcher installed"
fi

# ── summary ──────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
_ok "ClipShot installation complete!"
echo ""
echo "  Next steps:"
echo ""
echo "  1) TRAY ICON — requires the AppIndicator GNOME extension:"
echo "     Open https://extensions.gnome.org/extension/615/"
echo "     or install: gnome-extensions install appindicatorsupport@rgcjonas.gmail.com"
echo ""
echo "  2) GNOME SHELL EXTENSION — for per-window capture accuracy:"
echo "     Log out and back in, then:"
echo "     gnome-extensions enable clipshot@florinlab.uk"
echo ""
echo "  3) MOUSE BUTTON (BTN_SIDE → region capture):"
echo "     Import presets/input-remapper/clipshot.json via input-remapper-gtk."
echo "     Requires: input-remapper service running (sudo systemctl enable --now input-remapper)"
echo ""
echo "  Default hotkeys (register via GNOME Settings → Keyboard → Shortcuts):"
echo "    Super+Shift+S  →  Region capture"
echo "    Super+Shift+F  →  Fullscreen capture"
echo "    Super+Shift+W  →  Window capture"
echo "    Super+Shift+T  →  Extract text (OCR)"
echo "    Super+Shift+R  →  Repeat last region"
echo ""
echo "  Run 'clipshot --help' for all CLI flags."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
