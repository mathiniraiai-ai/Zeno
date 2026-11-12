"""Transcription prompt. A verbatim copy of build_transcribe_prompt in scripts/align_engine/gemini_transcriber.py.
Kept as a copy because Railway deploys only the service/ folder. Run `python service/check_prompt_sync.py` after
changing either one: it fails if they differ."""
from typing import List, Optional


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

    scripts = {
        "ml": ("Malayalam", "Malayalam script (മലയാളം)"), "kn": ("Kannada", "Kannada script (ಕನ್ನಡ)"),
        "ta": ("Tamil", "Tamil script (தமிழ்)"), "te": ("Telugu", "Telugu script (తెలుగు)"),
        "hi": ("Hindi", "Devanagari script (हिन्दी)"), "mr": ("Marathi", "Devanagari script (मराठी)"),
        "bn": ("Bengali", "Bengali script (বাংলা)"), "gu": ("Gujarati", "Gujarati script (ગુજરાતી)"),
        "pa": ("Punjabi", "Gurmukhi script (ਪੰਜਾਬੀ)"), "or": ("Odia", "Odia script (ଓଡ଼ିଆ)"),
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
