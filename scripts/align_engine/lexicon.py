"""
Kavioo 22-Language Modular Lexicon Engine & Dynamic SQLite Learning Database
Provides instant in-memory and SQLite-backed lookup for:
1. English Loanwords preservation in Indic speech (Tamil, Telugu, Malayalam, Kannada, Hindi, Bengali, etc.)
2. Agglutinative Grammatical Suffix handling (e.g. sample-ஆ, office-ல, teacher-తో, class-ൽ, button-ನಲ್ಲಿ)
3. Global Brand Protection (e.g. YouTube, Instagram, Premiere Pro, Supernova AI, Kavioo)
4. Dynamic User Auto-Learning ("Drain & Add" feedback loop)
"""

import os
import re
import json
import sqlite3
from typing import Dict, List, Optional, Tuple, Any

from .lexicons import ALL_LEXICONS

DB_PATH = os.path.join(os.path.dirname(__file__), "kavioo_lexicon.db")

# Standardized TitleCase mapping for English roots when joined with Indic grammatical suffixes
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

class KaviooLexiconEngine:
    """
    Modular 22-Language Lexicon Engine powered by native Python modules and lightweight SQLite storage.
    """
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.loanword_map: Dict[str, str] = {}
        self.lang_loanword_map: Dict[str, Dict[str, str]] = {}
        self.suffix_map: Dict[str, List[str]] = {}
        self.strict_native_lock: Dict[str, set] = {}
        self.json_db_path = os.path.join(os.path.dirname(__file__), "data", "lexicon_db.json")
        self._init_db()
        self.load_cache()

    def _init_db(self):
        """Initializes the lightweight SQLite DB for user corrections & dynamic learning."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS language_loanwords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language_code TEXT NOT NULL,
                native_transliteration TEXT NOT NULL,
                standard_english TEXT NOT NULL,
                category TEXT DEFAULT 'loanword',
                is_active INTEGER DEFAULT 1,
                UNIQUE(language_code, native_transliteration)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS language_suffixes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language_code TEXT NOT NULL,
                suffix_indic TEXT NOT NULL,
                UNIQUE(language_code, suffix_indic)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_loanwords_native ON language_loanwords(native_transliteration)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_loanwords_lang ON language_loanwords(language_code)")

        # Seed pre-compiled Python modules into database
        all_valid_keys = set()
        for lang_code, pack in ALL_LEXICONS.items():
            for suff in pack.get("suffixes", []):
                cur.execute(
                    "INSERT OR IGNORE INTO language_suffixes (language_code, suffix_indic) VALUES (?, ?)",
                    (lang_code, suff.strip())
                )
            for native, eng in pack.get("loanwords", {}).items():
                n_clean = native.strip()
                cur.execute(
                    "INSERT OR REPLACE INTO language_loanwords (language_code, native_transliteration, standard_english, category) VALUES (?, ?, ?, 'loanword')",
                    (lang_code, n_clean, eng.strip())
                )
                all_valid_keys.add((lang_code, n_clean))

        # Purge stale seeded loanwords that were removed from Python modules
        # (e.g. abbreviation expansions like 'ai' → 'artificial intelligence')
        # Only purge 'loanword' category rows — user_correction rows are untouched.
        cur.execute("SELECT language_code, native_transliteration FROM language_loanwords WHERE category = 'loanword'")
        for row_lang, row_native in cur.fetchall():
            if (row_lang, row_native) not in all_valid_keys:
                cur.execute(
                    "DELETE FROM language_loanwords WHERE language_code = ? AND native_transliteration = ? AND category = 'loanword'",
                    (row_lang, row_native)
                )

        conn.commit()
        conn.close()


    def load_cache(self):
        """Loads all native Python dictionaries/JSON DB + dynamic SQLite corrections into ultra-fast RAM cache."""
        self.loanword_map = {}
        self.lang_loanword_map = {}
        self.suffix_map = {}
        self.strict_native_lock = {}

        # 1. Load pre-compiled language packs (from JSON if available, fallback to native modules)
        if os.path.exists(self.json_db_path):
            try:
                with open(self.json_db_path, 'r', encoding='utf-8') as f:
                    db_data = json.load(f)
                
                # Optionally load CANONICAL_ENGLISH_STEMS from JSON if needed, but we keep the static one too.
                # For languages:
                for lang_code, data in db_data.get("languages", {}).items():
                    if lang_code not in self.lang_loanword_map:
                        self.lang_loanword_map[lang_code] = {}
                    if lang_code not in self.suffix_map:
                        self.suffix_map[lang_code] = []
                    if lang_code not in self.strict_native_lock:
                        self.strict_native_lock[lang_code] = set()
                    
                    self.suffix_map[lang_code].extend(data.get("suffixes", []))
                    
                    for native, eng in data.get("loanwords", {}).items():
                        n_clean, e_clean = native.strip(), eng.strip()
                        self.loanword_map[n_clean] = e_clean
                        self.lang_loanword_map[lang_code][n_clean] = e_clean
                    
                    for lock_word in data.get("strict_native_lock", []):
                        self.strict_native_lock[lang_code].add(lock_word.strip())
            except Exception as e:
                print(f"[Lexicon Engine] Error loading JSON DB: {e}")
                self._load_fallback_modules()
        else:
            self._load_fallback_modules()

        # 2. Merge dynamic user-learned words from SQLite
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT language_code, native_transliteration, standard_english FROM language_loanwords WHERE is_active = 1")
            for lang, native, eng in cur.fetchall():
                n_clean = native.strip()
                e_clean = eng.strip()
                self.loanword_map[n_clean] = e_clean
                if lang not in self.lang_loanword_map:
                    self.lang_loanword_map[lang] = {}
                self.lang_loanword_map[lang][n_clean] = e_clean
            conn.close()
        except Exception as e:
            print(f"[Lexicon Engine] Warning reading custom learned words: {e}")

    def _load_fallback_modules(self):
        """Fallback to native Python modules if JSON DB is not present."""
        for lang_code, pack in ALL_LEXICONS.items():
            if lang_code not in self.lang_loanword_map:
                self.lang_loanword_map[lang_code] = {}
            if lang_code not in self.suffix_map:
                self.suffix_map[lang_code] = []
            if lang_code not in self.strict_native_lock:
                self.strict_native_lock[lang_code] = set()

            for suff in pack.get("suffixes", []):
                self.suffix_map[lang_code].append(suff.strip())

            for native, eng in pack.get("loanwords", {}).items():
                n_clean = native.strip()
                e_clean = eng.strip()
                self.loanword_map[n_clean] = e_clean
                self.lang_loanword_map[lang_code][n_clean] = e_clean

    def learn_word(self, language_code: str, native_word: str, english_word: str, category: str = "user_correction") -> bool:
        """
        Dynamically learns/updates a new loanword or user correction in real-time.
        Immediately updates both SQLite DB and in-memory cache.
        """
        if not native_word or not english_word:
            return False
            
        n_clean = native_word.strip()
        e_clean = english_word.strip()
        lang = language_code.strip() if language_code else "global"

        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO language_loanwords (language_code, native_transliteration, standard_english, category, is_active) VALUES (?, ?, ?, ?, 1)",
                (lang, n_clean, e_clean, category)
            )
            conn.commit()
            conn.close()

            # Update in-memory cache
            self.loanword_map[n_clean] = e_clean
            if lang not in self.lang_loanword_map:
                self.lang_loanword_map[lang] = {}
            self.lang_loanword_map[lang][n_clean] = e_clean
            return True
        except Exception as e:
            print(f"[Lexicon Engine] Error learning word: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Returns statistics of all loaded languages, total words, and suffix count."""
        return {
            "total_cached_words": len(self.loanword_map),
            "languages_count": len(self.lang_loanword_map),
            "breakdown_by_language": {lang: len(words) for lang, words in self.lang_loanword_map.items()},
            "suffixes_by_language": {lang: len(suffs) for lang, suffs in self.suffix_map.items()}
        }

    @staticmethod
    def resolve_language_code(lang_str: Optional[str]) -> str:
        """
        Normalizes a language identifier (label or code, e.g. 'Tamil', 'ta', 'Hindi', 'hi')
        into an ISO 639-1 code used by the lexicon engine.
        """
        if not lang_str:
            return "global"
        s = lang_str.strip().lower()
        LABEL_TO_CODE = {
            "tamil": "ta", "telugu": "te", "hindi": "hi", "kannada": "kn",
            "malayalam": "ml", "bengali": "bn", "marathi": "mr", "gujarati": "gu",
            "punjabi": "pa", "odia": "or", "assamese": "as", "urdu": "ur",
            "sanskrit": "sa", "english": "en", "japanese": "ja", "german": "de",
            "french": "fr", "spanish": "es", "portuguese": "pt", "korean": "ko",
            "indonesian": "id", "arabic": "ar", "global": "global",
        }
        return LABEL_TO_CODE.get(s, s)

    def sanitize_token(self, word: str, language_code: Optional[str] = None) -> Optional[str]:
        """
        Cleans and normalizes a token:
        1. Strips corrupted unicode diacritics / unicode escapes.
        2. Filters out isolated non-word single Latin letters.
        3. Normalizes English loanwords and suffixes (e.g. 'ஸ்டுடென்ட்ஸ்' -> 'students', 'sample-ஆ').
        """
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
            raw = raw[len(punct_prefix):]
        m_suf = re.search(r"([.,?!:;\"')\]\-]+)$", raw)
        if m_suf:
            punct_suffix = m_suf.group(1)
            raw = raw[:-len(punct_suffix)]

        code = self.resolve_language_code(language_code)
        UNICODE_RANGES = {
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
        lang_range = UNICODE_RANGES.get(code, r"\u0600-\u0D7F\u00C0-\u024F")
        raw = re.sub(rf"[^{lang_range}a-zA-Z0-9\s.,?!:;\x22\x27\(\)\[\]\-]+", "", raw).strip()

        if not raw:
            return None

        if len(raw) == 1 and raw.isascii() and raw.isalpha() and raw.lower() not in {"i", "a", "o"}:
            return None

        norm = self.normalize_word(raw, code)
        return f"{punct_prefix}{norm}{punct_suffix}"

    @staticmethod
    def _generate_tamil_stem_candidates(stem: str) -> List[str]:
        """
        Generates candidate morphological stems in Tamil by reversing euphonic glides (sandhi),
        doubled plosives, and epenthetic u (ுகரம்) transformations.
        """
        cands = [stem]
        if stem.endswith("வு"):
            cands.append(stem[:-len("வு")])
        if stem.endswith("த்து"):
            cands.append(stem[:-len("த்து")] + "ம்")
            cands.append(stem[:-len("த்து")])
        if stem.endswith("ட்டு"):
            cands.append(stem[:-len("ட்டு")] + "ட்")
        if stem.endswith("க்கு"):
            cands.append(stem[:-len("க்கு")] + "க்")
        if stem.endswith("ப்பு"):
            cands.append(stem[:-len("ப்பு")] + "ப்")
        if stem.endswith("ண்டு"):
            cands.append(stem[:-len("ண்டு")] + "ண்ட்")
            cands.append(stem[:-len("ண்டு")] + "ண்")

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
                cands.append(stem[:-len(ending)] + replacement)

        return cands

    def normalize_word(self, word: str, language_code: Optional[str] = None) -> str:
        """
        Looks up word in loanwords dictionary. If found, returns standard English word.
        Handles attached suffixes (e.g. 'Video-க்கு', 'Instagram-ல', 'Content-ஐ', 'சாம்பிளா' -> 'sample-ஆ').
        """
        raw = word.strip()
        if not raw:
            return raw

        code = self.resolve_language_code(language_code)

        # STRICT NATIVE LOCK GUARDRAIL
        # If this exact word (or its root stem) is locked, do not translate it to English.
        if code in self.strict_native_lock and raw in self.strict_native_lock[code]:
            return raw
        
        # Check if root stem is locked before processing hyphen
        if "-" in raw:
            root_check = raw.split("-")[0].strip()
            if code in self.strict_native_lock and root_check in self.strict_native_lock[code]:
                return raw

        def _format_loanword_val(val: str) -> str:
            if "-" in val:
                stem_e, suff_e = val.split("-", 1)
                stem_c = CANONICAL_ENGLISH_STEMS.get(stem_e.lower(), stem_e)
                norm_s = TAMIL_SUFFIX_NORM.get(suff_e, suff_e) if (code == "ta" or not code or code == "global") else suff_e
                return f"{stem_c}-{norm_s}"
            return val

        # 0. Check hyphenated Indic tokens (e.g. "கண்டென்ட்-க்கு", "వీడియో-కి")
        if "-" in raw:
            parts = raw.split("-", 1)
            stem_part, suff_part = parts[0].strip(), parts[1].strip()
            norm_suff = TAMIL_SUFFIX_NORM.get(suff_part, suff_part) if (code == "ta" or not code or code == "global") else suff_part
            candidates = self._generate_tamil_stem_candidates(stem_part) if (code == "ta" or not code or code == "global") else [stem_part]
            for cand in candidates:
                eng = None
                if code in self.lang_loanword_map and cand in self.lang_loanword_map[code]:
                    eng = self.lang_loanword_map[code][cand]
                elif cand in self.loanword_map:
                    eng = self.loanword_map[cand]
                if eng:
                    canonical = CANONICAL_ENGLISH_STEMS.get(eng.lower(), eng)
                    return f"{canonical}-{norm_suff}"

        # 1. Check mixed Latin-Indic or Latin-hyphen-Indic tokens (e.g. "Videoக்கு", "video-కి", "Instagramలో", "Contentடை")
        indic_regex = r"^([A-Za-z0-9_]+)-?([\u0B80-\u0BFF]+)$" if (code == "ta" or not code or code == "global") else r"^([A-Za-z0-9_]+)-?([\u0600-\u0D7F]+)$"
        m_mixed = re.match(indic_regex, raw)
        if m_mixed:
            eng_part = m_mixed.group(1)
            indic_part = m_mixed.group(2)
            eng_norm = CANONICAL_ENGLISH_STEMS.get(eng_part.lower(), eng_part)
            norm_suff = TAMIL_SUFFIX_NORM.get(indic_part, indic_part) if (code == "ta" or not code or code == "global") else indic_part
            return f"{eng_norm}-{norm_suff}"

        # 2. Language specific check first
        if code and code in self.lang_loanword_map:
            if raw in self.lang_loanword_map[code]:
                return _format_loanword_val(self.lang_loanword_map[code][raw])

        # 3. Global direct match
        if raw in self.loanword_map:
            return _format_loanword_val(self.loanword_map[raw])

        # 4. Case-insensitive check
        raw_lower = raw.lower()
        for k, v in self.loanword_map.items():
            if k.lower() == raw_lower:
                return _format_loanword_val(v)

        # 5. Special sandhi combinations for Tamil accusative (e.g. கண்டென்ட்டை -> Content-ஐ)
        if (code == "ta" or not code or code == "global") and raw.endswith("ட்டை") and len(raw) > len("ட்டை"):
            cand = raw[:-len("ட்டை")] + "ட்"
            eng = None
            if code in self.lang_loanword_map and cand in self.lang_loanword_map[code]:
                eng = self.lang_loanword_map[code][cand]
            elif cand in self.loanword_map:
                eng = self.loanword_map[cand]
            if eng:
                canonical = CANONICAL_ENGLISH_STEMS.get(eng.lower(), eng)
                return f"{canonical}-ஐ"

        # 6. Suffix attachment check with morphological stemming
        if code and code in self.suffix_map:
            suffixes = sorted(self.suffix_map[code], key=len, reverse=True)
            for suff in suffixes:
                clean_suff = suff.lstrip("-")
                if raw.endswith(clean_suff) and len(raw) > len(clean_suff):
                    stem = raw[:-len(clean_suff)]
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
        """
        Normalizes and sanitizes all word tokens in subtitle cues through the SQLite lexicon.
        """
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
                            norm_cw.append({"word": clean_w, "start": cue.get("start", 0.0), "end": cue.get("end", 0.0)})

            if not norm_cw:
                continue

            cue_copy = dict(cue)
            cue_copy["words"] = norm_cw
            cue_copy["text"] = " ".join(cw["word"] for cw in norm_cw)
            cleaned_cues.append(cue_copy)

        return cleaned_cues

# Global Singleton instance
lexicon_engine = KaviooLexiconEngine()
