#!/usr/bin/env python3
from __future__ import annotations

import cmath
import json
import math
import re
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

EQ_INPUT_NODE = "effect_input.pw_eq_smart"
LEGACY_EQ_INPUT_NODE = "effect_input.pw_eq"
EQ_OUTPUT_NODE = "effect_output.pw_eq"

FILTER_MAP = {
    "PK": "bq_peaking", "PEQ": "bq_peaking",
    "LP": "bq_lowpass", "LPQ": "bq_lowpass",
    "HP": "bq_highpass", "HPQ": "bq_highpass",
    "LS": "bq_lowshelf", "LSC": "bq_lowshelf",
    "HS": "bq_highshelf", "HSC": "bq_highshelf",
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


def sanitize_profile_name(name: str) -> str:
    safe = re.sub(r"[\\/\x00]", "_", name.strip())
    if safe.lower().endswith(".txt"):
        safe = safe[:-4].strip()
    if not safe or safe in (".", ".."):
        raise ValueError("Invalid profile name.")
    return safe


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
    preamp_re = re.compile(r"^\s*Preamp\s*:\s*([+-]?\d+(?:[.,]\d+)?)\s*(?:dB)?\b", re.I)
    filter_re = re.compile(r"^\s*Filter(?:\s+\d+)?\s*:\s*(ON|OFF)\s+([A-Za-z0-9_]+)\s*(.*)$", re.I)
    fc_re = re.compile(r"\bFc\s+([+-]?\d+(?:[.,]\d+)?)\s*(?:Hz)?\b", re.I)
    gain_re = re.compile(r"\bGain\s+([+-]?\d+(?:[.,]\d+)?)\s*(?:dB)?\b", re.I)
    q_re = re.compile(r"\bQ\s+([+-]?\d+(?:[.,]\d+)?)\b", re.I)
    bw_re = re.compile(r"\bBW\s*(?:Oct)?\s+([+-]?\d+(?:[.,]\d+)?)\b", re.I)
    s_re = re.compile(r"\bS\s+([+-]?\d+(?:[.,]\d+)?)\b", re.I)

    for idx, line in enumerate(text.splitlines()):
        st = line.strip()
        if not st or st.startswith(("#", ";")):
            continue
        if pm := preamp_re.match(st):
            profile.preamp = _num(pm.group(1))
            continue
        if fm := filter_re.match(st):
            enabled, raw_type = fm.group(1).upper() == "ON", fm.group(2).upper()
            if raw_type == "NONE":
                continue
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
            if freq <= 0:
                profile.warnings.append(f"Line {idx + 1}: Invalid frequency {freq} Hz")
                continue
            gain = _num(gm.group(1)) if (gm := gain_re.search(rest)) else 0.0
            if qm := q_re.search(rest):
                q = _num(qm.group(1))
            elif bm := bw_re.search(rest):
                q = _bw_to_q(_num(bm.group(1)))
            elif (sm := s_re.search(rest)) and pw_type in ("bq_lowshelf", "bq_highshelf"):
                q = _slope_to_q(_num(sm.group(1)), gain)
            else:
                q = 0.7071 if pw_type in NO_GAIN_TYPES or pw_type in ("bq_lowshelf", "bq_highshelf") else 1.0
            profile.bands.append(EQBand(enabled, raw_type, pw_type, freq, gain, max(q, 0.05)))
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
    a = 10.0 ** (gain / 40.0)
    w0 = 2.0 * math.pi * max(1.0, min(freq, fs * 0.49)) / fs
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2.0 * max(q, 0.05))
    if pw_type == "bq_peaking":
        b = (1.0 + alpha * a, -2.0 * cw, 1.0 - alpha * a)
        a_ = (1.0 + alpha / a, -2.0 * cw, 1.0 - alpha / a)
    elif pw_type == "bq_lowshelf":
        sq = 2.0 * math.sqrt(a) * alpha
        b = (a * ((a + 1) - (a - 1) * cw + sq), 2 * a * ((a - 1) - (a + 1) * cw), a * ((a + 1) - (a - 1) * cw - sq))
        a_ = ((a + 1) + (a - 1) * cw + sq, -2 * ((a - 1) + (a + 1) * cw), (a + 1) + (a - 1) * cw - sq)
    elif pw_type == "bq_highshelf":
        sq = 2.0 * math.sqrt(a) * alpha
        b = (a * ((a + 1) + (a - 1) * cw + sq), -2 * a * ((a - 1) + (a + 1) * cw), a * ((a + 1) + (a - 1) * cw - sq))
        a_ = ((a + 1) - (a - 1) * cw + sq, 2 * ((a - 1) - (a + 1) * cw), (a + 1) - (a - 1) * cw - sq)
    elif pw_type == "bq_lowpass":
        b, a_ = ((1 - cw) / 2, 1 - cw, (1 - cw) / 2), (1 + alpha, -2 * cw, 1 - alpha)
    elif pw_type == "bq_highpass":
        b, a_ = ((1 + cw) / 2, -(1 + cw), (1 + cw) / 2), (1 + alpha, -2 * cw, 1 - alpha)
    elif pw_type == "bq_bandpass":
        b, a_ = (alpha, 0.0, -alpha), (1 + alpha, -2 * cw, 1 - alpha)
    elif pw_type == "bq_notch":
        b, a_ = (1.0, -2.0 * cw, 1.0), (1 + alpha, -2 * cw, 1 - alpha)
    elif pw_type == "bq_allpass":
        b, a_ = (1 - alpha, -2 * cw, 1 + alpha), (1 + alpha, -2 * cw, 1 - alpha)
    else:
        return (1.0, 0.0, 0.0), (1.0, 0.0, 0.0)
    return b, a_


def compute_response_curve(profile: EQProfile, n_points: int = 200, fs: float = 48000.0):
    n_points = max(2, n_points)
    freqs = [20.0 * (1000.0 ** (i / (n_points - 1))) for i in range(n_points)]
    coeffs = [_biquad_coeffs(b.pw_type, b.freq, b.gain, b.q, fs) for b in profile.bands if b.enabled]
    total = []
    for f in freqs:
        z1 = cmath.exp(-1j * 2.0 * math.pi * f / fs)
        z2 = z1 * z1
        h = 1.0 + 0j
        for (b0, b1, b2), (a0, a1, a2) in coeffs:
            h *= (b0 + b1 * z1 + b2 * z2) / (a0 + a1 * z1 + a2 * z2)
        total.append(profile.preamp + 20.0 * math.log10(max(abs(h), 1e-12)))
    return freqs, total


def recommended_preamp(profile: EQProfile) -> float:
    _, total = compute_response_curve(profile)
    peak = max(total) if total else 0.0
    boost = max(0.0, peak - profile.preamp)
    val = -math.ceil(boost * 10.0) / 10.0
    return 0.0 if val == 0 else val


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def generate_pipewire_conf(profile: EQProfile, target_node: str = "", smart_filter: bool = True) -> str:
    active = [b for b in profile.bands if b.enabled]
    nodes = [
        f'{{ type = builtin name = preamp label = bq_highshelf control = {{ "Freq" = 0.0 "Q" = 1.0 "Gain" = {_fmt(profile.preamp)} }} }}'
    ]
    for i, b in enumerate(active, 1):
        if b.pw_type in NO_GAIN_TYPES:
            ctrl = f'"Freq" = {_fmt(b.freq)} "Q" = {_fmt(b.q)}'
        else:
            ctrl = f'"Freq" = {_fmt(b.freq)} "Q" = {_fmt(b.q)} "Gain" = {_fmt(b.gain)}'
        nodes.append(f'{{ type = builtin name = eq_band_{i} label = {b.pw_type} control = {{ {ctrl} }} }}')

    links = [
        f'{{ output = "{nodes[i].split("name = ")[1].split()[0]}:Out" input = "{nodes[i + 1].split("name = ")[1].split()[0]}:In" }}'
        for i in range(len(nodes) - 1)
    ]

    if smart_filter:
        in_props = f'node.name = "{EQ_INPUT_NODE}"\n                    media.class = Audio/Sink'
        out_props = (
            f'node.name = "{EQ_OUTPUT_NODE}"\n'
            f'                    media.class = Stream/Output/Audio\n'
            f'                    node.passive = true'
        )
        mode_comment = "Smart auto-follow active default sink (device.routes / hotplug aware)"
    else:
        in_props = f'node.name = "{EQ_INPUT_NODE}"\n                    media.class = Audio/Sink'
        out_props = f'node.name = "{EQ_OUTPUT_NODE}"\n                    node.passive = true'
        if target_node:
            out_props += f'\n                    target.object = "{_esc(target_node)}"'
        mode_comment = f"Explicit target: {target_node or 'auto'}"

    nodes_block = "\n                    ".join(nodes)
    links_block = "\n                    ".join(links)
    return f"""# Generated by pw-eq | Profile: {profile.name} | {mode_comment}
context.modules = [
    {{
        name = libpipewire-module-filter-chain
        args = {{
            node.description = "Parametric EQ ({_esc(profile.name)})"
            media.name = "Parametric EQ ({_esc(profile.name)})"
            filter.graph = {{
                nodes = [
                    {nodes_block}
                ]
                links = [
                    {links_block}
                ]
            }}
            audio.channels = 2
            audio.position = [ FL FR ]
            capture.props = {{
                    {in_props}
            }}
            playback.props = {{
                    {out_props}
            }}
        }}
    }}
]
"""


def load_state() -> dict:
    ensure_dirs()
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"active_profile": "", "target_node": "", "last_hw_sink": "", "smart_filter": True}


def save_state(state: dict) -> None:
    ensure_dirs()
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def list_profiles() -> List[str]:
    ensure_dirs()
    return sorted(p.stem for p in PROFILES_DIR.glob("*.txt") if p.is_file())


def get_profile_path(name: str) -> Path:
    return PROFILES_DIR / f"{sanitize_profile_name(name)}.txt"


def load_profile(name: str) -> EQProfile:
    p = get_profile_path(name)
    return parse_eq_text(p.read_text(encoding="utf-8"), name=p.stem)


def save_profile_text(name: str, text: str) -> Path:
    ensure_dirs()
    p = get_profile_path(name)
    p.write_text(text.rstrip() + "\n", encoding="utf-8")
    return p


def delete_profile(name: str) -> None:
    p = get_profile_path(name)
    if p.exists():
        p.unlink()
    st = load_state()
    if st.get("active_profile") == p.stem:
        st["active_profile"] = ""
        save_state(st)


def _run(cmd: List[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"Command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"Command timed out: {' '.join(cmd)}")


def get_pw_sinks_and_defaults() -> Tuple[List[dict], str, str]:
    res = _run(["pw-dump"])
    if res.returncode != 0 or not res.stdout.strip():
        return [], "", ""
    try:
        objs = json.loads(res.stdout)
    except json.JSONDecodeError:
        return [], "", ""
    default_sink = ""
    sinks = []
    for o in objs:
        otype = o.get("type", "")
        if otype == "PipeWire:Interface:Metadata":
            for m in o.get("metadata", []):
                if m.get("key") == "default.audio.sink":
                    val = m.get("value")
                    default_sink = val.get("name", "") if isinstance(val, dict) else str(val or "")
        elif otype == "PipeWire:Interface:Node":
            props = o.get("info", {}).get("props", {})
            if props.get("media.class") == "Audio/Sink":
                sinks.append({
                    "id": o.get("id"),
                    "name": props.get("node.name", ""),
                    "desc": props.get("node.description") or props.get("node.nick") or props.get("node.name", ""),
                })
    return sinks, default_sink, ""


def _restore_hardware_default(state: dict) -> None:
    sinks, default_sink, _ = get_pw_sinks_and_defaults()
    hw_name = state.get("target_node") or state.get("last_hw_sink") or ""
    if hw_name and not is_eq_node(hw_name):
        if m := next((s for s in sinks if s["name"] == hw_name), None):
            _run(["wpctl", "set-default", str(m["id"])])
            return
    if is_eq_node(default_sink):
        if m := next((s for s in sinks if not is_eq_node(s["name"])), None):
            _run(["wpctl", "set-default", str(m["id"])])


def is_eq_running() -> bool:
    return _run(["systemctl", "--user", "is-active", SERVICE_NAME]).returncode == 0


def ensure_systemd_unit() -> None:
    SERVICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    desired = f"""[Unit]
Description=PipeWire Parametric EQ
After=pipewire.service pipewire-pulse.service wireplumber.service
Wants=pipewire.service wireplumber.service

[Service]
Type=simple
ExecStart=/usr/bin/pipewire -c "{ACTIVE_CONF}"
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""
    changed = not SERVICE_FILE.exists() or SERVICE_FILE.read_text(encoding="utf-8") != desired
    if changed:
        SERVICE_FILE.write_text(desired, encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"])


def apply_profile(
    profile_name: str,
    target_node: Optional[str] = None,
    smart_filter: Optional[bool] = None,
    enable_on_boot: bool = True,
) -> Tuple[bool, str]:
    ensure_dirs()
    profile_path = get_profile_path(profile_name)
    if not profile_path.exists():
        return False, f"Profile '{profile_name}' not found."

    ensure_systemd_unit()
    state = load_state()

    if target_node is not None:
        state["target_node"] = target_node
    if smart_filter is not None:
        state["smart_filter"] = smart_filter
    use_smart = state.get("smart_filter", True)

    _, default_sink, _ = get_pw_sinks_and_defaults()
    if default_sink and not is_eq_node(default_sink):
        state["last_hw_sink"] = default_sink

    hw_target = state.get("target_node") or ""
    if not use_smart and not hw_target:
        hw_target = default_sink if (default_sink and not is_eq_node(default_sink)) else state.get("last_hw_sink", "")

    profile = load_profile(profile_path.stem)
    ACTIVE_CONF.write_text(
        generate_pipewire_conf(profile, target_node=hw_target, smart_filter=use_smart),
        encoding="utf-8",
    )
    state["active_profile"] = profile.name
    save_state(state)

    res = _run(["systemctl", "--user", "restart", SERVICE_NAME])
    if res.returncode != 0:
        return False, f"Service failed: {res.stderr.strip()}"

    if use_smart:
        _restore_hardware_default(state)
    if enable_on_boot:
        _run(["systemctl", "--user", "enable", SERVICE_NAME])

    eq_id = None
    for _ in range(25):
        sinks, _, _ = get_pw_sinks_and_defaults()
        if m := next((s for s in sinks if is_eq_node(s["name"])), None):
            eq_id = m["id"]
            break
        time.sleep(0.12)
    if eq_id is None:
        return False, "EQ started, but PipeWire sink did not appear."
    _run(["wpctl", "set-default", str(eq_id)])
    return True, f"Applied '{profile.name}' -> PipeWire EQ active."


def stop_eq(disable_on_boot: bool = True) -> Tuple[bool, str]:
    state = load_state()
    _run(["systemctl", "--user", "stop", SERVICE_NAME])
    if disable_on_boot:
        _run(["systemctl", "--user", "disable", SERVICE_NAME])
    _restore_hardware_default(state)
    return True, "Parametric EQ disabled (bypassed)."
