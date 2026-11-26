# pw-eq

A lightweight, PipeWire-native Parametric EQ manager for Linux.

Loads Equalizer APO / AutoEQ / Squiglink / PEQdB `EQ.txt` presets directly into PipeWire's built-in `param_eq` filter-chain using WirePlumber Smart Filters (`filter.smart = true`), keeping your hardware output as the single volume slider.

## Features

- **100% PipeWire Native (`param_eq`)**: Runs in an isolated systemd user service (`pw-eq.service`) without restarting `pipewire.service`.
- **Single Volume Slider (`filter.smart = true`)**: Keeps your hardware audio device as the default sink so volume keys and desktop sliders control loudness directly.
- **GUI + System Tray (`pw-eq-gui`)**:
  - Drag & drop `EQ.txt` files or paste directly from clipboard.
  - Live 20 Hz – 20 kHz frequency response curve with clipping warning & 1-click **Auto-Preamp**.
  - Per-band toggle table + raw `EQ.txt` editor.
  - System tray icon for quick bypass and profile switching.
- **CLI & Hotkey Friendly (`pw-eq`)**: Bind `pw-eq toggle` to a desktop/WM shortcut.

## Requirements

- **Linux with `systemd`** (uses `systemctl --user`)
- **`pipewire`**
- **`wireplumber`** (0.5+ recommended for Smart Filter mode; older 0.4 setups can uncheck Smart Filter mode in the GUI)
- **Python 3.10+**
- **PyQt6** (for GUI & tray):
  - Arch / CachyOS / EndeavourOS: `python-pyqt6` *(auto-installed by `install.sh`)*
  - Fedora / Debian / Ubuntu: `python3-pyqt6` *(install before running `install.sh`)*

Works on any Wayland or X11 desktop/WM (KDE, GNOME, Hyprland, Sway, XFCE, etc.). System tray features require a desktop/panel with tray support.

## Installation

```bash
git clone https://github.com/AikeL01/pw-eq.git
cd pw-eq
./install.sh
```
## Uninstall

From anywhere in your terminal:
```bash
pw-eq-uninstall
```
Or from the cloned repository:
```bash
./uninstall.sh          # asks whether to keep saved profiles
./uninstall.sh --purge  # removes everything including saved profiles
```
