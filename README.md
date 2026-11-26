# pw-eq

A zero-dependency, PipeWire-native Parametric EQ manager for Linux.

Loads Equalizer APO / AutoEQ / Squiglink / PEQdB `.txt` presets directly into PipeWire's built-in `param_eq` filter-chain using WirePlumber Smart Filters (`filter.smart = true`), keeping your hardware output as the single volume slider.

## Features

- **Zero External Dependencies**: Uses only Python 3.10+'s standard library (local Web UI via `http.server`).
- **100% PipeWire Native (`param_eq`)**: Runs in an isolated systemd user service (`pw-eq.service`) without restarting `pipewire.service`.
- **Single Volume Slider (`filter.smart = true`)**: Keeps your hardware audio device as the default sink so volume keys and desktop sliders control loudness directly.
- **Browser UI (`pw-eq-gui`)**: Drag & drop `.txt` presets or paste from clipboard, live 20 Hz – 20 kHz curve with hover readout, 1-click **Auto-Preamp**, per-band toggles, and raw preset editor.

## Requirements

- Linux with `systemd` (`systemctl --user`)
- `pipewire`
- `wireplumber` (0.5+ recommended for Smart Filter mode; 0.4 setups can uncheck Smart Filter mode in the UI)
- `python3` (3.10+)

## Installation

```bash
git clone https://github.com/AikeL01/pw-eq.git
cd pw-eq
./install.sh
```

## Usage

```bash
pw-eq-gui
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
