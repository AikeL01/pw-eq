# pw-eq

A lightweight, **PipeWire-native Parametric EQ** manager for Linux (CachyOS / Arch / Wayland / KDE / Hyprland).

Load any Equalizer APO / AutoEQ / Squiglink / PEQdB `EQ.txt` preset directly into PipeWire's built-in `param_eq` filter-chain DSP — no heavy external audio daemons, no restarting `pipewire.service`, and **only one hardware volume slider**.

## Features

- **100% PipeWire Native (`libpipewire-module-filter-chain` + `param_eq`)**: Runs in an isolated systemd user service (`pw-eq.service`).
- **Single Volume Control (`filter.smart = true`)**: Uses WirePlumber Smart Filters so your hardware output remains the default sink and your desktop volume keys/applet control loudness directly.
- **Convenient GUI + System Tray (`pw-eq-gui`)**:
  - Drag & drop `EQ.txt` files or paste straight from clipboard (AutoEQ, Squiglink, PEQdB).
  - Live 20 Hz – 20 kHz frequency response curve with clipping warning & 1-click **Auto-Preamp**.
  - Per-band toggle table + raw `EQ.txt` editor.
  - System tray icon for 1-click bypass and profile switching.
- **CLI & Hotkey Friendly (`pw-eq`)**: Bind `pw-eq toggle` to a KDE or Hyprland shortcut.

## Requirements

- `pipewire`
- `wireplumber` (0.5+ recommended for Smart Filter mode)
- `python` (>= 3.10)
- `python-pyqt6` (for GUI & tray)

## Installation

### Option 1: User-local install (any distro)
```bash
git clone https://github.com/<your-username>/pw-eq.git
cd pw-eq
./install.sh
