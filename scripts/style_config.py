"""Centralized Style & Engine Tuning Configuration Loader (`style_config.py`).

Loads style specifications and tuning overrides from `formats/specs/<style>_spec.json`
with clean built-in defaults when `style` is `"micronarrative"` or empty, exposing a
unified parameter schema for `prepare.py`, `render.py`, `verify.py`, and `zeno.py`.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = ROOT / "formats" / "specs"
STYLES_DIR = SPECS_DIR  # Alias retained for backward compatibility

DEFAULT_TUNING: Dict[str, Any] = {
    # Silence & breath detection (prepare.py)
    "macro_silence_db": -35.0,
    "macro_silence_min_sec": 0.30,
    "micro_dip_db": -28.0,
    "micro_dip_min_sec": 0.05,
    "breath_onset_db": -32.0,
    "breath_lead_pad_sec": 0.14,
    "breath_tail_pad_sec": 0.08,
    # Pause tightening & pacing thresholds (prepare.py / verify.py / script_order.py)
    "tight_join_sec": 0.18,
    "tight_pause_sec": 0.25,
    "pause_split_monologue_sec": 1.35,
    "pause_split_dialogue_sec": 1.45,
    "pause_beat_gap_sec": 0.28,
    "pause_mid_thought_gap_sec": 0.17,
    "max_internal_silence_sec": 1.05,
    "pacing_max_static_sec": 6.0,
    "long_monologue_zoom_sec": 9.50,
    # Dynamic zooms & focus framing (layouts.py / vision_tracker.py)
    "jump_cut_zoom": 1.08,
    "push_in_zoom": 1.06,
    "split_active_max_zoom": 1.0,
    "closing_cta_zoom": 1.05,
    "focus_pan_deadband": 0.04,
    "static_camera_motion_max": 2.8,
    # Marks, subtitles & SFX (prepare.py / render.py — calibrated for -10.5 LUFS master)
    "mark_duration_sec": 0.48,
    "mark_tail_hold_sec": 0.38,
    "mark_alpha": 0.70,
    "wrong_sfx_primary": "sn_wrong.wav",
    "wrong_sfx_layer": "none",
    "right_sfx_primary": "correct_ding.wav",
    "right_sfx_layer": "sn_correct.wav",
    "whoosh_sfx": "whoosh.wav",
    "riser_sfx": "sn_riser_1.wav",
    "wrong_sfx_gain_db": 1.0,
    "right_sfx_gain_db": 0.5,
    "whoosh_sfx_gain_db": -0.5,
    "max_hook_transitions": 5,
    "default_subs_style": "auto",
    "default_subs_y": 65,
    "split_v_subs_y": 50,
    "use_hw_accel": True,
    # Audio crossfades, room tone & loudness mastering (layouts.py / render.py / verify.py)
    "edge_fade_sec": 0.012,
    "audio_crossfade_sec": 0.045,
    "room_tone_dbfs": -48.0,
    "clip_rms_target_dbfs": -18.0,
    "target_loudness_lufs": -10.5,
    "true_peak_dbtp": -1.0,
    "alimiter_limit": 0.80,
    "music_duck_gain_db": -18.0,
    # Candidate and brief formatting limits (prepare.py)
    "max_alt_takes_in_brief": 2,
    "max_brief_chars": 3900,
}

DEFAULT_MICRONARRATIVE_TUNING: Dict[str, Any] = {
    **DEFAULT_TUNING,
    "macro_silence_min_sec": 0.25,
    "breath_lead_pad_sec": 0.04,
    "breath_tail_pad_sec": 0.04,
    "pause_split_monologue_sec": 0.25,
    "pause_split_dialogue_sec": 0.50,
    "pause_beat_gap_sec": 0.15,
    "pause_mid_thought_gap_sec": 0.12,
    "max_internal_silence_sec": 0.25,
    "pacing_max_static_sec": 4.0,
    "long_monologue_zoom_sec": 5.0,
    "closing_cta_zoom": 1.06,
    "max_hook_transitions": 6,
}

DEFAULT_MICRONARRATIVE_SPEC: Dict[str, Any] = {
    "style": "micronarrative",
    "engine": {
        "scene_layouts": {"1": "single", "2": "single", "3": "split_h"},
        "split": {
            "mode": "live",
            "divider": {
                "x_frac": 0.49583,
                "w_frac": 0.00417,
                "color": "E1DBE1",
                "alpha": 1.0,
            },
        },
        "canvas_default": {"width": 1080, "height": 1920, "fps": 30},
        "color_match": False,
        "lut": None,
    },
    "tuning": DEFAULT_MICRONARRATIVE_TUNING,
}

_TUNING_CACHE: Dict[Tuple[str, int], Dict[str, Any]] = {}


def _resolve_spec_path(style_name: str) -> Optional[Path]:
    """Locate `formats/specs/<style>_spec.json` (or `<style>.json` if named directly)."""
    clean = (style_name or "").strip().lower()
    if not clean:
        clean = "micronarrative"
    if clean.endswith("_spec"):
        candidates = [SPECS_DIR / f"{clean}.json"]
    else:
        candidates = [
            SPECS_DIR / f"{clean}_spec.json",
            SPECS_DIR / f"{clean}.json",
        ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _normalize_tuning_overrides(merged: Dict[str, Any], raw_overrides: Dict[str, Any], migrate_legacy: bool = True) -> None:
    """Normalize legacy SFX filenames and pre-loudnorm gain values in-place."""
    if str(merged.get("riser_sfx", "")).strip().lower() in ("riser", "riser.wav"):
        merged["riser_sfx"] = "sn_riser_1.wav"
    if migrate_legacy:
        if raw_overrides.get("wrong_sfx_gain_db") == 4.0:
            merged["wrong_sfx_gain_db"] = DEFAULT_TUNING["wrong_sfx_gain_db"]
        if raw_overrides.get("right_sfx_gain_db") == 2.5:
            merged["right_sfx_gain_db"] = DEFAULT_TUNING["right_sfx_gain_db"]
        if raw_overrides.get("whoosh_sfx_gain_db") == -2.0:
            merged["whoosh_sfx_gain_db"] = DEFAULT_TUNING["whoosh_sfx_gain_db"]
        if isinstance(raw_overrides.get("max_hook_transitions"), (int, float)) and raw_overrides["max_hook_transitions"] < 5:
            merged["max_hook_transitions"] = DEFAULT_TUNING["max_hook_transitions"]
        if raw_overrides.get("mark_alpha") == 0.5:
            merged["mark_alpha"] = DEFAULT_TUNING["mark_alpha"]
        if raw_overrides.get("target_loudness_lufs") == -11.5:
            merged["target_loudness_lufs"] = DEFAULT_TUNING["target_loudness_lufs"]


def load_style_spec(style_name: str = "micronarrative") -> Dict[str, Any]:
    """Load `formats/specs/<style_name>_spec.json` if present, with fallback defaults for `micronarrative` or empty `style_name`."""
    eff_style = (style_name or "").strip().lower() or "micronarrative"
    spec_path = _resolve_spec_path(eff_style)
    if spec_path is not None:
        try:
            data = json.loads(spec_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if eff_style == "micronarrative":
                    base = copy.deepcopy(DEFAULT_MICRONARRATIVE_SPEC)
                    for k, v in data.items():
                        if k == "tuning" and isinstance(v, dict):
                            base["tuning"].update(v)
                        elif k == "engine" and isinstance(v, dict):
                            base["engine"].update(v)
                        else:
                            base[k] = v
                    return base
                return data
        except (OSError, ValueError):
            pass

    if eff_style == "micronarrative":
        return copy.deepcopy(DEFAULT_MICRONARRATIVE_SPEC)
    return {}


def get_tuning(style_name: Optional[str] = "micronarrative", plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return merged tuning dictionary (`DEFAULT_TUNING` <- `formats/specs/<style>_spec.json["tuning"]` <- `plan["tuning"]`)."""
    eff_style = str((plan or {}).get("style") or style_name or "micronarrative").strip().lower() or "micronarrative"
    spec_path = _resolve_spec_path(eff_style)
    mtime_ns = spec_path.stat().st_mtime_ns if spec_path is not None else 0
    cache_key = (eff_style, mtime_ns)

    if cache_key not in _TUNING_CACHE:
        merged = dict(DEFAULT_MICRONARRATIVE_TUNING if eff_style == "micronarrative" else DEFAULT_TUNING)
        spec = load_style_spec(eff_style)
        spec_tuning = spec.get("tuning")
        if isinstance(spec_tuning, dict):
            merged.update(spec_tuning)
            _normalize_tuning_overrides(merged, spec_tuning, migrate_legacy=True)
        _TUNING_CACHE[cache_key] = merged

    out = dict(_TUNING_CACHE[cache_key])
    plan_tuning = (plan or {}).get("tuning")
    if isinstance(plan_tuning, dict):
        out.update(plan_tuning)
        _normalize_tuning_overrides(out, plan_tuning, migrate_legacy=False)
    return out


def clear_cache() -> None:
    """Clear the cached style tuning dictionaries."""
    _TUNING_CACHE.clear()
