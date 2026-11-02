"""Centralized Style & Engine Tuning Configuration Loader (`style_config.py`).

Consolidates all audio, cut-timing, zoom, mark, and brief-budget thresholds into a
single tunable schema backed by `styles/<style>/spec.json` (`"tuning"` block), so new
ad styles can tune pacing, loudness, zooms, and mark timings without modifying Python code.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
STYLES_DIR = ROOT / "styles"

DEFAULT_TUNING: Dict[str, Any] = {
    # Silence & breath detection (prepare.py)
    "macro_silence_db": -35.0,
    "macro_silence_min_sec": 0.30,
    "micro_dip_db": -28.0,
    "micro_dip_min_sec": 0.05,
    "breath_onset_db": -32.0,
    "breath_lead_pad_sec": 0.14,
    "breath_tail_pad_sec": 0.08,
    # Pause tightening & pacing pass (prepare.py / verify.py)
    "pause_split_monologue_sec": 1.35,
    "pause_split_dialogue_sec": 1.45,
    "pause_beat_gap_sec": 0.28,
    "pause_mid_thought_gap_sec": 0.17,
    "max_internal_silence_sec": 1.05,
    "pacing_max_static_sec": 6.0,
    "long_monologue_zoom_sec": 9.50,
    # Dynamic zooms & focus pan (layouts.py / vision_tracker.py)
    "jump_cut_zoom": 1.08,
    "push_in_zoom": 1.06,
    "closing_cta_zoom": 1.05,
    "focus_pan_deadband": 0.04,
    "static_camera_motion_max": 2.8,
    # Marks & SFX (prepare.py / render.py)
    "mark_duration_sec": 0.56,
    "mark_alpha": 0.50,
    "wrong_sfx_primary": "buzzer.wav",
    "wrong_sfx_layer": "kn20228_wrong_core.mp3",
    "right_sfx_primary": "correct_ding.wav",
    "right_sfx_layer": "sn_correct.wav",
    "whoosh_sfx": "whoosh.wav",
    "wrong_sfx_gain_db": 4.0,
    "right_sfx_gain_db": 2.5,
    "whoosh_sfx_gain_db": -2.0,
    "max_hook_transitions": 2,
    "use_hw_accel": True,
    # Audio crossfades, room tone & loudness mastering (layouts.py / render.py / verify.py)
    "edge_fade_sec": 0.012,
    "audio_crossfade_sec": 0.045,
    "room_tone_dbfs": -48.0,
    "clip_rms_target_dbfs": -18.0,
    "target_loudness_lufs": -11.5,
    "true_peak_dbtp": -1.0,
    "music_duck_gain_db": -18.0,
    # Token budget & brief compactness (prepare.py)
    "max_alt_takes_in_brief": 2,
    "max_brief_chars": 3900,
}

_TUNING_CACHE: Dict[str, Dict[str, Any]] = {}


def load_style_spec(style_name: str = "micronarrative") -> Dict[str, Any]:
    """Load `styles/<style_name>/spec.json` if present, else return `{}`."""
    spec_path = STYLES_DIR / style_name / "spec.json"
    if not spec_path.is_file():
        return {}
    try:
        return json.loads(spec_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_tuning(style_name: Optional[str] = "micronarrative", plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return merged tuning dictionary (`DEFAULT_TUNING` <- `styles/<style>/spec.json["tuning"]` <- `plan["tuning"]`)."""
    eff_style = str(
        (plan or {}).get("style")
        or style_name
        or "micronarrative"
    ).strip()
    if eff_style not in _TUNING_CACHE:
        merged = dict(DEFAULT_TUNING)
        spec = load_style_spec(eff_style)
        spec_tuning = spec.get("tuning")
        if isinstance(spec_tuning, dict):
            merged.update(spec_tuning)
        _TUNING_CACHE[eff_style] = merged

    out = dict(_TUNING_CACHE[eff_style])
    plan_tuning = (plan or {}).get("tuning")
    if isinstance(plan_tuning, dict):
        out.update(plan_tuning)
    return out
