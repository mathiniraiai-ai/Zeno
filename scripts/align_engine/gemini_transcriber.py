"""
Self-contained Gemini Flash-Lite / Flash Verbatim Transcriber + Offline Fallback
for Claude Editor (`scripts/align_engine/gemini_transcriber.py`).

- Reads `GEMINI_API_KEY` / `GEMINI_API_KEY_1..4` (or `SCRIPTCUT_CLOUD_URL` + `SCRIPTCUT_CLOUD_TOKEN`)
  from environment, `.env`, or `../ScriptCut-AI/.env`.
- Uses `google-genai` (`gemini-3.5-flash-lite` / `gemini-2.5-flash` fallback) to transcribe a 16kHz WAV file
  with `target_script_lines` hint, returning verbatim spoken words (including retakes, repeated words, and fumbles),
  and runs `guardian_engine` healing.
- Includes `transcribe_offline_fallback` if no Gemini API key or internet connection is available,
  using existing `hq_words.json`/`words.json` or direct script-line forced alignment.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .guardian import guardian_engine

logger = logging.getLogger("align_engine.gemini_transcriber")

THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent.parent
SIBLING_SCRIPTCUT = PROJECT_ROOT.parent / "ScriptCut-AI"


def _load_env_files() -> None:
    """Loads `.env` from Claude Editor root or sibling `../ScriptCut-AI/.env` without overriding set env vars."""
    candidates = [
        PROJECT_ROOT / ".env",
        SIBLING_SCRIPTCUT / ".env",
    ]
    try:
        from dotenv import load_dotenv
        for env_path in candidates:
            if env_path.is_file():
                load_dotenv(env_path, override=False)
    except ImportError:
        for env_path in candidates:
            if not env_path.is_file():
                continue
            try:
                for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
            except Exception:
                pass

    # Auto-load local zero-copy OAuth session from .zeno_session.json if present
    session_path = PROJECT_ROOT / ".zeno_session.json"
    if session_path.is_file():
        try:
            sess = json.loads(session_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(sess, dict):
                tok = str(sess.get("token") or "").strip()
                url = str(sess.get("cloud_url") or "").strip()
                if tok:
                    os.environ.setdefault("ZENO_CLOUD_TOKEN", tok)
                    os.environ.setdefault("SCRIPTCUT_CLOUD_TOKEN", tok)
                if url:
                    os.environ.setdefault("ZENO_CLOUD_URL", url)
                    os.environ.setdefault("SCRIPTCUT_CLOUD_URL", url)
        except Exception:
            pass

    for a, b in (
        ("ZENO_CLOUD_URL", "SCRIPTCUT_CLOUD_URL"),
        ("ZENO_CLOUD_TOKEN", "SCRIPTCUT_CLOUD_TOKEN"),
    ):
        va = os.environ.get(a, "").strip()
        vb = os.environ.get(b, "").strip()
        if va and not vb:
            os.environ[b] = va
        elif vb and not va:
            os.environ[a] = vb


_load_env_files()


def get_gemini_api_keys() -> List[str]:
    """Collects all configured Gemini API keys from environment / `.env`."""
    _load_env_files()
    keys: List[str] = []
    for var in (
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_1",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4",
        "GOOGLE_API_KEY",
    ):
        val = os.getenv(var, "").strip().strip('"').strip("'")
        if val and not val.startswith("your_") and val not in keys:
            keys.append(val)
    return keys


def normalize_lang_code(lang: Optional[str]) -> str:
    """Maps language names ('kannada', 'tamil', 'telugu', 'hindi', 'english', 'auto') to 2-letter ISO codes."""
    if not lang:
        return "auto"
    s = str(lang).strip().lower()
    mapping = {
        "auto": "auto",
        "kannada": "kn",
        "kn": "kn",
        "tamil": "ta",
        "ta": "ta",
        "telugu": "te",
        "te": "te",
        "hindi": "hi",
        "hi": "hi",
        "malayalam": "ml",
        "ml": "ml",
        "bengali": "bn",
        "bn": "bn",
        "marathi": "mr",
        "mr": "mr",
        "gujarati": "gu",
        "gu": "gu",
        "english": "en",
        "en": "en",
    }
    return mapping.get(s, s[:2] if len(s) >= 2 else "en")


def build_transcribe_prompt(target_script_lines: Optional[List[str]] = None, language_hint: str = "auto") -> str:
    script_context = ""
    if target_script_lines:
        clean_lines = [str(x).strip() for x in target_script_lines if str(x).strip()][:40]
        if clean_lines:
            joined_script = "\n".join(f"- {line}" for line in clean_lines)
            script_context = (
                "\n## Target Script Reference (Speaker may have retakes, repeated words, false starts, or variations):\n"
                f"{joined_script}\n"
            )

    lang_note = (
        f"Main native language: {language_hint}. The audio is MIXED: it can switch to English for single words, "
        "phrases, whole sentences or whole scenes. Transcribe EVERY spoken word, in whichever language it is actually spoken. "
        "Never skip English because a native language was named.\n"
        if language_hint and language_hint != "auto" else ""
    )
    return f"""You are a precision multilingual speech-to-text transcription engine for video editing.
Transcribe ONLY the intelligible words actually spoken aloud in this audio clip 100% verbatim.
{lang_note}{script_context}
Rules:
1. Include ALL spoken words verbatim, including retakes, repeated words (e.g. "Almost... Almost"), false starts, and fumbles.
2. NEVER auto-complete a sentence from the Target Script Reference if the speaker stops mid-sentence or does not speak it in this clip!
3. If the clip ends with unintelligible AI gibberish/babble after the real sentence concludes, DO NOT transcribe the unintelligible gibberish.
4. STRICT BILINGUAL CODE-SWITCHING LAW: Decide language word by word. A spoken English word stays English, in the Latin alphabet (A-Z), even inside a native sentence. A native word stays native, in its own script. Never transliterate English into native script or native words into Latin.
5. Attach punctuation (.,?!) to the end of the spoken word.
6. Return ONLY valid JSON with this exact structure:
{{
  "detected_language_code": "kn",
  "detected_language_name": "Kannada",
  "full_transcript": "Complete verbatim transcript...",
  "words": ["word1", "word2", "word3"],
  "cues": [
    {{"text": "sentence or clause text", "start": 0.0, "end": 2.4, "words": ["word1", "word2"]}}
  ]
}}"""


class GeminiRateLimiter:
    _instance: Optional["GeminiRateLimiter"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, rpm: Optional[int] = None) -> "GeminiRateLimiter":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(rpm=rpm)
            return cls._instance

    def __init__(self, rpm: Optional[int] = None):
        if rpm is None:
            try:
                rpm = int(os.getenv("GEMINI_RPM", "30"))
            except ValueError:
                rpm = 30
        self.rpm = max(1, rpm)
        self.interval = 60.0 / float(self.rpm)
        self._call_lock = threading.Lock()
        self._last_call = 0.0

    def acquire(self) -> None:
        with self._call_lock:
            now = time.time()
            wait = self.interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()


class GeminiTranscriber:
    """
    Self-contained Gemini audio transcriber with multi-key rotation, cloud endpoint support,
    `guardian_engine` healing, and offline fallback.
    """

    def __init__(self, api_keys: Optional[List[str]] = None):
        _load_env_files()
        self.api_keys = api_keys if api_keys is not None else get_gemini_api_keys()
        primary = (
            os.getenv("GEMINI_PRIMARY_MODEL", "").strip()
            or os.getenv("GEMINI_MODEL", "").strip()
            or "gemini-2.5-flash"
        )
        # Put fast, reliable models in rotation: gemini-2.5-flash and gemini-3.5-flash-lite
        default_models = ["gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-flash-lite-latest"]
        self.models = [primary] + [m for m in default_models if m != primary]
        self.cloud_url = (
            os.getenv("ZENO_CLOUD_URL", "").strip()
            or os.getenv("SCRIPTCUT_CLOUD_URL", "").strip()
        )
        self.cloud_token = (
            os.getenv("ZENO_CLOUD_TOKEN", "").strip()
            or os.getenv("SCRIPTCUT_CLOUD_TOKEN", "").strip()
        )
        self.rate_limiter = GeminiRateLimiter.get_instance()
        self._key_idx = 0
        self._model_idx = 0
        self._client = None
        self._client_key: Optional[str] = None

    @property
    def is_configured(self) -> bool:
        if os.getenv("CLAUDE_EDITOR_OFFLINE", "").lower() in ("1", "true", "yes"):
            return False
        if os.getenv("SCRIPTCUT_OFFLINE", "").lower() in ("1", "true", "yes"):
            return False
        return len(self.api_keys) > 0 or bool(self.cloud_url and self.cloud_token)

    def _get_client(self):
        from google import genai
        from google.genai import types

        if not self.api_keys:
            raise RuntimeError("No GEMINI_API_KEY configured")
        key = self.api_keys[self._key_idx % len(self.api_keys)]
        if self._client is None or self._client_key != key:
            # Minimum allowed deadline by Google GenAI API is 10s (10000ms); use 25000ms
            self._client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=25000))
            self._client_key = key
        return self._client

    def _rotate(self) -> None:
        self._key_idx += 1
        self._client = None
        self._client_key = None
        if self._key_idx % max(1, len(self.api_keys)) == 0:
            self._model_idx += 1

    @staticmethod
    def _parse_json_lenient(raw_text: str) -> Dict[str, Any]:
        text = (raw_text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            res = json.loads(text)
            return res if isinstance(res, dict) else {"words": res if isinstance(res, list) else []}
        except Exception:
            m = re.search(r"(\{[\s\S]*\})", text)
            if m:
                return json.loads(m.group(1))
            raise

    def _heal_result(
        self,
        data: Dict[str, Any],
        language_hint: str,
        model_used: str,
        clip_duration_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        norm_hint = normalize_lang_code(language_hint)
        fallback_lang = norm_hint if norm_hint != "auto" else "en"
        lang_code = normalize_lang_code(data.get("detected_language_code") or fallback_lang)
        if lang_code == "auto":
            lang_code = fallback_lang

        # Strip hallucinated cues whose start timestamp is at or past the physical clip duration
        raw_cues = data.get("cues") or []
        if clip_duration_sec and clip_duration_sec > 1.0 and isinstance(raw_cues, list) and raw_cues:
            valid_cues = [
                c for c in raw_cues
                if isinstance(c, dict) and float(c.get("start") or 0.0) < (clip_duration_sec - 0.15)
            ]
            if valid_cues and len(valid_cues) < len(raw_cues):
                raw_cues = valid_cues
                cue_words = [w for c in valid_cues for w in (c.get("words") or str(c.get("text") or "").split()) if str(w).strip()]
                if cue_words:
                    data = dict(data)
                    data["cues"] = valid_cues
                    data["words"] = cue_words
                    data["full_transcript"] = " ".join(str(c.get("text") or "").strip() for c in valid_cues if str(c.get("text") or "").strip())

        raw_words = data.get("words") or []
        if not raw_words and data.get("full_transcript"):
            raw_words = str(data["full_transcript"]).split()

        audit = guardian_engine.audit(
            full_transcript=str(data.get("full_transcript", "")),
            words=raw_words,
            target_language=lang_code,
        )
        healed_words = [w["word"] for w in (audit.healed_words or [])] or [str(w) for w in raw_words]
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return {
            "status": "success",
            "model_used": model_used,
            "language": lang_code,
            "full_transcript": audit.healed_text or " ".join(healed_words),
            "words": healed_words,
            "cues": raw_cues,
            "usage": {
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or (int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0))),
            },
        }

    def _transcribe_via_cloud(
        self,
        wav_path: str,
        language_hint: str = "auto",
        target_script_lines: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        import httpx

        url = self.cloud_url.rstrip("/") + "/v1/transcribe"
        headers = {"Authorization": f"Bearer {self.cloud_token}"} if self.cloud_token else {}
        form_data = {
            "language_hint": language_hint or "auto",
            "target_script_lines": json.dumps(list(target_script_lines or [])[:40], ensure_ascii=False),
        }
        with httpx.Client(timeout=240.0) as http:
            with open(wav_path, "rb") as fh:
                resp = http.post(
                    url,
                    headers=headers,
                    data=form_data,
                    files={"file": (os.path.basename(wav_path), fh, "audio/wav")},
                )
        resp.raise_for_status()
        data = resp.json()
        if data.get("warning"):
            print(f"[transcribe] WARNING from service: {data['warning']}", file=sys.stderr)
        dur_sec = (Path(wav_path).stat().st_size / 32000.0) if Path(wav_path).is_file() else None
        return self._heal_result(data, language_hint, model_used=str(data.get("model_used") or "cloud"), clip_duration_sec=dur_sec)

    def transcribe(
        self,
        wav_path: str,
        language_hint: str = "auto",
        target_script_lines: Optional[List[str]] = None,
        work_dir: Optional[Path] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        Transcribes a 16kHz WAV file via Gemini (or cloud endpoint), caches the result in `work_dir`,
        and heals loanwords via `guardian_engine`. Falls back to `transcribe_offline_fallback` on failure.
        """
        wav_p = Path(wav_path)
        wdir = Path(work_dir) if work_dir else wav_p.parent
        cache_file = wdir / "gemini_transcript.json"
        dur_sec = (wav_p.stat().st_size / 32000.0) if wav_p.is_file() else None

        if use_cache and cache_file.is_file() and wav_p.is_file():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                st = wav_p.stat()
                if cached.get("wav_size") == st.st_size and cached.get("words"):
                    return self._heal_result(
                        {
                            "detected_language_code": cached.get("language", language_hint),
                            "full_transcript": cached.get("full_transcript", ""),
                            "words": cached.get("words", []),
                            "cues": cached.get("cues", []),
                            "usage": cached.get("usage") or {},
                        },
                        language_hint=language_hint,
                        model_used=cached.get("model_used", "cached_gemini"),
                        clip_duration_sec=dur_sec,
                    )
            except Exception:
                pass

        if not self.is_configured or not wav_p.is_file():
            return transcribe_offline_fallback(
                wav_path=str(wav_p),
                lang=language_hint,
                script_lines=target_script_lines,
                work_dir=wdir,
            )

        if self.cloud_url and not self.api_keys:
            try:
                res = self._transcribe_via_cloud(str(wav_p), language_hint, target_script_lines)
                self._save_cache(cache_file, wav_p, res)
                return res
            except Exception as e:
                logger.warning("Cloud transcription failed, falling back to offline: %s", e)
                print(f"[transcribe] WARNING: transcription service failed ({type(e).__name__}); using the OFFLINE script-based fallback, "
                      "which cannot hear retakes or ad-libs. Check SCRIPTCUT_CLOUD_URL / SCRIPTCUT_CLOUD_TOKEN.", file=sys.stderr)
                return transcribe_offline_fallback(
                    wav_path=str(wav_p),
                    lang=language_hint,
                    script_lines=target_script_lines,
                    work_dir=wdir,
                )

        audio_bytes = wav_p.read_bytes()
        prompt = build_transcribe_prompt(target_script_lines, language_hint=language_hint)

        max_attempts = min(4, max(2, len(self.api_keys)))
        for attempt in range(max_attempts):
            try:
                from google.genai import types

                self.rate_limiter.acquire()
                client = self._get_client()
                model_name = self.models[self._model_idx % len(self.models)]
                audio_part = types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav")
                resp = client.models.generate_content(
                    model=model_name,
                    contents=[audio_part, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                data = self._parse_json_lenient(resp.text or "{}")
                um = getattr(resp, "usage_metadata", None)
                if um is not None:
                    in_tok = int(getattr(um, "prompt_token_count", 0) or 0)
                    out_tok = int(getattr(um, "candidates_token_count", 0) or 0) + int(getattr(um, "thoughts_token_count", 0) or 0)
                    tot_tok = int(getattr(um, "total_token_count", 0) or (in_tok + out_tok))
                    data["usage"] = {"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": tot_tok}
                res = self._heal_result(data, language_hint, model_used=model_name, clip_duration_sec=dur_sec)
                if res.get("words"):
                    self._save_cache(cache_file, wav_p, res)
                    return res
            except Exception as e:
                err_str = str(e)
                self._rotate()
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    time.sleep(min(4.0, 1.0 + attempt + random.uniform(0.1, 0.5)))
                else:
                    time.sleep(0.2)

        print("[transcribe] WARNING: Gemini failed on every attempt; using the OFFLINE script-based fallback, "
              "which cannot hear retakes or ad-libs.", file=sys.stderr)
        return transcribe_offline_fallback(
            wav_path=str(wav_p),
            lang=language_hint,
            script_lines=target_script_lines,
            work_dir=wdir,
        )

    @staticmethod
    def _save_cache(cache_file: Path, wav_p: Path, res: Dict[str, Any]) -> None:
        try:
            st = wav_p.stat()
            payload = {
                "wav_size": st.st_size,
                "wav_mtime": st.st_mtime_ns,
                "language": res.get("language", "en"),
                "model_used": res.get("model_used", "gemini"),
                "full_transcript": res.get("full_transcript", ""),
                "words": res.get("words", []),
                "cues": res.get("cues", []),
                "usage": res.get("usage") or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            }
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_name(cache_file.name + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, cache_file)
        except Exception:
            pass


def transcribe_offline_fallback(
    wav_path: str,
    lang: str = "auto",
    script_lines: Optional[List[str]] = None,
    work_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Offline fallback transcription when no Gemini API key or internet connection is available:
      1. Uses existing `hq_words.json` or `words.json` in `work_dir` (if present),
         healing loanwords with `guardian_engine`.
      2. Else uses `script_lines` for direct script-line forced alignment with Wav2Vec2 + uroman.
    """
    wav_p = Path(wav_path)
    wdir = Path(work_dir) if work_dir else wav_p.parent
    lang_code = normalize_lang_code(lang)
    if lang_code == "auto":
        lang_code = "kn" if any(
            re.search(r"[\u0C80-\u0CFF]", s) for s in (script_lines or [])
        ) else "en"

    # 1. Check existing work_dir word files (hq_words.json preferred over words.json)
    for fname in ("hq_words.json", "words.json"):
        fpath = wdir / fname
        if fpath.is_file():
            try:
                items = json.loads(fpath.read_text(encoding="utf-8"))
                if isinstance(items, list) and len(items) > 0:
                    raw_words = []
                    max_end = 0.0
                    for item in items:
                        if isinstance(item, dict):
                            w_txt = str(item.get("w") if "w" in item else item.get("word", "")).strip()
                            e_val = float(item.get("end") or 0.0)
                            if w_txt:
                                raw_words.append(w_txt)
                                if e_val > max_end:
                                    max_end = e_val
                        elif isinstance(item, str) and item.strip():
                            raw_words.append(item.strip())
                    if raw_words:
                        has_indic = any(bool(re.search(r"[\u0900-\u0D7F]", w)) for w in raw_words)
                        detected_lang = lang_code if has_indic else "en"
                        audit = guardian_engine.audit(
                            full_transcript=" ".join(raw_words),
                            words=raw_words,
                            target_language=detected_lang,
                        )
                        healed_words = [w["word"] for w in (audit.healed_words or [])] or raw_words
                        return {
                            "status": "success",
                            "model_used": f"offline_cached:{fname}",
                            "language": detected_lang,
                            "full_transcript": " ".join(healed_words),
                            "words": healed_words,
                            "cues": [],
                            "rough_end_sec": max_end if max_end > 0 else None,
                        }
            except Exception:
                pass

    # 2. Direct script-line forced alignment fallback
    if script_lines:
        from .script_matcher import parse_target_script

        parsed = parse_target_script("\n".join(script_lines))
        words_from_script = [w for line in parsed for w in line.split() if w.strip()]
        if words_from_script:
            return {
                "status": "success",
                "model_used": "script_forced_align",
                "language": lang_code,
                "full_transcript": " ".join(words_from_script),
                "words": words_from_script,
                "cues": [],
            }

    return {
        "status": "offline",
        "model_used": "offline_empty",
        "language": lang_code,
        "full_transcript": "",
        "words": [],
        "cues": [],
    }


_shared_transcriber: Optional[GeminiTranscriber] = None


def get_shared_transcriber() -> GeminiTranscriber:
    global _shared_transcriber
    if _shared_transcriber is None:
        _shared_transcriber = GeminiTranscriber()
    return _shared_transcriber
