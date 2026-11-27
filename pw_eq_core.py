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
from typing import List, Tuple

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
    "LS": "bq_lowshelf", "LSC": "bq_lowshelf", "LSQ": "bq_lowshelf",
    "HS": "bq_highshelf", "HSC": "bq_highshelf", "HSQ": "bq_highshelf",
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
    preamp_re = re.compile(r"^\s*Preamp:\s*([+-]?\d+(?:[.,]\d+)?)\s*dB", re.I)
    filter_re = re.compile(r"^\s*Filter(?:\s+\d+)?:\s*(ON|OFF)\s+([A-Z]+)\b(.*)$", re.I)
    fc_re = re.compile(r"\bFc\s*([+-]?\d+(?:[.,]\d+)?)\s*(?:Hz)?\b", re.I)
    gain_re = re.compile(r"\bGain\s*([+-]?\d+(?:[.,]\d+)?)\s*(?:dB)?\b", re.I)
    q_re = re.compile(r"\bQ\s*([+-]?\d+(?:[.,]\d+)?)\b", re.I)
    bw_re = re.compile(r"\bBW\s*Oct\s*([+-]?\d+(?:[.,]\d+)?)\b", re.I)
    s_re = re.compile(r"\bS\s*([+-]?\d+(?:[.,]\d+)?)\b", re.I)

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
    w0 = 2.0 * math.pi * max(min(freq, fs * 0.495), 1.0) / fs
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2.0 * max(q, 0.05))
    a = 10.0 ** (gain / 40.0)
    if pw_type == "bq_peaking":
        return 1 + alpha * a, -2 * cw, 1 - alpha * a, 1 + alpha / a, -2 * cw, 1 - alpha / a
    if pw_type == "bq_lowshelf":
        s = 2 * math.sqrt(a) * alpha
        return (a * ((a + 1) - (a - 1) * cw + s), 2 * a * ((a - 1) - (a + 1) * cw), a * ((a + 1) - (a - 1) * cw - s),
                (a + 1) + (a - 1) * cw + s, -2 * ((a - 1) + (a + 1) * cw), (a + 1) + (a - 1) * cw - s)
    if pw_type == "bq_highshelf":
        s = 2 * math.sqrt(a) * alpha
        return (a * ((a + 1) + (a - 1) * cw + s), -2 * a * ((a - 1) + (a + 1) * cw), a * ((a + 1) + (a - 1) * cw - s),
                (a + 1) - (a - 1) * cw + s, 2 * ((a - 1) - (a + 1) * cw), (a + 1) - (a - 1) * cw - s)
    if pw_type == "bq_lowpass":
        return (1 - cw) / 2, 1 - cw, (1 - cw) / 2, 1 + alpha, -2 * cw, 1 - alpha
    if pw_type == "bq_highpass":
        return (1 + cw) / 2, -(1 + cw), (1 + cw) / 2, 1 + alpha, -2 * cw, 1 - alpha
    if pw_type == "bq_bandpass":
        return alpha, 0.0, -alpha, 1 + alpha, -2 * cw, 1 - alpha
    if pw_type == "bq_notch":
        return 1.0, -2 * cw, 1.0, 1 + alpha, -2 * cw, 1 - alpha
    if pw_type == "bq_allpass":
        return 1 - alpha, -2 * cw, 1 + alpha, 1 + alpha, -2 * cw, 1 - alpha
    return 1.0, 0.0, 0.0, 1.0, 0.0, 0.0


def compute_response_curve(profile: EQProfile, n_points: int = 200, fs: float = 48000.0):
    freqs = [20.0 * (1000.0 ** (i / (n_points - 1))) for i in range(n_points)]
    coeffs = [_biquad_coeffs(b.pw_type, b.freq, b.gain, b.q, fs) for b in profile.bands if b.enabled]
    mags = []
    for f in freqs:
        z1 = cmath.exp(-1j * 2.0 * math.pi * f / fs)
        z2 = z1 * z1
        h = 1.0 + 0j
        for b0, b1, b2, a0, a1, a2 in coeffs:
            h *= (b0 + b1 * z1 + b2 * z2) / (a0 + a1 * z1 + a2 * z2)
        mags.append(profile.preamp + 20.0 * math.log10(max(abs(h), 1e-9)))
    return freqs, mags


def recommended_preamp(profile: EQProfile) -> float:
    _, mags = compute_response_curve(profile, n_points=300)
    peak_without_preamp = max(m - profile.preamp for m in mags) if mags else 0.0
    return round(min(0.0, -peak_without_preamp - 0.1), 2)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def generate_pipewire_conf(profile: EQProfile, target_node: str = "", smart_filter: bool = True) -> str:
    active = [b for b in profile.bands if b.enabled]
    lines = [
        f"# Auto-generated by pw-eq | Profile: {profile.name}",
        "context.modules = [",
        "  { name = libpipewire-module-filter-chain",
        "    args = {",
        f'      node.description = "Parametric EQ ({_esc(profile.name)})"',
        '      media.name = "Parametric EQ"',
        "      filter.graph = {",
        "        nodes = [",
        "          {",
        "            type = builtin",
        "            name = eq_preamp",
        "            label = bq_highshelf",
        f"            control = {{ Freq = 0.0 Q = 1.0 Gain = {_fmt(profile.preamp)} }}",
        "          }",
    ]
    for idx, b in enumerate(active, 1):
        gain = 0.0 if b.pw_type in NO_GAIN_TYPES else b.gain
        lines += [
            "          {",
            "            type = builtin",
            f"            name = eq_band_{idx}",
            f"            label = {b.pw_type}",
            f"            control = {{ Freq = {_fmt(b.freq)} Q = {_fmt(b.q)} Gain = {_fmt(gain)} }}",
            "          }",
        ]
    lines += ["        ]", "        links = ["]
    prev = "eq_preamp"
    for idx in range(1, len(active) + 1):
        curr = f"eq_band_{idx}"
        lines.append(f'          {{ output = "{prev}:Out" input = "{curr}:In" }}')
        prev = curr
    lines += [
        "        ]",
        '        inputs = [ "eq_preamp:In" ]',
        f'        outputs = [ "{prev}:Out" ]',
        "      }",
        "      audio.channels = 2",
        "      audio.position = [ FL FR ]",
        "      capture.props = {",
        f'        node.name = "{EQ_INPUT_NODE}"',
        f'        node.description = "Parametric EQ ({_esc(profile.name)})"',
        '        media.class = "Audio/Sink"',
        "        priority.driver = 1000",
        "        priority.session = 1000",
        "      }",
        "      playback.props = {",
        f'        node.name = "{EQ_OUTPUT_NODE}"',
        "        node.passive = true",
    ]
    if not smart_filter and target_node:
        lines.append(f'        target.object = "{_esc(target_node)}"')
    lines += ["      }", "    }", "  }", "]", ""]
    return "\n".join(lines)


def load_state() -> dict:
    ensure_dirs()
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"active_profile": "", "target_node": "", "smart_filter": True}


def save_state(state: dict) -> None:
    ensure_dirs()
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def list_profiles() -> List[str]:
    ensure_dirs()
    return sorted(p.stem for p in PROFILES_DIR.glob("*.txt"))


def get_profile_path(name: str) -> Path:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "Untitled"
    return PROFILES_DIR / f"{safe}.txt"


def load_profile(name: str) -> EQProfile:
    return parse_eq_text(get_profile_path(name).read_text(encoding="utf-8"), name=name)


def save_profile_text(name: str, text: str) -> Path:
    ensure_dirs()
    p = get_profile_path(name)
    p.write_text(text.rstrip() + "\n", encoding="utf-8")
    return p


def delete_profile(name: str) -> None:
    p = get_profile_path(name)
    if p.exists():
        p.unlink()


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def get_pw_sinks_and_defaults() -> Tuple[List[dict], str, str]:
    sinks: List[dict] = []
    default_sink = ""
    configured_sink = ""
    res = _run(["pw-dump"])
    if res.returncode == 0 and res.stdout:
        try:
            for obj in json.loads(res.stdout):
                if obj.get("type") == "PipeWire:Interface:Metadata":
                    for item in obj.get("metadata", []):
                        k, v = item.get("key"), item.get("value")
                        if isinstance(v, dict) and "name" in v:
                            if k == "default.audio.sink":
                                default_sink = v["name"]
                            elif k == "default.configured.audio.sink":
                                configured_sink = v["name"]
                elif obj.get("type") == "PipeWire:Interface:Node":
                    props = obj.get("info", {}).get("props", {})
                    if props.get("media.class") == "Audio/Sink":
                        name = props.get("node.name", "")
                        if name:
                            sinks.append({"id": obj.get("id"), "name": name, "desc": props.get("node.description") or name})
        except Exception:
            pass
    return sinks, default_sink, configured_sink


def _restore_hardware_default(state: dict) -> None:
    sinks, default_sink, configured_sink = get_pw_sinks_and_defaults()
    hw_sinks = [s for s in sinks if not is_eq_node(s["name"])]
    target = state.get("target_node") or ""
    if not any(s["name"] == target for s in hw_sinks):
        target = hw_sinks[0]["name"] if hw_sinks else ""
    if target and (is_eq_node(default_sink) or is_eq_node(configured_sink) or not default_sink):
        match = next((s for s in hw_sinks if s["name"] == target), None)
        if match and match.get("id") is not None:
            _run(["wpctl", "set-default", str(match["id"])])
            _run(["pactl", "set-default-sink", target])


def is_eq_running() -> bool:
    return _run(["systemctl", "--user", "is-active", "--quiet", SERVICE_NAME]).returncode == 0


def ensure_systemd_unit() -> None:
    SERVICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    unit = f"""[Unit]
Description=PipeWire Parametric Equalizer (pw-eq)
After=pipewire.service wireplumber.service
Wants=pipewire.service

[Service]
Type=simple
ExecStart=/usr/bin/pipewire -c %h/.config/pw-eq/active.conf
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""
    if not SERVICE_FILE.exists() or SERVICE_FILE.read_text(encoding="utf-8") != unit:
        SERVICE_FILE.write_text(unit, encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"])


def apply_profile(profile_name: str) -> Tuple[bool, str]:
    ensure_dirs()
    ensure_systemd_unit()
    state = load_state()
    use_smart = state.get("smart_filter", True)

    sinks, default_sink, configured_sink = get_pw_sinks_and_defaults()
    hw_sinks = [s for s in sinks if not is_eq_node(s["name"])]
    hw_names = {s["name"] for s in hw_sinks}

    saved_target = state.get("target_node", "")
    if saved_target not in hw_names:
        if default_sink in hw_names:
            saved_target = default_sink
        elif configured_sink in hw_names:
            saved_target = configured_sink
        elif hw_sinks:
            saved_target = hw_sinks[0]["name"]
        else:
            saved_target = ""
        state["target_node"] = saved_target

    profile = load_profile(profile_name)
    ACTIVE_CONF.write_text(generate_pipewire_conf(profile, saved_target, use_smart), encoding="utf-8")
    state["active_profile"] = profile_name
    save_state(state)

    if use_smart:
        _restore_hardware_default(state)
    _run(["systemctl", "--user", "enable", SERVICE_NAME])

    res = _run(["systemctl", "--user", "restart", SERVICE_NAME])
    if res.returncode != 0:
        return False, f"systemd error: {res.stderr.strip() or res.stdout.strip()}"

    if not use_smart:
        for _ in range(25):
            cur_sinks, _, _ = get_pw_sinks_and_defaults()
            eq_sink = next((s for s in cur_sinks if s["name"] == EQ_INPUT_NODE), None)
            if eq_sink and eq_sink.get("id") is not None:
                _run(["wpctl", "set-default", str(eq_sink["id"])])
                break
            time.sleep(0.1)

    return True, f"Applied '{profile_name}' ({len([b for b in profile.bands if b.enabled])} active bands)."


def stop_eq() -> Tuple[bool, str]:
    state = load_state()
    _run(["systemctl", "--user", "stop", SERVICE_NAME])
    _run(["systemctl", "--user", "disable", SERVICE_NAME])
    _restore_hardware_default(state)
    return True, "Parametric EQ disabled (bypassed)."
