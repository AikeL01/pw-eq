#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$HOME/.local/lib/pw-eq"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"

for cmd in pipewire pw-dump wpctl systemctl python3; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "Missing required command: $cmd"; exit 1; }
done

if ! python3 -c "import PyQt6" 2>/dev/null; then
    if command -v pacman >/dev/null 2>&1; then
        echo "==> Installing python-pyqt6 via pacman..."
        sudo pacman -S --needed --noconfirm python-pyqt6
    else
        echo "Warning: PyQt6 is not installed. Install python3-pyqt6 / PyQt6 for GUI support."
    fi
fi

mkdir -p "$LIB_DIR" "$BIN_DIR" "$APP_DIR"
cp "$ROOT_DIR/pw_eq_core.py" "$LIB_DIR/pw_eq_core.py"
cp "$ROOT_DIR/pw-eq" "$BIN_DIR/pw-eq"
cp "$ROOT_DIR/pw-eq-gui" "$BIN_DIR/pw-eq-gui"
chmod +x "$BIN_DIR/pw-eq" "$BIN_DIR/pw-eq-gui"

cat <<EOF > "$APP_DIR/pw-eq.desktop"
[Desktop Entry]
Type=Application
Name=PipeWire Parametric EQ
Comment=Native PipeWire EQ.txt loader and profile switcher
Exec=$BIN_DIR/pw-eq-gui
Icon=audio-card
Terminal=false
Categories=AudioVideo;Audio;Settings;
EOF

echo "Installed pw-eq to $BIN_DIR"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo "Note: Add $BIN_DIR to your PATH to run 'pw-eq' and 'pw-eq-gui' directly."
fi
