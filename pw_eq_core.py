#!/usr/bin/env python3
from __future__ import annotations

import cmath
import json
import math
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

CONFIG_DIR = Path.home() / ".config" / "pw-eq"
PROFILES_DIR = CONFIG_DIR / "profiles"
ACTIVE_CONF = CONFIG_DIR / "active.conf"
STATE_FILE = CONFIG_DIR / "state.json"
SERVICE_NAME = "pw-eq.service"
SERVICE_FILE = Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME
AUTOSTART_FILE = Path.home() / ".config" / "autostart" / "pw-eq-tray.desktop"

EQ_INPUT_NODE = "effect_input.pw_eq_smart"
LEGACY_EQ_INPUT_NODE = "effect_input.pw_eq"
EQ_OUTPUT_NODE = "effect_output.pw_eq"

FILTER_MAP = {
    "PK": "bq_peaking", "PEQ": "bq_peaking", "MODAL": "bq_peaking",
    "LS": "bq_lowshelf", "LSQ": "bq_lowshelf", "LSC": "bq_lowshelf", "LSCQ": "bq_lowshelf",
    "HS": "bq_highshelf", "HSQ": "bq_highshelf", "HSC": "bq_highshelf", "HSCQ": "bq_highshelf",
    "LP": "bq_lowpass", "LPQ": "bq_lowpass",
    "HP": "bq_highpass", "HPQ": "bq_highpass",
    "BP": "bq_bandpass", "NO": "bq_notch", "AP": "bq_allpass",
}
NO_GAIN_TYPES = {"bq_lowpass", "bq_highpass", "bq_bandpass", "bq_notch", "bq_allpass"}


@dataclass
class EQBand:
    enabled: bool
    raw_type: str
    pw_type: str
    freq: float
    gain: float = 0.0
    q: float = 0.7071
    line_index: int = -1


@dataclass
class EQProfile:
    name: str
    preamp: float = 0.0
    bands: List[EQBand] = field(default_factory=list)
    raw_text: str = ""
    warnings: List[str] = field(default_factory=list)


def is_eq_node(node_name: str) -> bool:
    return node_name in (EQ_INPUT_NODE, LEGACY_EQ_INPUT_NODE)


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def _num(s: str) -> float:
    s = s.strip()
    return float(s.replace(",", "") if re.match(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$", s) else s.replace(",", "."))


def _fmt(v: float) -> str:
    txt = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if txt in ("-0", "") else txt


def _bw_to_q(bw: float) -> float:
    p = 2.0 ** max(bw, 0.01)
    return math.sqrt(p) / (p - 1.0)


def _slope_to_q(slope: float, gain_db: float) -> float:
    slope = max(min(slope, 10.0), 0.05)
    a = 10.0 ** (gain_db / 40.0)
    inner = (a + 1.0 / a) * (1.0 / slope - 1.0) + 2.0
    return 0.7071 if inner <= 0.01 else 1.0 / math.sqrt(inner)


def parse_eq_text(text: str, name: str = "Untitled") -> EQProfile:
    profile = EQProfile(name=name, raw_text=text)
    preamp_re = re.compile(r"^\s*Preamp\s*:\s*([+-]?\d+(?:[.,]\d+)?)\s*(?:dB)?", re.I)
    filter_re = re.compile(r"^\s*Filter(?:\s+\d+)?\s*:\s*(ON|OFF)\s+([A-Za-z0-9_]+)\s*(.*)$", re.I)
    fc_re = re.compile(r"\bFc\s+([+-]?\d+(?:[.,]\d+)?)\s*(?:Hz)?", re.I)
    gain_re = re.compile(r"\bGain\s+([+-]?\d+(?:[.,]\d+)?)\s*(?:dB)?", re.I)
    q_re = re.compile(r"\bQ\s+([+-]?\d+(?:[.,]\d+)?)", re.I)
    bw_re = re.compile(r"\bBW\s*Oct\s+([+-]?\d+(?:[.,]\d+)?)", re.I)
    s_re = re.compile(r"\bS\s+([+-]?\d+(?:[.,]\d+)?)", re.I)

    for idx, line in enumerate(text.splitlines()):
        st = line.strip()
        if not st or st.startswith(("#", ";")):
            continue
        if pm := preamp_re.match(st):
            profile.preamp = _num(pm.group(1))
            continue
        if fm := filter_re.match(st):
            enabled, raw_type = fm.group(1).upper() == "ON", fm.group(2).upper()
            rest = re.sub(r"^\s*\d+\s*dB\b", "", fm.group(3), flags=re.I)
            pw_type = FILTER_MAP.get(raw_type)
            if not pw_type:
                profile.warnings.append(f"Line {idx + 1}: Unsupported '{raw_type}'")
                continue
            fc_m = fc_re.search(rest)
            if not fc_m:
                profile.warnings.append(f"Line {idx + 1}: Missing Fc")
                continue
            freq = _num(fc_m.group(1))
            gain = _num(gm.group(1)) if (gm := gain_re.search(rest)) else 0.0
            if qm := q_re.search(rest):
                q = _num(qm.group(1))
            elif bm := bw_re.search(rest):
                q = _bw_to_q(_num(bm.group(1)))
            elif (sm := s_re.search(rest)) and pw_type in ("bq_lowshelf", "bq_highshelf"):
                q = _slope_to_q(_num(sm.group(1)), gain)
            else:
                q = 0.7071 if pw_type in NO_GAIN_TYPES or pw_type in ("bq_lowshelf", "bq_highshelf") else 1.0
            profile.bands.append(EQBand(enabled, raw_type, pw_type, freq, gain, max(q, 0.05), idx))
    return profile


def format_eq_text(profile: EQProfile) -> str:
    lines = [f"Preamp: {_fmt(profile.preamp)} dB"]
    for i, b in enumerate(profile.bands, 1):
        st = "ON" if b.enabled else "OFF"
        if b.pw_type in NO_GAIN_TYPES:
            lines.append(f"Filter {i}: {st} {b.raw_type} Fc {_fmt(b.freq)} Hz Q {_fmt(b.q)}")
        else:
            lines.append(f"Filter {i}: {st} {b.raw_type} Fc {_fmt(b.freq)} Hz Gain {_fmt(b.gain)} dB Q {_fmt(b.q)}")
    return "\n".join(lines) + "\n"


def _biquad_coeffs(pw_type: str, freq: float, gain: float, q: float, fs: float = 48000.0):
    freq = max(1.0, min(freq, fs / 2.0 - 1.0))
    q = max(q, 0.05)
    w0 = 2.0 * math.pi * freq / fs
    cw, sw = math.cos(w0), math.sin(w0)
    alpha, a = sw / (2.0 * q), 10.0 ** (gain / 40.0)

    if pw_type == "bq_peaking":
        return (1 + alpha * a, -2 * cw, 1 - alpha * a, 1 + alpha / a, -2 * cw, 1 - alpha / a)
    if pw_type == "bq_lowshelf":
        k = 2.0 * math.sqrt(a) * alpha
        return (
            a * ((a + 1) - (a - 1) * cw + k), 2 * a * ((a - 1) - (a + 1) * cw), a * ((a + 1) - (a - 1) * cw - k),
            (a + 1) + (a - 1) * cw + k, -2 * ((a - 1) + (a + 1) * cw), (a + 1) + (a - 1) * cw - k,
        )
    if pw_type == "bq_highshelf":
        k = 2.0 * math.sqrt(a) * alpha
        return (
            a * ((a + 1) + (a - 1) * cw + k), -2 * a * ((a - 1) + (a + 1) * cw), a * ((a + 1) + (a - 1) * cw - k),
            (a + 1) - (a - 1) * cw + k, 2 * ((a - 1) - (a + 1) * cw), (a + 1) - (a - 1) * cw - k,
        )
    if pw_type == "bq_lowpass":
        return ((1 - cw) / 2, 1 - cw, (1 - cw) / 2, 1 + alpha, -2 * cw, 1 - alpha)
    if pw_type == "bq_highpass":
        return ((1 + cw) / 2, -(1 + cw), (1 + cw) / 2, 1 + alpha, -2 * cw, 1 - alpha)
    if pw_type == "bq_bandpass":
        return (alpha, 0.0, -alpha, 1 + alpha, -2 * cw, 1 - alpha)
    if pw_type == "bq_notch":
        return (1.0, -2 * cw, 1.0, 1 + alpha, -2 * cw, 1 - alpha)
    if pw_type == "bq_allpass":
        return (1 - alpha, -2 * cw, 1 + alpha, 1 + alpha, -2 * cw, 1 - alpha)
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def compute_response_curve(profile: EQProfile, n_points: int = 200, fs: float = 48000.0):
    freqs = [20.0 * ((20000.0 / 20.0) ** (i / (n_points - 1))) for i in range(n_points)]
    raw_db = [0.0] * n_points
    for b in profile.bands:
        if not b.enabled:
            continue
        b0, b1, b2, a0, a1, a2 = _biquad_coeffs(b.pw_type, b.freq, b.gain, b.q, fs)
        for i, f in enumerate(freqs):
            z1 = cmath.exp(-1j * (2.0 * math.pi * f / fs))
            z2 = z1 * z1
            den = a0 + a1 * z1 + a2 * z2
            mag = abs((b0 + b1 * z1 + b2 * z2) / den) if abs(den) > 1e-12 else 1.0
            raw_db[i] += 20.0 * math.log10(max(mag, 1e-12))
    return freqs, raw_db, [v + profile.preamp for v in raw_db]


def recommended_preamp(profile: EQProfile) -> float:
    _, raw_db, _ = compute_response_curve(profile, n_points=300)
    return round(-max(0.0, max(raw_db) if raw_db else 0.0), 2)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def generate_pipewire_conf(profile: EQProfile, target_node: str = "", smart_filter: bool = True) -> str:
    fl = []
    if abs(profile.preamp) > 1e-4:
        fl.append(
            f"                                {{ type = bq_highshelf, freq = 0, gain = {_fmt(profile.preamp)}, q = 1.0 }}"
        )
    for b in profile.bands:
        if not b.enabled:
            continue
        if b.pw_type in NO_GAIN_TYPES:
            fl.append(f"                                {{ type = {b.pw_type}, freq = {_fmt(b.freq)}, q = {_fmt(b.q)} }}")
        else:
            fl.append(
                f"                                {{ type = {b.pw_type}, freq = {_fmt(b.freq)}, gain = {_fmt(b.gain)}, q = {_fmt(b.q)} }}"
            )

    desc = _esc(f"Parametric EQ ({profile.name})")
    if smart_filter:
        smart = '                filter.smart      = true\n                filter.smart.name = "pw_eq"\n'
        if target_node:
            smart += f'                filter.smart.target = {{ node.name = "{_esc(target_node)}" }}\n'
        tgt = ""
    else:
        smart = ""
        tgt = f'                target.object = "{_esc(target_node)}"\n' if target_node else ""

    return f"""# Auto-generated by pw-eq
context.properties = {{ log.level = 0 }}
context.spa-libs = {{
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}}
context.modules = [
    {{ name = libpipewire-module-rt args = {{ nice.level = -11 }} flags = [ ifexists nofail ] }}
    {{ name = libpipewire-module-protocol-native }}
    {{ name = libpipewire-module-client-node }}
    {{ name = libpipewire-module-adapter }}
    {{ name = libpipewire-module-filter-chain
        args = {{
            node.description = "{desc}"
            media.name       = "{desc}"
            filter.graph = {{
                nodes = [
                    {{
                        type = builtin name = eq label = param_eq
                        config = {{
                            filters = [
{chr(10).join(fl)}
                            ]
                        }}
                    }}
                ]
                links = []
            }}
            audio.channels = 2
            audio.position = [ FL FR ]
            capture.props = {{
                node.name    = "{EQ_INPUT_NODE}"
                node.virtual = true
                media.class  = Audio/Sink
{smart}            }}
            playback.props = {{
                node.name    = "{EQ_OUTPUT_NODE}"
                node.passive = true
{tgt}            }}
        }}
    }}
]
"""


def load_state() -> dict:
    ensure_dirs()
    d = {"active_profile": "", "target_node": "", "smart_filter": True, "prev_default_sink": "", "prev_configured_sink": ""}
    if STATE_FILE.exists():
        try:
            d.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return d


def save_state(state: dict) -> None:
    ensure_dirs()
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def list_profiles() -> List[str]:
    ensure_dirs()
    return sorted(p.stem for p in PROFILES_DIR.glob("*.txt"))


def get_profile_path(name: str) -> Path:
    return PROFILES_DIR / f"{name.replace('/', '_').strip()}.txt"


def load_profile(name: str) -> EQProfile:
    return parse_eq_text(get_profile_path(name).read_text(encoding="utf-8"), name=name)


def save_profile_text(name: str, text: str) -> Path:
    ensure_dirs()
    p = get_profile_path(name)
    p.write_text(text, encoding="utf-8")
    return p


def delete_profile(name: str) -> None:
    p = get_profile_path(name)
    if p.exists():
        p.unlink()


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def get_pw_sinks_and_defaults() -> Tuple[List[dict], str, str]:
    res = _run(["pw-dump"])
    if res.returncode != 0 or not res.stdout.strip():
        return [], "", ""
    try:
        objs = json.loads(res.stdout)
    except Exception:
        return [], "", ""
    sinks, default_sink, configured_sink = [], "", ""
    for obj in objs:
        t = obj.get("type")
        if t == "PipeWire:Interface:Node":
            props = obj.get("info", {}).get("props", {})
            if props.get("media.class") == "Audio/Sink":
                nn = props.get("node.name", "")
                sinks.append({"id": obj.get("id"), "name": nn, "description": props.get("node.description") or props.get("node.nick") or nn})
        elif t == "PipeWire:Interface:Metadata" and obj.get("props", {}).get("metadata.name") == "default":
            for e in obj.get("metadata", []):
                val = e.get("value")
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except Exception:
                        val = {}
                if isinstance(val, dict):
                    if e.get("key") == "default.audio.sink":
                        default_sink = val.get("name", "")
                    elif e.get("key") == "default.configured.audio.sink":
                        configured_sink = val.get("name", "")
    return sinks, default_sink, configured_sink


def _restore_hardware_default(state: dict) -> None:
    sinks, cur_default, _ = get_pw_sinks_and_defaults()
    if cur_default and not is_eq_node(cur_default):
        return
    for cand in (state.get("prev_configured_sink", ""), state.get("prev_default_sink", "")):
        if cand and not is_eq_node(cand):
            for s in sinks:
                if s["name"] == cand:
                    _run(["wpctl", "set-default", str(s["id"])])
                    return
    _run(["wpctl", "clear-default"])


def is_eq_running() -> bool:
    r = _run(["systemctl", "--user", "is-active", SERVICE_NAME])
    return r.returncode == 0 and r.stdout.strip() == "active"


def ensure_systemd_unit() -> None:
    SERVICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    unit = """[Unit]
Description=PipeWire Native Parametric EQ (pw-eq)
After=pipewire.service wireplumber.service
Wants=pipewire.service wireplumber.service
PartOf=pipewire.service

[Service]
Type=simple
ExecStart=/usr/bin/pipewire -c %h/.config/pw-eq/active.conf
Restart=on-failure
RestartSec=1

[Install]
WantedBy=pipewire.service
"""
    if not SERVICE_FILE.exists() or SERVICE_FILE.read_text(encoding="utf-8") != unit:
        SERVICE_FILE.write_text(unit, encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"])


def apply_profile(
    profile_name: str,
    target_node: Optional[str] = None,
    smart_filter: Optional[bool] = None,
    enable_on_boot: bool = True,
) -> Tuple[bool, str]:
    ensure_dirs()
    ensure_systemd_unit()
    state = load_state()

    if target_node is not None:
        state["target_node"] = target_node
    if smart_filter is not None:
        state["smart_filter"] = smart_filter
    use_smart = state.get("smart_filter", True)

    prof = load_profile(profile_name)
    if not prof.bands and abs(prof.preamp) < 1e-4:
        return False, f"Profile '{profile_name}' has no active EQ bands."

    _, cur_def, cur_cfg = get_pw_sinks_and_defaults()
    if cur_def and not is_eq_node(cur_def):
        state["prev_default_sink"] = cur_def
    state["prev_configured_sink"] = cur_cfg if (cur_cfg and not is_eq_node(cur_cfg)) else ""

    ACTIVE_CONF.write_text(generate_pipewire_conf(prof, state.get("target_node", ""), use_smart), encoding="utf-8")
    state["active_profile"] = profile_name
    save_state(state)

    if use_smart:
        _restore_hardware_default(state)
    if enable_on_boot:
        _run(["systemctl", "--user", "enable", SERVICE_NAME])

    res = _run(["systemctl", "--user", "restart", SERVICE_NAME])
    if res.returncode != 0:
        return False, f"Failed to start {SERVICE_NAME}: {res.stderr.strip()}"

    eq_id = None
    for _ in range(50):
        sinks_now, _, _ = get_pw_sinks_and_defaults()
        for s in sinks_now:
            if s["name"] == EQ_INPUT_NODE:
                eq_id = s["id"]
                break
        if eq_id is not None:
            break
        time.sleep(0.05)

    if eq_id is not None:
        _run(["wpctl", "set-mute", str(eq_id), "0"])
        _run(["wpctl", "set-volume", str(eq_id), "1.0"])
        if not use_smart:
            _run(["wpctl", "set-default", str(eq_id)])

    return True, f"Applied '{profile_name}' ({sum(1 for b in prof.bands if b.enabled)} active bands)"


def stop_eq(disable_on_boot: bool = True) -> Tuple[bool, str]:
    state = load_state()
    _run(["systemctl", "--user", "stop", SERVICE_NAME])
    if disable_on_boot:
        _run(["systemctl", "--user", "disable", SERVICE_NAME])
    _restore_hardware_default(state)
    return True, "Parametric EQ disabled (bypassed)."


def set_autostart_tray(enabled: bool) -> None:
    AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
    if enabled:
        gui_cmd = shutil.which("pw-eq-gui") or str(Path.home() / ".local" / "bin" / "pw-eq-gui")
        AUTOSTART_FILE.write_text(
            f"""[Desktop Entry]
Type=Application
Name=PipeWire Parametric EQ (Tray)
Comment=System tray controller for PipeWire Parametric EQ
Exec={gui_cmd} --tray
Icon=audio-card
Terminal=false
Categories=AudioVideo;Audio;
X-GNOME-Autostart-enabled=true
""",
            encoding="utf-8",
        )
    elif AUTOSTART_FILE.exists():
        AUTOSTART_FILE.unlink()
