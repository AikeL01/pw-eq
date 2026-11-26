#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$HOME/.local/lib/pw-eq"
BIN_DIR="$HOME/.local/bin"

for cmd in pipewire pw-dump wpctl systemctl python3; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "Missing required command: $cmd"; exit 1; }
done

mkdir -p "$LIB_DIR" "$BIN_DIR"
cp "$ROOT_DIR/pw_eq_core.py" "$LIB_DIR/pw_eq_core.py"
cp "$ROOT_DIR/pw-eq" "$BIN_DIR/pw-eq"
cp "$ROOT_DIR/pw-eq-gui" "$BIN_DIR/pw-eq-gui"
cp "$ROOT_DIR/uninstall.sh" "$BIN_DIR/pw-eq-uninstall"
chmod +x "$BIN_DIR/pw-eq" "$BIN_DIR/pw-eq-gui" "$BIN_DIR/pw-eq-uninstall"

echo "Installed pw-eq to $BIN_DIR"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo "Note: Add $BIN_DIR to your PATH to run 'pw-eq' and 'pw-eq-gui' directly."
fi
