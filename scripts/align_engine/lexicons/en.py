"""
English (en) Lexicon & Loanwords Dictionary for Kavioo Subtitle Engine

IMPORTANT — Verbatim Transcription Rule:
  This lexicon is for cleaning COLLOQUIAL CONTRACTIONS only.
  It must NEVER expand spoken abbreviations (AI, CEO, UI, API, etc.)
  into their full forms. If the speaker says "AI", the subtitle must show "AI".
  If the speaker says "artificial intelligence", the subtitle must show "artificial intelligence".
  Expanding abbreviations violates the verbatim transcription guarantee.
"""

LANGUAGE_CODE = "en"
LANGUAGE_NAME = "English"

SUFFIXES = [
]

LOANWORDS = {
    # Colloquial contractions → readable subtitle forms
    "gonna": "going to",
    "wanna": "want to",
    "gotta": "got to",
    "lemme": "let me",
    "gimme": "give me",
    "sorta": "sort of",
    "kinda": "kind of",
    "outta": "out of",
    "watcha": "what are you",

    # Common misspellings / misheard words that benefit from correction
    "wifi": "Wi-Fi",
}
