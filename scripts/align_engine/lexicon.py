"""22-language loanword lexicon and Indic morphological suffix normalizer.

Provides fast in-memory lookup with optional SQLite persistence for learned corrections:
1. Preserves English loanwords spoken inside Indic dialogue (Tamil, Telugu, Malayalam, Kannada, Hindi, etc.).
2. Decouples agglutinative grammatical suffixes (e.g. `sample-ஆ`, `office-ல`, `teacher-తో`, `class-ൽ`, `button-ನಲ್ಲಿ`).
3. Protects product and brand names (e.g. YouTube, Instagram, Premiere Pro, Supernova AI).
4. Supports dynamic user-correction learning via SQLite (`work/_lexicon.db`).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = str(_PROJECT_ROOT / "work" / "_lexicon.db")

try:
    from .lexicons import ALL_LEXICONS
except ImportError:
    ALL_LEXICONS = {}
    _local_lex_dir = Path(__file__).resolve().parent / "lexicons"
    if _local_lex_dir.is_dir():
        import types as _types

        for _lf in sorted(_local_lex_dir.glob("*.py")):
            if _lf.name == "__init__.py":
                continue
            try:
                _lmod = _types.ModuleType(f"align_engine._lex_{_lf.stem}")
                exec(compile(_lf.read_text(encoding="utf-8"), str(_lf), "exec"), _lmod.__dict__)
                _lcode = getattr(_lmod, "LANGUAGE_CODE", None)
                if _lcode:
                    ALL_LEXICONS[str(_lcode)] = {
                        "code": _lcode,
                        "suffixes": getattr(_lmod, "SUFFIXES", []),
                        "loanwords": getattr(_lmod, "LOANWORDS", {}),
                    }
            except Exception:
                continue

CANONICAL_ENGLISH_STEMS: Dict[str, str] = {
    "video": "Video",
    "videos": "Videos",
    "audio": "Audio",
    "audios": "Audios",
    "content": "Content",
    "contents": "Contents",
    "subtitle": "Subtitle",
    "subtitles": "Subtitles",
    "caption": "Caption",
    "captions": "Captions",
    "reel": "Reel",
    "reels": "Reels",
    "channel": "Channel",
    "channels": "Channels",
    "creator": "Creator",
    "creators": "Creators",
    "audience": "Audience",
    "audiences": "Audiences",
    "user": "User",
    "users": "Users",
    "customer": "Customer",
    "customers": "Customers",
    "brand": "Brand",
    "brands": "Brands",
    "business": "Business",
    "businesses": "Businesses",
    "website": "Website",
    "websites": "Websites",
    "app": "App",
    "apps": "Apps",
    "tool": "Tool",
    "tools": "Tools",
    "software": "Software",
    "softwares": "Softwares",
    "editing": "Editing",
    "marketing": "Marketing",
    "youtube": "YouTube",
    "instagram": "Instagram",
    "insta": "Instagram",
    "time": "Time",
    "times": "Times",
    "work": "Work",
    "works": "Works",
    "problem": "Problem",
    "problems": "Problems",
    "solution": "Solution",
    "solutions": "Solutions",
    "idea": "Idea",
    "ideas": "Ideas",
    "result": "Result",
    "results": "Results",
    "quality": "Quality",
    "qualities": "Qualities",
    "accuracy": "Accuracy",
}

TAMIL_SUFFIX_NORM: Dict[str, str] = {
    "க்கு": "க்கு",
    "வுக்கு": "வுக்கு",
    "கு": "க்கு",
    "ல": "ல",
    "லேயே": "லேயே",
    "வை": "வை",
    "யை": "வை",
    "ஐ": "ஐ",
    "டை": "ஐ",
    "ோட": "ஓட",
    "வோட": "வோட",
    "யோட": "ஓட",
    "ஓட": "ஓட",
    "லோட": "ஓட",
    "கிட்ட": "கிட்ட",
    "ட்ட": "கிட்ட",
    "தான்": "தான்",
    "க்காக": "க்காக",
    "லாம்": "லாம்",
    "ஆ": "ஆ",
    "வா": "வா",
    "டா": "டா",
    "டி": "டி",
    "ங்க": "ங்க",
    "ங்களா": "ங்களா",
    "னு": "னு",
    "ன்னு": "ன்னு",
}


class ZenoLexiconEngine:
    """Modular 22-language loanword and suffix lexicon engine."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.loanword_map: Dict[str, str] = {}
        self.lang_loanword_map: Dict[str, Dict[str, str]] = {}
        self.suffix_map: Dict[str, List[str]] = {}
        self.strict_native_lock: Dict[str, set] = {}
        self.json_db_path = os.path.join(os.path.dirname(__file__), "data", "lexicon_db.json")
        self._db_initialized = False
        if db_path != DB_PATH:
            self._init_db()
        self.load_cache()

    def _init_db(self) -> None:
        """Initialize the SQLite database used for dynamic user-learned loanwords."""
        if self._db_initialized:
            return
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_file))
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS language_loanwords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language_code TEXT NOT NULL,
                native_transliteration TEXT NOT NULL,
                standard_english TEXT NOT NULL,
                category TEXT DEFAULT 'loanword',
                is_active INTEGER DEFAULT 1,
                UNIQUE(language_code, native_transliteration)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS language_suffixes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language_code TEXT NOT NULL,
                suffix_indic TEXT NOT NULL,
                UNIQUE(language_code, suffix_indic)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_loanwords_native ON language_loanwords(native_transliteration)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_loanwords_lang ON language_loanwords(language_code)")
        conn.commit()
        conn.close()
        self._db_initialized = True

    def load_cache(self) -> None:
        """Load static language packs (JSON and Python modules) plus any learned SQLite entries into RAM."""
        self.loanword_map = {}
        self.lang_loanword_map = {}
        self.suffix_map = {}
        self.strict_native_lock = {}

        # 1. Load static Python lexicon modules first as base layer
        self._load_fallback_modules()

        # 2. Overlay JSON lexicon database if present
        if os.path.exists(self.json_db_path):
            try:
                with open(self.json_db_path, "r", encoding="utf-8") as f:
                    db_data = json.load(f)
                for lang_code, data in db_data.get("languages", {}).items():
                    self.lang_loanword_map.setdefault(lang_code, {})
                    self.suffix_map.setdefault(lang_code, [])
                    self.strict_native_lock.setdefault(lang_code, set())

                    existing_suffs = set(self.suffix_map[lang_code])
                    for suff in data.get("suffixes", []):
                        s_clean = suff.strip()
                        if s_clean not in existing_suffs:
                            self.suffix_map[lang_code].append(s_clean)
                            existing_suffs.add(s_clean)

                    for native, eng in data.get("loanwords", {}).items():
                        n_clean, e_clean = native.strip(), eng.strip()
                        self.loanword_map[n_clean] = e_clean
                        self.lang_loanword_map[lang_code][n_clean] = e_clean

                    for lock_word in data.get("strict_native_lock", []):
                        self.strict_native_lock[lang_code].add(lock_word.strip())
            except Exception as exc:
                print(f"[Lexicon Engine] Warning loading JSON lexicon DB: {exc}")

        # 3. Merge dynamic user-learned words from SQLite if present on disk
        if os.path.isfile(self.db_path):
            try:
                conn = sqlite3.connect(self.db_path)
                cur = conn.cursor()
                cur.execute(
                    "SELECT language_code, native_transliteration, standard_english "
                    "FROM language_loanwords WHERE is_active = 1"
                )
                for lang, native, eng in cur.fetchall():
                    n_clean = native.strip()
                    e_clean = eng.strip()
                    self.loanword_map[n_clean] = e_clean
                    self.lang_loanword_map.setdefault(lang, {})[n_clean] = e_clean
                conn.close()
            except Exception as exc:
                print(f"[Lexicon Engine] Warning reading learned words: {exc}")

    def _load_fallback_modules(self) -> None:
        """Load compiled Python lexicon dictionaries from `align_engine.lexicons`."""
        for lang_code, pack in ALL_LEXICONS.items():
            self.lang_loanword_map.setdefault(lang_code, {})
            self.suffix_map.setdefault(lang_code, [])
            self.strict_native_lock.setdefault(lang_code, set())

            for suff in pack.get("suffixes", []):
                self.suffix_map[lang_code].append(suff.strip())

            for native, eng in pack.get("loanwords", {}).items():
                n_clean = native.strip()
                e_clean = eng.strip()
                self.loanword_map[n_clean] = e_clean
                self.lang_loanword_map[lang_code][n_clean] = e_clean

    def learn_word(
        self,
        language_code: str,
        native_word: str,
        english_word: str,
        category: str = "user_correction",
    ) -> bool:
        """Persist a new loanword or user correction to SQLite and update the in-memory cache."""
        if not native_word or not english_word:
            return False

        n_clean = native_word.strip()
        e_clean = english_word.strip()
        lang = language_code.strip() if language_code else "global"

        try:
            self._init_db()
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO language_loanwords "
                "(language_code, native_transliteration, standard_english, category, is_active) "
                "VALUES (?, ?, ?, ?, 1)",
                (lang, n_clean, e_clean, category),
            )
            conn.commit()
            conn.close()

            self.loanword_map[n_clean] = e_clean
            self.lang_loanword_map.setdefault(lang, {})[n_clean] = e_clean
            return True
        except Exception as exc:
            print(f"[Lexicon Engine] Error learning word: {exc}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Return counts of cached words, languages, and suffixes."""
        return {
            "total_cached_words": len(self.loanword_map),
            "languages_count": len(self.lang_loanword_map),
            "breakdown_by_language": {lang: len(words) for lang, words in self.lang_loanword_map.items()},
            "suffixes_by_language": {lang: len(suffs) for lang, suffs in self.suffix_map.items()},
        }

    @staticmethod
    def resolve_language_code(lang_str: Optional[str]) -> str:
        """Normalize a language label or code (e.g. `'Tamil'`, `'ta'`) into an ISO 639-1 code."""
        if not lang_str:
            return "global"
        s = lang_str.strip().lower()
        label_to_code = {
            "tamil": "ta",
            "telugu": "te",
            "hindi": "hi",
            "kannada": "kn",
            "malayalam": "ml",
            "bengali": "bn",
            "marathi": "mr",
            "gujarati": "gu",
            "punjabi": "pa",
            "odia": "or",
            "assamese": "as",
            "urdu": "ur",
            "sanskrit": "sa",
            "english": "en",
            "japanese": "ja",
            "german": "de",
            "french": "fr",
            "spanish": "es",
            "portuguese": "pt",
            "korean": "ko",
            "indonesian": "id",
            "arabic": "ar",
            "global": "global",
        }
        return label_to_code.get(s, s)

    def sanitize_token(self, word: str, language_code: Optional[str] = None) -> Optional[str]:
        """Clean corrupted Unicode escapes, strip single-letter ASCII noise, and normalize loanwords."""
        if not word:
            return None
        raw = str(word).strip()
        raw = re.sub(r"\\[uU][0-9a-fA-F]{4}[_]?", "", raw)
        raw = re.sub(r"[\u200B-\u200D\uFEFF]", "", raw)

        punct_prefix = ""
        punct_suffix = ""
        m_pre = re.match(r"^([.,?!:;\"'(\[\-]+)", raw)
        if m_pre:
            punct_prefix = m_pre.group(1)
            raw = raw[len(punct_prefix) :]
        m_suf = re.search(r"([.,?!:;\"')\]\-]+)$", raw)
        if m_suf:
            punct_suffix = m_suf.group(1)
            raw = raw[: -len(punct_suffix)]

        code = self.resolve_language_code(language_code)
        unicode_ranges = {
            "ta": r"\u0B80-\u0BFF",
            "te": r"\u0C00-\u0C7F",
            "kn": r"\u0C80-\u0CFF",
            "ml": r"\u0D00-\u0D7F",
            "hi": r"\u0900-\u097F",
            "mr": r"\u0900-\u097F",
            "bn": r"\u0980-\u09FF",
            "gu": r"\u0A80-\u0AFF",
            "pa": r"\u0A00-\u0A7F",
            "or": r"\u0B00-\u0B7F",
            "as": r"\u0980-\u09FF",
            "ur": r"\u0600-\u06FF",
            "sa": r"\u0900-\u097F",
            "ar": r"\u0600-\u06FF",
            "ja": r"\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF",
            "ko": r"\uAC00-\uD7AF\u1100-\u11FF",
            "de": r"\u00C0-\u024F",
            "fr": r"\u00C0-\u024F",
            "es": r"\u00C0-\u024F",
            "pt": r"\u00C0-\u024F",
            "id": r"\u00C0-\u024F",
            "en": r"\u00C0-\u024F",
        }
        lang_range = unicode_ranges.get(code, r"\u0600-\u0D7F\u00C0-\u024F")
        raw = re.sub(rf"[^{lang_range}a-zA-Z0-9\s.,?!:;\x22\x27\(\)\[\]\-]+", "", raw).strip()

        if not raw:
            return None

        if len(raw) == 1 and raw.isascii() and raw.isalpha() and raw.lower() not in {"i", "a", "o"}:
            return None

        norm = self.normalize_word(raw, code)
        return f"{punct_prefix}{norm}{punct_suffix}"

    @staticmethod
    def _generate_tamil_stem_candidates(stem: str) -> List[str]:
        """Generate candidate Tamil stems by reversing sandhi glides, doubled plosives, and epenthetic u."""
        cands = [stem]
        if stem.endswith("வு"):
            cands.append(stem[: -len("வு")])
        if stem.endswith("த்து"):
            cands.append(stem[: -len("த்து")] + "ம்")
            cands.append(stem[: -len("த்து")])
        if stem.endswith("ட்டு"):
            cands.append(stem[: -len("ட்டு")] + "ட்")
        if stem.endswith("க்கு"):
            cands.append(stem[: -len("க்கு")] + "க்")
        if stem.endswith("ப்பு"):
            cands.append(stem[: -len("ப்பு")] + "ப்")
        if stem.endswith("ண்டு"):
            cands.append(stem[: -len("ண்டு")] + "ண்ட்")
            cands.append(stem[: -len("ண்டு")] + "ண்")

        substitutions = [
            ("லு", "ல்"),
            ("ரு", "ர்"),
            ("சு", "ஸ்"),
            ("ஸு", "ஸ்"),
            ("மு", "ம்"),
            ("பு", "ப்"),
            ("கு", "க்"),
            ("ங்கு", "ங்"),
            ("னு", "ன்"),
            ("டு", "ட்"),
            ("ல", "ல்"),
            ("ர", "ர்"),
            ("வ", ""),
        ]
        for ending, replacement in substitutions:
            if stem.endswith(ending):
                cands.append(stem[: -len(ending)] + replacement)

        return cands

    def normalize_word(self, word: str, language_code: Optional[str] = None) -> str:
        """Resolve transliterated loanwords to English and format attached Indic grammatical suffixes."""
        raw = word.strip()
        if not raw:
            return raw

        code = self.resolve_language_code(language_code)

        if code in self.strict_native_lock and raw in self.strict_native_lock[code]:
            return raw

        if "-" in raw:
            root_check = raw.split("-")[0].strip()
            if code in self.strict_native_lock and root_check in self.strict_native_lock[code]:
                return raw

        def _format_loanword_val(val: str) -> str:
            if "-" in val:
                stem_e, suff_e = val.split("-", 1)
                stem_c = CANONICAL_ENGLISH_STEMS.get(stem_e.lower(), stem_e)
                norm_s = (
                    TAMIL_SUFFIX_NORM.get(suff_e, suff_e)
                    if (code == "ta" or not code or code == "global")
                    else suff_e
                )
                return f"{stem_c}-{norm_s}"
            return val

        # 0. Hyphenated Indic tokens (e.g. "கண்டென்ட்-க்கு", "వీడియో-కి")
        if "-" in raw:
            parts = raw.split("-", 1)
            stem_part, suff_part = parts[0].strip(), parts[1].strip()
            norm_suff = (
                TAMIL_SUFFIX_NORM.get(suff_part, suff_part)
                if (code == "ta" or not code or code == "global")
                else suff_part
            )
            candidates = (
                self._generate_tamil_stem_candidates(stem_part)
                if (code == "ta" or not code or code == "global")
                else [stem_part]
            )
            for cand in candidates:
                eng = None
                if code in self.lang_loanword_map and cand in self.lang_loanword_map[code]:
                    eng = self.lang_loanword_map[code][cand]
                elif cand in self.loanword_map:
                    eng = self.loanword_map[cand]
                if eng:
                    canonical = CANONICAL_ENGLISH_STEMS.get(eng.lower(), eng)
                    return f"{canonical}-{norm_suff}"

        # 1. Mixed Latin-Indic compounds (e.g. "Videoக்கு", "video-కి", "Instagramలో")
        indic_regex = (
            r"^([A-Za-z0-9_]+)-?([\u0B80-\u0BFF]+)$"
            if (code == "ta" or not code or code == "global")
            else r"^([A-Za-z0-9_]+)-?([\u0600-\u0D7F]+)$"
        )
        m_mixed = re.match(indic_regex, raw)
        if m_mixed:
            eng_part = m_mixed.group(1)
            indic_part = m_mixed.group(2)
            eng_norm = CANONICAL_ENGLISH_STEMS.get(eng_part.lower(), eng_part)
            norm_suff = (
                TAMIL_SUFFIX_NORM.get(indic_part, indic_part)
                if (code == "ta" or not code or code == "global")
                else indic_part
            )
            return f"{eng_norm}-{norm_suff}"

        # 2. Language-specific exact match
        if code and code in self.lang_loanword_map and raw in self.lang_loanword_map[code]:
            return _format_loanword_val(self.lang_loanword_map[code][raw])

        # 3. Global exact match
        if raw in self.loanword_map:
            return _format_loanword_val(self.loanword_map[raw])

        # 4. Case-insensitive match
        raw_lower = raw.lower()
        for k, v in self.loanword_map.items():
            if k.lower() == raw_lower:
                return _format_loanword_val(v)

        # 5. Tamil accusative sandhi ("கண்டென்ட்டை" -> "Content-ஐ")
        if (code == "ta" or not code or code == "global") and raw.endswith("ட்டை") and len(raw) > len("ட்டை"):
            cand = raw[: -len("ட்டை")] + "ட்"
            eng = None
            if code in self.lang_loanword_map and cand in self.lang_loanword_map[code]:
                eng = self.lang_loanword_map[code][cand]
            elif cand in self.loanword_map:
                eng = self.loanword_map[cand]
            if eng:
                canonical = CANONICAL_ENGLISH_STEMS.get(eng.lower(), eng)
                return f"{canonical}-ஐ"

        # 6. Suffix detachment with morphological stemming
        if code and code in self.suffix_map:
            suffixes = sorted(self.suffix_map[code], key=len, reverse=True)
            for suff in suffixes:
                clean_suff = suff.lstrip("-")
                if raw.endswith(clean_suff) and len(raw) > len(clean_suff):
                    stem = raw[: -len(clean_suff)]
                    candidates = self._generate_tamil_stem_candidates(stem) if code == "ta" else [stem]
                    for cand in candidates:
                        eng = None
                        if code in self.lang_loanword_map and cand in self.lang_loanword_map[code]:
                            eng = self.lang_loanword_map[code][cand]
                        elif cand in self.loanword_map:
                            eng = self.loanword_map[cand]

                        if eng:
                            canonical = CANONICAL_ENGLISH_STEMS.get(eng.lower(), eng)
                            if canonical == "Idea" and clean_suff in ("வுக்கு", "க்கு"):
                                return "Idea-க்கு"
                            norm_suff = TAMIL_SUFFIX_NORM.get(clean_suff, clean_suff) if code == "ta" else clean_suff
                            return f"{canonical}-{norm_suff}"

        return raw

    def process_cues(self, cues: List[Dict[str, Any]], language_code: Optional[str] = None) -> List[Dict[str, Any]]:
        """Sanitize and normalize all word tokens inside a list of subtitle cues."""
        cleaned_cues = []
        for cue in cues:
            norm_cw = []
            if "words" in cue and isinstance(cue["words"], list):
                for w in cue["words"]:
                    if isinstance(w, dict) and "word" in w:
                        clean_w = self.sanitize_token(w["word"], language_code)
                        if clean_w:
                            w_copy = dict(w)
                            w_copy["word"] = clean_w
                            norm_cw.append(w_copy)
                    elif isinstance(w, str):
                        clean_w = self.sanitize_token(w, language_code)
                        if clean_w:
                            norm_cw.append(
                                {"word": clean_w, "start": cue.get("start", 0.0), "end": cue.get("end", 0.0)}
                            )

            if not norm_cw:
                continue

            cue_copy = dict(cue)
            cue_copy["words"] = norm_cw
            cue_copy["text"] = " ".join(cw["word"] for cw in norm_cw)
            cleaned_cues.append(cue_copy)

        return cleaned_cues


KaviooLexiconEngine = ZenoLexiconEngine
lexicon_engine = ZenoLexiconEngine()
