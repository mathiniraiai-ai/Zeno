"""Gemini and Zeno Cloud verbatim audio transcriber with offline fallback.

- Resolves credentials (`ZENO_CLOUD_URL` + `ZENO_CLOUD_TOKEN` or `GEMINI_API_KEY` / `GEMINI_API_KEY_1..4`)
  via `common.load_dotenv()`, `.env`, `.zeno_session.json`, or read-only fallback to sibling `ScriptCut-AI`.
- Transcribes 16 kHz mono WAV audio verbatim (preserving stutters, repeated words, false starts, and retakes)
  with optional `target_script_lines` vocabulary reference and `guardian_engine` loanword healing.
- Falls back to `transcribe_offline_fallback` when offline or unconfigured.
"""

from __future__ import annotations

import io
import json
import logging
import os
import platform
import random
import re
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

from .guardian import guardian_engine

logger = logging.getLogger("align_engine.gemini_transcriber")

THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent
PROJECT_ROOT = SCRIPTS_DIR.parent
SIBLING_SCRIPTCUT = PROJECT_ROOT.parent / "ScriptCut-AI"


def zeno_client_headers() -> Dict[str, str]:
    """Return `X-Zeno-Version` and `X-Zeno-Platform` request headers for Zeno Cloud."""
    ver = ""
    ver_path = PROJECT_ROOT / "VERSION"
    if ver_path.is_file():
        try:
            ver = ver_path.read_text(encoding="utf-8", errors="ignore").strip()[:40]
        except OSError:
            pass
    return {
        "X-Zeno-Version": ver,
        "X-Zeno-Platform": f"{platform.system()} {platform.machine()}"[:80],
    }


def _parse_env_file_readonly(env_path: Path) -> None:
    """Read key=value pairs from `env_path` into `os.environ` without overriding set variables."""
    if not env_path.is_file():
        return
    try:
        lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k or k in os.environ:
            continue
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        elif "#" in v:
            v = v.split("#", 1)[0].strip()
        if k == "FFMPEG_PATH" and not Path(v).is_absolute():
            continue
        os.environ[k] = v


def _load_env_files() -> None:
    """Load `.env` and `.zeno_session.json` via `common` with read-only fallback to sibling directories."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        from common import load_dotenv as _common_load_dotenv

        _common_load_dotenv()
    except Exception:
        pass

    for env_cand in (
        PROJECT_ROOT / ".env",
        SIBLING_SCRIPTCUT / ".env",
    ):
        _parse_env_file_readonly(env_cand)

    # Read-only session lookup: Zeno/.zeno_session.json
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
    """Collect configured Gemini API keys from environment variables."""
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
    """Normalize language names (`'kannada'`, `'tamil'`, `'telugu'`, `'malayalam'`, `'hindi'`, `'english'`, `'auto'`) to ISO codes."""
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
        "punjabi": "pa",
        "pa": "pa",
        "odia": "or",
        "or": "or",
        "english": "en",
        "en": "en",
    }
    return mapping.get(s, s[:2] if len(s) >= 2 else "en")


def build_transcribe_prompt(
    target_script_lines: Optional[List[str]] = None,
    language_hint: str = "auto",
) -> str:
    """Build the verbatim multilingual transcription prompt with bilingual code-switching rules."""
    script_context = ""
    if target_script_lines:
        clean_lines = [str(x).strip() for x in target_script_lines if str(x).strip()][:40]
        if clean_lines:
            joined_script = "\n".join(f"- {line}" for line in clean_lines)
            script_context = (
                "\n## Target Script Reference (Speaker may have retakes, repeated words, false starts, or variations):\n"
                f"{joined_script}\n"
            )

    scripts = {
        "ml": ("Malayalam", "Malayalam script (മലയാളം)"),
        "kn": ("Kannada", "Kannada script (ಕನ್ನಡ)"),
        "ta": ("Tamil", "Tamil script (தமிழ்)"),
        "te": ("Telugu", "Telugu script (తెలుగు)"),
        "hi": ("Hindi", "Devanagari script (हिन्दी)"),
        "mr": ("Marathi", "Devanagari script (मराठी)"),
        "bn": ("Bengali", "Bengali script (বাংলা)"),
        "gu": ("Gujarati", "Gujarati script (ગુજરાતી)"),
        "pa": ("Punjabi", "Gurmukhi script (ਪੰਜਾਬੀ)"),
        "or": ("Odia", "Odia script (ଓଡ଼ିଆ)"),
    }
    hint = (language_hint or "auto").strip().lower()
    for code, (lname, _) in scripts.items():
        if hint == lname.lower():
            hint = code
    if hint in scripts:
        lname, lscript = scripts[hint]
        lang_note = (
            f"Main language: {lname}, written in {lscript}. The audio is MIXED: it can switch to English for single words, "
            "phrases, whole sentences or whole scenes. Transcribe EVERY spoken word, in whichever language it is actually spoken. "
            "Never skip English because a native language was named.\n"
            f"SCRIPT LAW: every non-English word is written in {lscript} and nothing else. Never write a word in Bengali, "
            "Devanagari, Tamil, Telugu, Kannada, Malayalam or any other script than the one named, even when a word sounds like another language, "
            f"the audio is unclear or the speaker is quiet. Give your best {lname} spelling instead. Only {lscript} and English (Latin A-Z) may appear.\n"
        )
    elif hint != "auto":
        lang_note = (
            f"Main native language: {language_hint}. The audio is MIXED: it can switch to English for single words, "
            "phrases, whole sentences or whole scenes. Transcribe EVERY spoken word, in whichever language it is actually spoken. "
            "Never skip English because a native language was named.\n"
        )
    else:
        lang_note = (
            "Decide the clip's main language once, from the whole clip, and write every non-English word in that language's own script. "
            "Never mix two Indic scripts in one clip.\n"
        )
    return f"""You are a precision multilingual speech-to-text transcription engine for video editing.
Transcribe ONLY the intelligible words actually spoken aloud in this audio clip 100% verbatim.
{lang_note}{script_context}
Rules:
1. Include ALL spoken words verbatim in exact chronological order, including retakes, repeated words, stutters, false starts, and restarted phrases (e.g. "Almost... Almost", "I want... I want to go", "ನಾನು ನಾನು"). NEVER deduplicate, clean up, or collapse repeated words, stutters, false starts, or restarted phrases even when Target Script Reference shows the phrase only once — output EVERY spoken attempt verbatim in chronological order (if a word or phrase is spoken twice, it MUST appear twice in `words`).
2. NEVER auto-complete a sentence or copy lines from the Target Script Reference if the speaker stops mid-sentence or does not speak it in this clip! Target Script Reference is ONLY a spelling/vocabulary reference for words actually heard in the audio.
3. ZERO-SPEECH GUARD: If the audio contains zero intelligible human speech (only silence, room tone, SFX, background noise, or music), return `"full_transcript": ""`, `"words": []`, and `"cues": []` — NEVER hallucinate or copy Target Script Reference lines onto silent or non-speech audio!
4. If the clip ends with unintelligible AI gibberish/babble after the real sentence concludes, DO NOT transcribe the unintelligible gibberish.
5. STRICT BILINGUAL CODE-SWITCHING LAW: Decide language word by word. A spoken English word stays English, in the Latin alphabet (A-Z), even inside a native sentence. A native word stays native, in its own script. Never transliterate English into native script or native words into Latin.
6. Attach punctuation (.,?!) to the end of the spoken word.
7. Return ONLY valid JSON with this exact structure:
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
    """Thread-safe RPM rate limiter for direct Gemini API requests."""

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
    """Audio transcriber supporting Zeno Cloud, multi-key Gemini rotation, and offline fallback."""

    def __init__(self, api_keys: Optional[List[str]] = None):
        _load_env_files()
        self._explicit_keys = api_keys is not None
        self.api_keys = list(api_keys) if api_keys is not None else get_gemini_api_keys()
        primary = os.getenv("GEMINI_FORCE_MODEL", "").strip() or "gemini-3.1-flash-lite"
        default_models = [
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite-preview",
            "gemini-3-flash-preview",
            "gemini-3.5-flash",
            "gemini-flash-lite-latest",
            "gemini-flash-latest",
        ]
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

    def _refresh_credentials_if_empty(self) -> None:
        if self.api_keys or (self.cloud_url and self.cloud_token):
            return
        _load_env_files()
        if not self._explicit_keys:
            self.api_keys = get_gemini_api_keys()
        self.cloud_url = (
            os.getenv("ZENO_CLOUD_URL", "").strip()
            or os.getenv("SCRIPTCUT_CLOUD_URL", "").strip()
        )
        self.cloud_token = (
            os.getenv("ZENO_CLOUD_TOKEN", "").strip()
            or os.getenv("SCRIPTCUT_CLOUD_TOKEN", "").strip()
        )

    @property
    def is_configured(self) -> bool:
        for flag in ("ZENO_OFFLINE", "CLAUDE_EDITOR_OFFLINE", "SCRIPTCUT_OFFLINE"):
            if os.getenv(flag, "").lower() in ("1", "true", "yes"):
                return False
        self._refresh_credentials_if_empty()
        return len(self.api_keys) > 0 or bool(self.cloud_url and self.cloud_token)

    def _get_client(self):
        from google import genai
        from google.genai import types

        if not self.api_keys:
            raise RuntimeError("No GEMINI_API_KEY configured")
        key = self.api_keys[self._key_idx % len(self.api_keys)]
        if self._client is None or self._client_key != key:
            self._client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=120000))
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

        # Drop cues whose start timestamp lies past the physical clip duration
        raw_cues = data.get("cues") or []
        if clip_duration_sec and clip_duration_sec > 1.0 and isinstance(raw_cues, list) and raw_cues:
            valid_cues = [
                c
                for c in raw_cues
                if isinstance(c, dict) and float(c.get("start") or 0.0) < (clip_duration_sec - 0.15)
            ]
            if valid_cues and len(valid_cues) < len(raw_cues):
                raw_cues = valid_cues
                cue_words = []
                for c in valid_cues:
                    for w in c.get("words") or str(c.get("text") or "").split():
                        w_str = (
                            str(w.get("word") or w.get("w") or w.get("text") or "").strip()
                            if isinstance(w, dict)
                            else str(w).strip()
                        )
                        if w_str:
                            cue_words.append(w_str)
                if cue_words:
                    data = dict(data)
                    data["cues"] = valid_cues
                    data["words"] = cue_words
                    data["full_transcript"] = " ".join(
                        str(c.get("text") or "").strip()
                        for c in valid_cues
                        if str(c.get("text") or "").strip()
                    )

        raw_words = data.get("words") or []
        if not raw_words and data.get("full_transcript"):
            raw_words = str(data["full_transcript"]).split()

        audit = guardian_engine.audit(
            full_transcript=str(data.get("full_transcript", "")),
            words=raw_words,
            target_language=lang_code,
        )
        fallback_words = [
            w_str
            for w in raw_words
            for w_str in [
                str(w.get("word") or w.get("w") or w.get("text") or "").strip()
                if isinstance(w, dict)
                else str(w).strip()
            ]
            if w_str
        ]
        healed_words = [w["word"] for w in (audit.healed_words or [])] or fallback_words
        return {
            "status": "success",
            "model_used": model_used,
            "language": lang_code,
            "full_transcript": audit.healed_text or " ".join(healed_words),
            "words": healed_words,
            "cues": raw_cues,
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
        headers.update(zeno_client_headers())
        form_data = {
            "language_hint": language_hint or "auto",
            "target_script_lines": json.dumps(list(target_script_lines or [])[:40], ensure_ascii=False),
        }
        audio_bytes = _pad_short_wav(Path(wav_path).read_bytes())
        resp = None
        max_attempts = 8
        with httpx.Client(timeout=240.0) as http:
            for attempt in range(max_attempts):
                resp = http.post(
                    url,
                    headers=headers,
                    data=form_data,
                    files={"file": (os.path.basename(wav_path), audio_bytes, "audio/wav")},
                )
                if resp.status_code not in (429, 502, 503):
                    break
                if attempt >= max_attempts - 1:
                    break
                retry_hdr = resp.headers.get("retry-after") if getattr(resp, "headers", None) else None
                try:
                    hdr_wait = float(retry_hdr) if retry_hdr else 0.0
                except Exception:
                    hdr_wait = 0.0
                base_wait = (5.0 if resp.status_code == 502 else 8.0) * (1.6 ** attempt)
                jitter_wait = random.uniform(1.0, min(45.0, base_wait))
                wait = min(45.0, max(1.0, hdr_wait, jitter_wait))
                print(
                    f"[transcribe] server busy ({resp.status_code}); waiting {wait:.1f}s (attempt {attempt + 1}/{max_attempts})",
                    file=sys.stderr,
                )
                time.sleep(wait)
        if resp is None:
            raise RuntimeError("No HTTP response from transcription service")
        if int(getattr(resp, "status_code", 0) or 0) >= 400:
            detail = ""
            try:
                body = resp.json()
                detail = str(body.get("detail") or body.get("error") or "")[:300]
            except Exception:
                detail = str(getattr(resp, "text", "") or "")[:300]
            try:
                resp.raise_for_status()
            except Exception as exc:
                if detail:
                    raise RuntimeError(f"HTTP {resp.status_code}: {detail}") from exc
                raise
        data = resp.json()
        if data.get("warning"):
            print(f"[transcribe] WARNING from service: {data['warning']}", file=sys.stderr)
        dur_sec = (Path(wav_path).stat().st_size / 32000.0) if Path(wav_path).is_file() else None
        return self._heal_result(
            data,
            language_hint,
            model_used=str(data.get("model_used") or "cloud"),
            clip_duration_sec=dur_sec,
        )

    def transcribe(
        self,
        wav_path: str,
        language_hint: str = "auto",
        target_script_lines: Optional[List[str]] = None,
        work_dir: Optional[Path] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """Transcribe a 16 kHz WAV file via Zeno Cloud or Gemini, cache in `work_dir`, and heal loanwords."""
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

        last_error = ""
        if self.cloud_url:
            try:
                res = self._transcribe_via_cloud(str(wav_p), language_hint, target_script_lines)
                if res.get("words"):
                    self._save_cache(cache_file, wav_p, res)
                return res
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("Cloud transcription failed: %s", exc)
                if not self.api_keys:
                    print(
                        f"[transcribe] ERROR: transcription service failed ({last_error}); "
                        "refusing to fabricate forced-aligned cuts. Check Zeno Cloud session or quota.",
                        file=sys.stderr,
                    )
                    return {
                        "status": "cloud_failed",
                        "model_used": "cloud_error",
                        "error": last_error,
                        "language": language_hint,
                        "full_transcript": "",
                        "words": [],
                        "cues": [],
                    }
                print(
                    f"[transcribe] WARNING: cloud transcription failed ({type(exc).__name__}); "
                    "falling back to local Gemini API key rotation.",
                    file=sys.stderr,
                )

        if not self.api_keys:
            return {
                "status": "cloud_failed",
                "model_used": "cloud_error",
                "error": last_error or "No Gemini API keys configured",
                "language": language_hint,
                "full_transcript": "",
                "words": [],
                "cues": [],
            }

        audio_bytes = wav_p.read_bytes()
        prompt = build_transcribe_prompt(target_script_lines, language_hint=language_hint)

        max_attempts = max(4, len(self.api_keys) * len(self.models))
        last_empty_res: Optional[Dict[str, Any]] = None
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
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
                data = self._parse_json_lenient(resp.text or "{}")
                res = self._heal_result(data, language_hint, model_used=model_name, clip_duration_sec=dur_sec)
                if res.get("words"):
                    self._save_cache(cache_file, wav_p, res)
                    return res
                last_empty_res = res
                self._rotate()
            except Exception as exc:
                err_str = str(exc)
                last_error = f"{type(exc).__name__}: {err_str[:250]}"
                self._rotate()
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "503" in err_str or "UNAVAILABLE" in err_str:
                    backoff = min(30.0, (2.0 ** min(attempt, 4)) + random.uniform(0.5, 2.5))
                    time.sleep(backoff)
                else:
                    time.sleep(0.2)

        if last_empty_res is not None and not last_error:
            return last_empty_res
        print(
            f"[transcribe] ERROR: Gemini failed on every attempt ({last_error}); "
            "refusing to fabricate forced-aligned cuts.",
            file=sys.stderr,
        )
        return {
            "status": "cloud_failed",
            "model_used": "cloud_error",
            "error": last_error or "Gemini transcription failed on every attempt",
            "language": language_hint,
            "full_transcript": "",
            "words": [],
            "cues": [],
        }

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
            }
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_name(cache_file.name + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, cache_file)
        except Exception:
            pass


MIN_SEND_SECONDS = 1.2


def _pad_short_wav(data: bytes) -> bytes:
    """Pad sub-second WAV buffers with trailing silence so cloud endpoints accept short utterances."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            params, frames = w.getparams(), w.readframes(w.getnframes())
        rate, width, ch = params.framerate, params.sampwidth, params.nchannels
        need = int(MIN_SEND_SECONDS * rate) - params.nframes
        if need <= 0:
            return data
        out = io.BytesIO()
        with wave.open(out, "wb") as o:
            o.setnchannels(ch)
            o.setsampwidth(width)
            o.setframerate(rate)
            o.writeframes(frames + b"\x00" * (need * width * ch))
        return out.getvalue()
    except Exception:
        return data


def transcribe_offline_fallback(
    wav_path: str,
    lang: str = "auto",
    script_lines: Optional[List[str]] = None,
    work_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Offline fallback using cached word files (`hq_words.json`/`words.json`) or single-line script text."""
    wav_p = Path(wav_path)
    wdir = Path(work_dir) if work_dir else wav_p.parent
    lang_code = normalize_lang_code(lang)
    if lang_code == "auto":
        lang_code = "kn" if any(re.search(r"[\u0C80-\u0CFF]", s) for s in (script_lines or [])) else "en"

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
                            w_txt = str(item.get("word") or item.get("w") or item.get("text") or "").strip()
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

    if script_lines:
        from .script_matcher import parse_target_script

        parsed = parse_target_script("\n".join(script_lines))
        if len(parsed) > 1:
            print(
                f"[transcribe] WARNING: cannot determine which of the {len(parsed)} script lines "
                f"{wav_p.name} speaks without transcription; leaving untranscribed.",
                file=sys.stderr,
            )
            return {
                "status": "no_transcript",
                "model_used": "none",
                "language": lang_code,
                "full_transcript": "",
                "words": [],
                "cues": [],
            }
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
