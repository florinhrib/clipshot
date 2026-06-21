#!/usr/bin/env bash
# ClipShot uninstall script.
#
# Usage:
#   ./uninstall.sh           # remove app, keep config + history
#   ./uninstall.sh --purge   # also delete ~/.config/clipshot and history

set -euo pipefail

_ok()   { printf '\033[0;32m  ✔  %s\033[0m\n' "$*"; }
_info() { printf '\033[0;34m  ▶  %s\033[0m\n' "$*"; }
_warn() { printf '\033[0;33m  ⚠  %s\033[0m\n' "$*"; }

PURGE=false
for arg in "$@"; do
    [[ "$arg" == "--purge" ]] && PURGE=true
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. remove GNOME keyboard shortcuts ───────────────────────────────────────
_info "Removing GNOME keyboard shortcuts…"
python3 -c "
try:
    from clipshot import shortcuts
    shortcuts.remove_all()
    print('shortcuts removed')
except Exception as e:
    print(f'shortcuts removal skipped: {e}')
" 2>/dev/null | grep -v '^$' || true
_ok "Shortcuts step done"

# ── 2. stop + disable systemd service ────────────────────────────────────────
_info "Stopping and disabling clipshot.service…"
if command -v systemctl &>/dev/null; then
    systemctl --user stop    clipshot.service 2>/dev/null || true
    systemctl --user disable clipshot.service 2>/dev/null || true
    systemctl --user daemon-reload 2>/dev/null || true
fi
SERVICE_FILE="$HOME/.config/systemd/user/clipshot.service"
if [[ -f "$SERVICE_FILE" ]]; then
    rm -f "$SERVICE_FILE"
    _ok "Removed $SERVICE_FILE"
fi

# ── 3. remove autostart entry ────────────────────────────────────────────────
_info "Removing autostart entry…"
AUTOSTART="$HOME/.config/autostart/clipshot-daemon.desktop"
[[ -f "$AUTOSTART" ]] && rm -f "$AUTOSTART" && _ok "Removed $AUTOSTART" || true

# ── 4. remove GNOME Shell extension ─────────────────────────────────────────
_info "Disabling and removing GNOME Shell extension…"
EXT_UUID="clipshot@florinlab.uk"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"
if command -v gnome-extensions &>/dev/null; then
    gnome-extensions disable "$EXT_UUID" 2>/dev/null || true
fi
if [[ -d "$EXT_DIR" ]]; then
    rm -rf "$EXT_DIR"
    _ok "Removed extension at $EXT_DIR"
fi

# ── 5. remove application launcher ──────────────────────────────────────────
LAUNCHER="$HOME/.local/share/applications/clipshot.desktop"
[[ -f "$LAUNCHER" ]] && rm -f "$LAUNCHER" && _ok "Removed $LAUNCHER" || true
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

# ── 6. pip uninstall ─────────────────────────────────────────────────────────
_info "Uninstalling clipshot Python package…"
python3 -m pip uninstall -y clipshot 2>/dev/null && _ok "pip package removed" \
    || _warn "pip uninstall skipped (already removed?)"

# ── 7. purge config + history ────────────────────────────────────────────────
if $PURGE; then
    _info "--purge: removing config and history…"
    CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/clipshot"
    DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/clipshot"
    [[ -d "$CONFIG_DIR" ]] && rm -rf "$CONFIG_DIR" && _ok "Removed $CONFIG_DIR"
    [[ -d "$DATA_DIR"   ]] && rm -rf "$DATA_DIR"   && _ok "Removed $DATA_DIR"
else
    _warn "Config and history kept at ~/.config/clipshot and ~/.local/share/clipshot"
    _warn "Run with --purge to also remove them"
fi

echo ""
_ok "ClipShot uninstalled."
