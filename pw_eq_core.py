#!/usr/bin/env python3
from __future__ import annotations

import cmath
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


def xdg_config_home() -> Path:
    val = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return Path(val) if val else Path.home() / ".config"


CONFIG_DIR = xdg_config_home() / "pw-eq"
PROFILES_DIR = CONFIG_DIR / "profiles"
ACTIVE_CONF = CONFIG_DIR / "active.conf"
STATE_FILE = CONFIG_DIR / "state.json"
SERVICE_NAME = "pw-eq.service"
SERVICE_FILE = xdg_config_home() / "systemd" / "user" / SERVICE_NAME

EQ_SMART_NODE = "effect_input.pw_eq_smart"
EQ_LEGACY_NODE = "effect_input.pw_eq"
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

_NUM_PAT = r"([+-]?\d+(?:,\d{3})*(?:[.,]\d+)?)"
_PREAMP_RE = re.compile(rf"^\s*Preamp\s*:\s*{_NUM_PAT}\s*(?:dB)?\b", re.I)
_FILTER_RE = re.compile(r"^\s*Filter(?:\s+\d+)?\s*:\s*(ON|OFF)\s+([A-Za-z0-9_]+)\s*(.*)$", re.I)
_FC_RE = re.compile(rf"\bFc\s+{_NUM_PAT}\s*(?:Hz)?\b", re.I)
_GAIN_RE = re.compile(rf"\bGain\s+{_NUM_PAT}\s*(?:dB)?\b", re.I)
_Q_RE = re.compile(rf"\bQ\s+{_NUM_PAT}\b", re.I)
_BW_RE = re.compile(rf"\bBW\s*(?:Oct)?\s+{_NUM_PAT}\b", re.I)
_S_RE = re.compile(rf"\bS\s+{_NUM_PAT}\b", re.I)
_THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


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
    return node_name in (EQ_SMART_NODE, EQ_LEGACY_NODE)


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def sanitize_profile_name(name: str) -> str:
    safe = _CTRL_RE.sub("", name).strip()
    safe = re.sub(r"[\\/]", "_", safe)
    if safe.lower().endswith(".txt"):
        safe = safe[:-4].strip()
    if not safe or safe in (".", ".."):
        raise ValueError("Invalid profile name.")
    return safe[:200]


def _num(s: str) -> float:
    s = s.strip()
    val = float(s.replace(",", "") if _THOUSANDS_RE.match(s) else s.replace(",", "."))
    if not math.isfinite(val):
        raise ValueError(f"Non-finite number: {s}")
    return val


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
    for idx, line in enumerate(text.splitlines()):
        st = line.strip()
        if not st or st.startswith(("#", ";")):
            continue
        if pm := _PREAMP_RE.match(st):
            try:
                profile.preamp = _num(pm.group(1))
            except ValueError:
                profile.warnings.append(f"Line {idx + 1}: Invalid Preamp")
            continue
        if fm := _FILTER_RE.match(st):
            enabled, raw_type = fm.group(1).upper() == "ON", fm.group(2).upper()
            if raw_type == "NONE":
                continue
            rest = re.sub(r"^\s*\d+\s*dB\b", "", fm.group(3), flags=re.I)
            pw_type = FILTER_MAP.get(raw_type)
            if not pw_type:
                profile.warnings.append(f"Line {idx + 1}: Unsupported '{raw_type}'")
                continue
            fc_m = _FC_RE.search(rest)
            if not fc_m:
                profile.warnings.append(f"Line {idx + 1}: Missing Fc")
                continue
            try:
                freq = _num(fc_m.group(1))
            except ValueError:
                profile.warnings.append(f"Line {idx + 1}: Invalid Fc")
                continue
            if freq <= 0:
                profile.warnings.append(f"Line {idx + 1}: Invalid frequency {freq} Hz")
                continue
            try:
                gain = _num(gm.group(1)) if (gm := _GAIN_RE.search(rest)) else 0.0
                if qm := _Q_RE.search(rest):
                    q = _num(qm.group(1))
                elif bm := _BW_RE.search(rest):
                    q = _bw_to_q(_num(bm.group(1)))
                elif (sm := _S_RE.search(rest)) and pw_type in ("bq_lowshelf", "bq_highshelf"):
                    q = _slope_to_q(_num(sm.group(1)), gain)
                else:
                    q = 0.7071 if pw_type in NO_GAIN_TYPES or pw_type in ("bq_lowshelf", "bq_highshelf") else 1.0
            except ValueError:
                profile.warnings.append(f"Line {idx + 1}: Invalid filter parameter")
                continue
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


def compute_response_curve(profile: EQProfile, n_points: int = 240, fs: float = 48000.0):
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
    _, total = compute_response_curve(profile, n_points=400)
    # Include exact active band center frequencies to catch narrow high-Q peaks
    coeffs = [_biquad_coeffs(b.pw_type, b.freq, b.gain, b.q) for b in profile.bands if b.enabled]
    peak = max(total) if total else profile.preamp
    for b in profile.bands:
        if b.enabled and 20.0 <= b.freq <= 20000.0:
            z1 = cmath.exp(-1j * 2.0 * math.pi * b.freq / 48000.0)
            z2 = z1 * z1
            h = 1.0 + 0j
            for (b0, b1, b2), (a0, a1, a2) in coeffs:
                h *= (b0 + b1 * z1 + b2 * z2) / (a0 + a1 * z1 + a2 * z2)
            peak = max(peak, profile.preamp + 20.0 * math.log10(max(abs(h), 1e-12)))
    boost = max(0.0, peak - profile.preamp)
    val = -math.ceil(boost * 10.0) / 10.0
    return 0.0 if val == 0 else val


def _spa_str(s: str) -> str:
    return _CTRL_RE.sub("", s).replace("\\", "\\\\").replace('"', '\\"')


def generate_pipewire_conf(profile: EQProfile, target_node: str = "", smart_filter: bool = True) -> str:
    filters = []
    if profile.preamp != 0.0 or not any(b.enabled for b in profile.bands):
        filters.append(f"{{ type = bq_highshelf, freq = 0, gain = {_fmt(profile.preamp)}, q = 1.0 }}")
    for b in profile.bands:
        if not b.enabled:
            continue
        if b.pw_type in NO_GAIN_TYPES:
            filters.append(f"{{ type = {b.pw_type}, freq = {_fmt(b.freq)}, q = {_fmt(b.q)} }}")
        else:
            filters.append(f"{{ type = {b.pw_type}, freq = {_fmt(b.freq)}, gain = {_fmt(b.gain)}, q = {_fmt(b.q)} }}")

    if smart_filter:
        capture_lines = [
            f'node.name = "{EQ_SMART_NODE}"',
            "media.class = Audio/Sink",
            "filter.smart = true",
            'filter.smart.name = "pw-eq"',
        ]
        if target_node:
            capture_lines.append(f'filter.smart.target = {{ node.name = "{_spa_str(target_node)}" }}')
        playback_lines = [
            f'node.name = "{EQ_OUTPUT_NODE}"',
            "node.passive = true",
            "node.dont-fallback = true",
        ]
        mode_comment = f"Smart Filter ({target_node or 'auto-follow default'})"
    else:
        capture_lines = [
            f'node.name = "{EQ_LEGACY_NODE}"',
            "media.class = Audio/Sink",
        ]
        playback_lines = [
            f'node.name = "{EQ_OUTPUT_NODE}"',
            "node.passive = true",
        ]
        if target_node:
            playback_lines.append(f'target.object = "{_spa_str(target_node)}"')
        mode_comment = f"Explicit sink ({target_node or 'auto'})"

    safe_name = _spa_str(profile.name)
    filters_block = "\n                        ".join(filters)
    capture_block = "\n                ".join(capture_lines)
    playback_block = "\n                ".join(playback_lines)

    return f"""# Generated by pw-eq | Profile: {safe_name} | {mode_comment}
context.properties = {{
    log.level = 0
}}

context.spa-libs = {{
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}}

context.modules = [
    {{ name = libpipewire-module-rt
        args = {{ nice.level = -11 }}
        flags = [ ifexists nofail ]
    }}
    {{ name = libpipewire-module-protocol-native }}
    {{ name = libpipewire-module-client-node }}
    {{ name = libpipewire-module-adapter }}
    {{ name = libpipewire-module-filter-chain
        args = {{
            node.description = "Parametric EQ ({safe_name})"
            media.name       = "Parametric EQ ({safe_name})"
            filter.graph = {{
                nodes = [
                    {{
                        type   = builtin
                        name   = eq
                        label  = param_eq
                        config = {{
                            filters = [
                                {filters_block}
                            ]
                        }}
                    }}
                ]
                links = []
            }}
            audio.channels = 2
            audio.position = [ FL FR ]
            capture.props = {{
                {capture_block}
            }}
            playback.props = {{
                {playback_block}
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
    _atomic_write(STATE_FILE, json.dumps(state, indent=2) + "\n")


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
    _atomic_write(p, text.rstrip() + "\n")
    return p


def delete_profile(name: str) -> None:
    p = get_profile_path(name)
    if p.exists():
        p.unlink()
    st = load_state()
    if st.get("active_profile") == p.stem:
        if is_eq_running():
            stop_eq()
        st["active_profile"] = ""
        save_state(st)


def _run(cmd: List[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"Command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"Command timed out: {' '.join(cmd)}")


def get_pw_sinks_and_default() -> Tuple[List[dict], str]:
    res = _run(["pw-dump"])
    if res.returncode != 0 or not res.stdout.strip():
        return [], ""
    try:
        objs = json.loads(res.stdout)
    except json.JSONDecodeError:
        return [], ""
    if not isinstance(objs, list):
        return [], ""
    default_sink = ""
    sinks = []
    for o in objs:
        if not isinstance(o, dict):
            continue
        otype = o.get("type", "")
        if otype == "PipeWire:Interface:Metadata":
            for m in o.get("metadata") or []:
                if isinstance(m, dict) and m.get("key") == "default.audio.sink":
                    val = m.get("value")
                    default_sink = val.get("name", "") if isinstance(val, dict) else str(val or "")
        elif otype == "PipeWire:Interface:Node":
            props = (o.get("info") or {}).get("props") or {}
            if props.get("media.class") == "Audio/Sink":
                sinks.append({
                    "id": o.get("id"),
                    "name": props.get("node.name", ""),
                    "desc": props.get("node.description") or props.get("node.nick") or props.get("node.name", ""),
                })
    return sinks, default_sink


def _restore_hardware_default(state: dict) -> None:
    sinks, default_sink = get_pw_sinks_and_default()
    if not is_eq_node(default_sink):
        return
    hw_name = state.get("target_node") or state.get("last_hw_sink") or ""
    if hw_name and not is_eq_node(hw_name):
        if m := next((s for s in sinks if s["name"] == hw_name), None):
            _run(["wpctl", "set-default", str(m["id"])])
            return
    if m := next((s for s in sinks if not is_eq_node(s["name"])), None):
        _run(["wpctl", "set-default", str(m["id"])])


def is_eq_running() -> bool:
    return _run(["systemctl", "--user", "is-active", "--quiet", SERVICE_NAME]).returncode == 0


def _systemd_esc(s: str) -> str:
    return _CTRL_RE.sub("", s).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def ensure_systemd_unit() -> None:
    pw_bin = shutil.which("pipewire")
    if not pw_bin:
        raise FileNotFoundError("pipewire executable not found in PATH.")
    desired = f"""[Unit]
Description=PipeWire Parametric EQ
After=pipewire.service pipewire-pulse.service wireplumber.service
Wants=pipewire.service wireplumber.service
PartOf=pipewire.service

[Service]
Type=simple
ExecStart="{_systemd_esc(pw_bin)}" -c "{_systemd_esc(str(ACTIVE_CONF))}"
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""
    if not SERVICE_FILE.exists() or SERVICE_FILE.read_text(encoding="utf-8") != desired:
        _atomic_write(SERVICE_FILE, desired)
        _run(["systemctl", "--user", "daemon-reload"])


def apply_profile(
    profile_name: str,
    target_node: Optional[str] = None,
    smart_filter: Optional[bool] = None,
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
    use_smart = bool(state.get("smart_filter", True))

    _, default_sink = get_pw_sinks_and_default()
    if default_sink and not is_eq_node(default_sink):
        state["last_hw_sink"] = default_sink

    hw_target = state.get("target_node") or ""
    if not use_smart and not hw_target:
        hw_target = default_sink if (default_sink and not is_eq_node(default_sink)) else state.get("last_hw_sink", "")

    profile = load_profile(profile_path.stem)
    _atomic_write(
        ACTIVE_CONF,
        generate_pipewire_conf(profile, target_node=hw_target, smart_filter=use_smart),
    )
    state["active_profile"] = profile.name
    save_state(state)

    res = _run(["systemctl", "--user", "restart", SERVICE_NAME])
    if res.returncode != 0:
        return False, f"Service failed: {res.stderr.strip() or res.stdout.strip()}"

    _run(["systemctl", "--user", "enable", SERVICE_NAME])

    expected_node = EQ_SMART_NODE if use_smart else EQ_LEGACY_NODE
    eq_id = None
    for _ in range(25):
        sinks, _ = get_pw_sinks_and_default()
        if m := next((s for s in sinks if s["name"] == expected_node), None):
            eq_id = m["id"]
            break
        time.sleep(0.12)
    if eq_id is None:
        return False, "EQ started, but PipeWire filter sink did not appear."

    if use_smart:
        _restore_hardware_default(state)
        return True, f"Applied '{profile.name}' (WirePlumber Smart Filter active)."

    _run(["wpctl", "set-default", str(eq_id)])
    return True, f"Applied '{profile.name}' (Legacy virtual sink active)."


def stop_eq() -> Tuple[bool, str]:
    state = load_state()
    _run(["systemctl", "--user", "stop", SERVICE_NAME])
    _run(["systemctl", "--user", "disable", SERVICE_NAME])
    _restore_hardware_default(state)
    return True, "Parametric EQ disabled (bypassed)."


def cleanup() -> List[str]:
    actions: List[str] = []
    state = load_state() if STATE_FILE.exists() else {}
    _run(["systemctl", "--user", "disable", "--now", SERVICE_NAME])
    _restore_hardware_default(state)
    actions.append(f"Stopped & disabled {SERVICE_NAME}")

    if SERVICE_FILE.exists():
        SERVICE_FILE.unlink()
        actions.append(f"Removed {SERVICE_FILE}")
    _run(["systemctl", "--user", "daemon-reload"])

    if CONFIG_DIR.exists():
        shutil.rmtree(CONFIG_DIR)
        actions.append(f"Removed {CONFIG_DIR}")
    return actions
