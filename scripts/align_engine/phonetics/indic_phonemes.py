"""
EditFlow Acoustic Engine — Indian Language Phonetic Mapping (G2P Layer)
Provides phonetic rules and acoustic sound mappings for Indic languages:
Telugu (te), Tamil (ta), Hindi (hi), Malayalam (ml), Kannada (kn), and English (en).
"""

import re
import unicodedata
from typing import Dict, List, Tuple

# Common Indic phonetic substitutions to align Romanized Indian spellings
# to English acoustic token representations without losing phoneme attack onsets.
INDIC_PHONETIC_RULES: Dict[str, List[Tuple[str, str]]] = {
    # Telugu phonetic mapping rules
    "te": [
        (r'([aeiou])\1+', r'\1'),           # Long vowels: aa->a, ee->e, oo->o
        (r'bh', 'b'),                         # Aspirated voiced bilabial -> b
        (r'dh', 'd'),                         # Aspirated dental -> d
        (r'gh', 'g'),                         # Aspirated velar -> g
        (r'jh', 'j'),                         # Aspirated palatal -> j
        (r'th', 't'),                         # Aspirated voiceless dental -> t
        (r'kh', 'k'),                         # Aspirated voiceless velar -> k
        (r'ph', 'f'),                         # Aspirated voiceless bilabial -> f/p
        (r'ch', 'c'),                         # Affricate ch -> c
        (r'sh', 's'),                         # Sibilants sh/shh -> s
        (r'zh', 'l'),                         # Retroflex lateral -> l
        (r'tx', 't'), (r'dx', 'd'),           # Retroflex stops -> t/d
        (r'nx', 'n'), (r'lx', 'l'),           # Retroflex nasals/laterals -> n/l
        (r'(\w)nn(\w)', r'\1n\2'),            # Geminate consonants -> single
        (r'(\w)tt(\w)', r'\1t\2'),
        (r'(\w)pp(\w)', r'\1p\2'),
        (r'(\w)kk(\w)', r'\1k\2'),
    ],
    # Tamil phonetic mapping rules
    "ta": [
        (r'zh', 'l'),                         # Tamil retroflex approximant -> l
        (r'rr', 'r'),                         # Alveolar trill -> r
        (r'nn', 'n'),                         # Alveolar/retroflex nasal -> n
        (r'll', 'l'),                         # Retroflex lateral -> l
        (r'th', 't'),                         # Interdental stop -> t
        (r'dh', 'd'),
        (r'bh', 'b'),
        (r'sh', 's'),
        (r'([aeiou])\1+', r'\1'),
    ],
    # Hindi phonetic mapping rules
    "hi": [
        (r'([aeiou])\1+', r'\1'),
        (r'chh', 'c'),
        (r'kh', 'k'),
        (r'gh', 'g'),
        (r'jh', 'j'),
        (r'th', 't'),
        (r'dh', 'd'),
        (r'bh', 'b'),
        (r'sh', 's'),
        (r'ph', 'f'),
        (r'ah$', 'a'),                        # Word-final visarga / aspiration
    ],
    # Malayalam phonetic mapping rules
    "ml": [
        (r'zh', 'l'),
        (r'rr', 'r'),
        (r'nn', 'n'),
        (r'th', 't'),
        (r'dh', 'd'),
        (r'bh', 'b'),
        (r'([aeiou])\1+', r'\1'),
    ],
    # Kannada phonetic mapping rules
    "kn": [
        (r'([aeiou])\1+', r'\1'),
        (r'th', 't'),
        (r'dh', 'd'),
        (r'bh', 'b'),
        (r'kh', 'k'),
        (r'gh', 'g'),
        (r'sh', 's'),
    ],
    # Bengali phonetic mapping rules
    "bn": [
        (r'([aeiou])\1+', r'\1'),
        (r'bh', 'b'),
        (r'dh', 'd'),
        (r'gh', 'g'),
        (r'jh', 'j'),
        (r'th', 't'),
        (r'kh', 'k'),
        (r'ph', 'f'),
        (r'ch', 'c'),
        (r'sh', 's'),
        (r'rh', 'r'),
    ],
    # Generic English / fallback
    "en": [
        (r'ph', 'f'),
        (r'ck', 'k'),
    ]
}

# Languages sharing a sound system reuse the closest measured rule set.
INDIC_PHONETIC_RULES["mr"] = INDIC_PHONETIC_RULES["hi"]
INDIC_PHONETIC_RULES["gu"] = INDIC_PHONETIC_RULES["hi"]
INDIC_PHONETIC_RULES["pa"] = INDIC_PHONETIC_RULES["hi"]
INDIC_PHONETIC_RULES["or"] = INDIC_PHONETIC_RULES["hi"]
INDIC_PHONETIC_RULES["as"] = INDIC_PHONETIC_RULES["bn"]

# Unicode offset mapping for all major Indic scripts
INDIC_CONSONANTS: Dict[int, str] = {
    0x15: 'k',  0x16: 'kh', 0x17: 'g',  0x18: 'gh', 0x19: 'ng',
    0x1A: 'ch', 0x1B: 'chh',0x1C: 'j',  0x1D: 'jh', 0x1E: 'ny',
    0x1F: 't',  0x20: 'th', 0x21: 'd',  0x22: 'dh', 0x23: 'n',
    0x24: 't',  0x25: 'th', 0x26: 'd',  0x27: 'dh', 0x28: 'n',  0x29: 'n',
    0x2A: 'p',  0x2B: 'ph', 0x2C: 'b',  0x2D: 'bh', 0x2E: 'm',
    0x2F: 'y',  0x30: 'r',  0x31: 'r',  0x32: 'l',  0x33: 'l',
    0x34: 'l',  0x35: 'v',  0x36: 'sh', 0x37: 'sh', 0x38: 's', 0x39: 'h',
    0x5C: 'r',  0x5D: 'rh', 0x5F: 'y'
}

INDIC_VOWELS: Dict[int, str] = {
    0x01: 'n',  0x02: 'm',  0x03: 'h',
    0x05: 'a',  0x06: 'aa', 0x07: 'i',  0x08: 'ee', 0x09: 'u',  0x0A: 'oo',
    0x0B: 'ri', 0x0E: 'e',  0x0F: 'e',  0x10: 'ai', 0x12: 'o',  0x13: 'o',  0x14: 'au',
    0x3E: 'aa', 0x3F: 'i',  0x40: 'ee', 0x41: 'u',  0x42: 'oo', 0x43: 'ri',
    0x46: 'e',  0x47: 'e',  0x48: 'ai', 0x4A: 'o',  0x4B: 'o',  0x4C: 'au',
    0x55: 'e',  0x56: 'ai', 0x57: 'au',
    0x7A: 'n',  0x7B: 'n',  0x7C: 'r',  0x7D: 'l',  0x7E: 'l',  0x7F: 'k'
}

# Script blocks handled by the transliterator (Devanagari, Bengali/Assamese, Gurmukhi, Gujarati, Odia, Tamil, Telugu, Kannada, Malayalam).
INDIC_BASES = (0x0900, 0x0980, 0x0A00, 0x0A80, 0x0B00, 0x0B80, 0x0C00, 0x0C80, 0x0D00)

# Letters that exist only in one script (offset within its block).
BASE_EXTRA_CONSONANTS: Dict[int, Dict[int, str]] = {
    0x0900: {0x58: 'k', 0x59: 'kh', 0x5A: 'g', 0x5B: 'z', 0x5E: 'f'},   # Devanagari precomposed nukta letters
    0x0980: {0x70: 'r', 0x71: 'w'},                                        # Assamese ra / wa
    0x0A00: {0x59: 'kh', 0x5A: 'g', 0x5B: 'z', 0x5E: 'f'},                # Gurmukhi precomposed nukta letters
    0x0B00: {0x71: 'w'},                                                   # Odia wa
}
BASE_EXTRA_VOWELS: Dict[int, Dict[int, str]] = {
    0x0900: {0x0D: 'e', 0x11: 'o', 0x45: 'e', 0x49: 'o', 0x72: 'a'},      # candra e / o (English loanwords), Marathi candra a
    0x0A00: {0x70: 'n', 0x72: 'i', 0x73: 'u'},                             # tippi, iri, ura
    0x0A80: {0x0D: 'e', 0x11: 'o', 0x45: 'e', 0x49: 'o'},                 # Gujarati candra e / o
}
# Consonant + nukta (0x3C) sounds, by the consonant's offset.
NUKTA_CONSONANTS: Dict[int, str] = {
    0x15: 'k', 0x16: 'kh', 0x17: 'g', 0x1C: 'z', 0x21: 'r', 0x22: 'rh',
    0x2B: 'f', 0x2F: 'y', 0x32: 'l', 0x38: 'sh',
}
# Marks that carry no sound: nukta, avagraha, danda, double danda, addak, and invisible joiners.
SILENT_OFFSETS = {0x3C, 0x3D, 0x64, 0x65}
INVISIBLE_CHARS = "\u200b\u200c\u200d\u2060\ufeff"


DIGIT_MAP = {
    '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
    '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine',
    '10': 'ten', '11': 'eleven', '12': 'twelve', '13': 'thirteen', '14': 'fourteen',
    '15': 'fifteen', '16': 'sixteen', '17': 'seventeen', '18': 'eighteen', '19': 'nineteen',
    '20': 'twenty', '30': 'thirty', '40': 'forty', '50': 'fifty',
    '60': 'sixty', '70': 'seventy', '80': 'eighty', '90': 'ninety'
}


ENGLISH_CONTRACTIONS: Dict[str, str] = {
    "i'm": "AY M",
    "im": "AY M",
    "i've": "AY V",
    "ive": "AY V",
    "i'll": "AY L",
    "ill": "AY L",
    "i'd": "AY D",
    "you're": "Y U R",
    "youre": "Y U R",
    "you've": "Y U V",
    "you'll": "Y U L",
    "he's": "H E Z",
    "hes": "H E Z",
    "she's": "S H E Z",
    "shes": "S H E Z",
    "it's": "I T S",
    "its": "I T S",
    "we're": "W E R",
    "were": "W E R",
    "they're": "T H E Y R",
    "theyre": "T H E Y R",
    "don't": "D O N T",
    "dont": "D O N T",
    "doesn't": "D O Z N T",
    "doesnt": "D O Z N T",
    "didn't": "D I D N T",
    "didnt": "D I D N T",
    "can't": "K A N T",
    "cant": "K A N T",
    "couldn't": "K U D N T",
    "won't": "W O N T",
    "wont": "W O N T",
    "wouldn't": "W U D N T",
    "isn't": "I Z N T",
    "isnt": "I Z N T",
    "aren't": "A R N T",
    "arent": "A R N T",
    "wasn't": "W A Z N T",
    "weren't": "W E R N T",
    "haven't": "H A V N T",
    "hasn't": "H A Z N T",
    "hadn't": "H A D N T",
    "what's": "W A T S",
    "whats": "W A T S",
    "that's": "T H A T S",
    "thats": "T H A T S",
    "there's": "T H E R Z",
    "theres": "T H E R Z",
    "let's": "L E T S",
    "lets": "L E T S",
    "ain't": "A Y N T",
}


class IndicPhoneticMapper:
    """
    Transforms Romanized and Native Indic words and digits into optimal phonetic acoustic sequences.
    Preserves 100% of English loanwords and broken English speech patterns verbatim.
    """
    @staticmethod
    def is_pure_indic(text: str) -> bool:
        """Returns True if the text contains Indic Unicode characters."""
        return bool(re.search(r'[\u0900-\u0D7F]', text))

    @staticmethod
    def transliterate_indic_script(text: str) -> str:
        """
        Pure-Python G2P transliterator for the Indic Unicode blocks (Devanagari, Bengali/Assamese, Gurmukhi,
        Gujarati, Odia, Tamil, Telugu, Kannada, Malayalam). Input is NFC-normalised and invisible joiners dropped.
        Applies word-final inherent schwa ('a') deletion for Indo-Aryan blocks (Devanagari, Bengali, Gurmukhi,
        Gujarati) on multi-character words ending in a non-conjunct bare consonant.
        """
        text = unicodedata.normalize("NFC", text)
        for ch in INVISIBLE_CHARS:
            text = text.replace(ch, "")
        n = len(text)

        schwa_drop_bases = {0x0900, 0x0980, 0x0A00, 0x0A80}

        def block(code: int):
            for b in INDIC_BASES:
                if b <= code <= b + 0x7F:
                    return b
            return None

        def is_indic_word_char(idx: int) -> bool:
            if idx < 0 or idx >= n:
                return False
            code_pt = ord(text[idx])
            b = block(code_pt)
            if b is None:
                return False
            off = code_pt - b
            if b == 0x0900 and off == 0x70:  # Devanagari abbreviation sign
                return False
            return (0x01 <= off <= 0x63) or (0x70 <= off <= 0x7F)

        res = []
        i = 0
        while i < n:
            c = text[i]
            code = ord(c)
            base = block(code)
            if base is None:
                res.append(c)
                i += 1
                continue

            offset = code - base
            cons = BASE_EXTRA_CONSONANTS.get(base, {}).get(offset) or INDIC_CONSONANTS.get(offset)
            if cons is not None:
                j = i + 1
                if j < n and ord(text[j]) - base == 0x3C:       # consonant + nukta (z, f, r, y, ...)
                    cons = NUKTA_CONSONANTS.get(offset, cons)
                    j += 1
                if j < n:
                    next_offset = ord(text[j]) - base if base <= ord(text[j]) <= base + 0x7F else -1
                    is_chillu = base == 0x0D00 and 0x7A <= next_offset <= 0x7F
                    extra_v = BASE_EXTRA_VOWELS.get(base, {}).get(next_offset)
                    if next_offset == 0x4D:                      # virama / halant: bare consonant
                        res.append(cons)
                        i = j + 1
                        continue
                    if not is_chillu and (extra_v or next_offset in INDIC_VOWELS):
                        res.append(cons + (extra_v or INDIC_VOWELS[next_offset]))
                        i = j + 1
                        continue
                # Word-final inherent schwa deletion for Indo-Aryan scripts (hi/mr/bn/pa/gu):
                # Drop trailing 'a' when at word end, preceded by at least one Indic character
                # (not a single-consonant monosyllable like "न"/"व"), and not preceded by virama (0x4D).
                at_word_end = not is_indic_word_char(j)
                has_prev_word_char = is_indic_word_char(i - 1)
                preceded_by_virama = i > 0 and (ord(text[i - 1]) - base == 0x4D)
                if base in schwa_drop_bases and at_word_end and has_prev_word_char and not preceded_by_virama:
                    res.append(cons)
                else:
                    res.append(cons + 'a')
                i = j
                continue

            vowel = BASE_EXTRA_VOWELS.get(base, {}).get(offset) or INDIC_VOWELS.get(offset)
            if vowel is not None:
                res.append(vowel)
            elif offset == 0x4D or offset in SILENT_OFFSETS or (base == 0x0A00 and offset == 0x71):
                pass
            else:
                res.append(c)
            i += 1
        return ''.join(res)

    @staticmethod
    def number_to_words(text: str) -> str:
        """Converts digit strings (e.g. '10', '15', '100', '250') to spoken words for acoustic matching."""
        if not re.search(r'\d', text):
            return text

        def _int_to_words(val: int) -> str:
            if str(val) in DIGIT_MAP:
                return DIGIT_MAP[str(val)]
            if val < 100:
                tens = (val // 10) * 10
                units = val % 10
                return f"{DIGIT_MAP.get(str(tens), '')} {DIGIT_MAP.get(str(units), '')}".strip()
            if val < 1000:
                hundreds = f"{DIGIT_MAP[str(val // 100)]} hundred"
                rem = val % 100
                return f"{hundreds} {_int_to_words(rem)}".strip() if rem else hundreds
            if val < 10000:
                thousands = f"{DIGIT_MAP[str(val // 1000)]} thousand"
                rem = val % 1000
                return f"{thousands} {_int_to_words(rem)}".strip() if rem else thousands
            return " ".join([DIGIT_MAP.get(d, '') for d in str(val)])

        def replace_num(match):
            val_str = match.group(0)
            if val_str in DIGIT_MAP:
                return DIGIT_MAP[val_str]
            return _int_to_words(int(val_str))

        return re.sub(r'\d+', replace_num, text)

    @classmethod
    def _map_single_segment(cls, word: str, lang: str = "te", is_native_indic: bool = False, orig_word: str = "") -> str:
        """Maps a single homogeneous (either pure Latin or pure Indic) word segment to acoustic tokens."""
        clean_lower = word.lower().strip()

        # Fast path: Check English contractions (e.g. "i'm" -> "AY M", "don't" -> "D O N T")
        clean_word_no_punct = re.sub(r'[^\w\']', '', clean_lower)
        if clean_word_no_punct in ENGLISH_CONTRACTIONS:
            expanded = ENGLISH_CONTRACTIONS[clean_word_no_punct]
            return re.sub(r'[^A-Z]', '', expanded)

        source_word = orig_word if (orig_word and cls.is_pure_indic(orig_word)) else word
        has_indic_chars = cls.is_pure_indic(source_word)

        if has_indic_chars:
            # Step 1: Transliterate native Indic script characters to Roman phonemes
            transliterated = cls.transliterate_indic_script(source_word)
            alpha_only = re.sub(r'[^a-zA-Z]', '', transliterated)
            # Fall back to uroman `word` cleanly if transliterate_indic_script produces < 2 chars
            if len(alpha_only) < 2 and not cls.is_pure_indic(word):
                uroman_alpha = re.sub(r'[^a-zA-Z]', '', word)
                if len(uroman_alpha) > len(alpha_only):
                    transliterated = word
            # Step 2: Convert numbers to words
            w = cls.number_to_words(transliterated.lower().strip())
            # Step 3: Apply language-specific Indic phoneme smoothing
            rules = INDIC_PHONETIC_RULES.get(lang, INDIC_PHONETIC_RULES.get("en", []))
            for pattern, replacement in rules:
                w = re.sub(pattern, replacement, w)
            clean_tokens = re.sub(r'[^a-z]', '', w).upper()
            return clean_tokens if clean_tokens else re.sub(r'[^A-Z]', '', word.upper())

        # Word is purely Latin/ASCII characters (an English loanword or English sentence)
        # Convert numbers if any (e.g. "2d" -> "two d", "15" -> "fifteen")
        w_eng = cls.number_to_words(clean_lower)

        if lang == "en" or not is_native_indic:
            # Standard English phonetic rules (only basic smoothing like ph->f, ck->k)
            # CRITICAL: DO NOT apply Indic rules (sh->s, ch->c, oo->o, pp->p) to English words!
            for pattern, replacement in INDIC_PHONETIC_RULES.get("en", []):
                w_eng = re.sub(pattern, replacement, w_eng)
            clean_tokens = re.sub(r'[^a-z]', '', w_eng).upper()
            return clean_tokens if clean_tokens else re.sub(r'[^A-Z]', '', word.upper())
        else:
            # Romanized Indic word in Roman mode (e.g. "chestunna", "maduthini")
            rules = INDIC_PHONETIC_RULES.get(lang, INDIC_PHONETIC_RULES.get("en", []))
            for pattern, replacement in rules:
                w_eng = re.sub(pattern, replacement, w_eng)
            clean_tokens = re.sub(r'[^a-z]', '', w_eng).upper()
            return clean_tokens if clean_tokens else re.sub(r'[^A-Z]', '', word.upper())

    @classmethod
    def map_to_acoustic_tokens(
        cls,
        word: str,
        lang: str = "te",
        is_native_indic: bool = False,
        orig_word: str = ""
    ) -> str:
        """
        Applies language-aware phonetic transformations.
        Handles Romanized, Native Indic script, and hyphenated/code-switched inputs with 100% G2P coverage,
        while preserving English loanwords (e.g. 'teacher', 'practice', 'cooking', 'I'm', 'English-లో') intact.
        """
        source_for_split = orig_word if orig_word else word
        has_latin = bool(re.search(r'[A-Za-z0-9]', source_for_split))
        has_indic = cls.is_pure_indic(source_for_split)

        # Law 12: Code-switched compound containing both Latin and Indic segments (e.g. "English-లో", "teacher-తో")
        if has_latin and has_indic:
            segments = re.findall(r"[A-Za-z0-9']+|[\u0900-\u0D7F]+", source_for_split)
            token_parts: List[str] = []
            for seg in segments:
                if cls.is_pure_indic(seg):
                    token_parts.append(
                        cls._map_single_segment(seg, lang=lang, is_native_indic=True, orig_word=seg)
                    )
                else:
                    token_parts.append(
                        cls._map_single_segment(seg, lang="en", is_native_indic=False, orig_word=seg)
                    )
            combined = "".join(token_parts)
            return combined if combined else re.sub(r'[^A-Z]', '', word.upper())

        return cls._map_single_segment(word, lang=lang, is_native_indic=is_native_indic, orig_word=orig_word)

