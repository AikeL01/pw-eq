# pw-eq

PipeWire-native Parametric EQ manager for Linux (CachyOS / Arch / Wayland).

Loads Equalizer APO / AutoEQ / Squiglink / PEQdB `EQ.txt` presets directly into PipeWire's built-in `param_eq` filter-chain using WirePlumber Smart Filters (`filter.smart = true`), so your hardware output stays the single volume slider.

## Features

- **100% PipeWire Native (`param_eq`)**: Runs in an isolated systemd user service (`pw-eq.service`) without restarting `pipewire.service`.
- **Single Volume Slider (`filter.smart = true`)**: Keeps your hardware audio device as the default sink so volume keys and desktop sliders control loudness directly.
- **GUI + System Tray (`pw-eq-gui`)**:
  - Drag & drop `EQ.txt` files or paste directly from clipboard.
  - Live 20 Hz – 20 kHz frequency response curve with clipping warning & 1-click **Auto-Preamp**.
  - Per-band toggle table + raw `EQ.txt` editor.
  - System tray icon for quick bypass and profile switching.
- **CLI & Hotkey Friendly (`pw-eq`)**: Bind `pw-eq toggle` to a KDE or Hyprland shortcut.

## Requirements

- `pipewire`
- `wireplumber` (0.5+ for Smart Filter mode)
- `python`
- `python-pyqt6`

## Installation

```bash
git clone https://github.com/AikeL01/pw-eq.git
cd pw-eq
./install.sh
