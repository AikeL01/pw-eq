#!/usr/bin/env bash
set -euo pipefail

PURGE=false
KEEP_PROFILES=false
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=true ;;
        --keep-profiles) KEEP_PROFILES=true ;;
        -h|--help)
            echo "Usage: $0 [--purge | --keep-profiles]"
            echo "  --purge          Also delete saved EQ profiles in ~/.config/pw-eq/"
            echo "  --keep-profiles  Keep saved EQ profiles without prompting"
            exit 0
            ;;
    esac
done

echo "==> Stopping pw-eq GUI and service..."
pkill -f "pw-eq-gui" 2>/dev/null || true

# Cleanly stop service and restore previous hardware default sink if core is present
python3 - <<'PY' 2>/dev/null || true
import sys
from pathlib import Path
lib = Path.home() / ".local" / "lib" / "pw-eq"
if (lib / "pw_eq_core.py").exists():
    sys.path.insert(0, str(lib))
    import pw_eq_core as core
    core.stop_eq(disable_on_boot=True)
PY

systemctl --user stop pw-eq.service 2>/dev/null || true
systemctl --user disable pw-eq.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/pw-eq.service"
systemctl --user daemon-reload 2>/dev/null || true

echo "==> Removing installed files..."
rm -rf "$HOME/.local/lib/pw-eq"
rm -f "$HOME/.local/bin/pw-eq" \
      "$HOME/.local/bin/pw-eq-gui" \
      "$HOME/.local/bin/pw-eq-uninstall" \
      "$HOME/.local/share/applications/pw-eq.desktop" \
      "$HOME/.config/autostart/pw-eq-tray.desktop" \
      "$HOME/.config/pw-eq/active.conf" \
      "$HOME/.config/pw-eq/state.json"

CFG_DIR="$HOME/.config/pw-eq"
if [[ -d "$CFG_DIR" ]]; then
    if $PURGE; then
        rm -rf "$CFG_DIR"
        echo "==> Removed saved profiles ($CFG_DIR)."
    elif ! $KEEP_PROFILES && [[ -t 0 ]] && [[ -n "$(ls -A "$CFG_DIR/profiles" 2>/dev/null || true)" ]]; then
        read -r -p "Also delete saved EQ profiles in $CFG_DIR/profiles? [y/N] " ans
        if [[ "$ans" =~ ^[Yy]$ ]]; then
            rm -rf "$CFG_DIR"
            echo "==> Removed saved profiles."
        else
            echo "==> Kept saved profiles in $CFG_DIR/profiles."
        fi
    else
        rmdir "$CFG_DIR/profiles" 2>/dev/null || true
        rmdir "$CFG_DIR" 2>/dev/null || true
        [[ -d "$CFG_DIR/profiles" ]] && echo "==> Kept saved profiles in $CFG_DIR/profiles."
    fi
fi

echo "✅ pw-eq uninstalled."
