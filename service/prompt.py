"""Transcription prompt. A verbatim copy of build_transcribe_prompt in scripts/align_engine/gemini_transcriber.py.
Kept as a copy because Railway deploys only the service/ folder. Run `python service/check_prompt_sync.py` after
changing either one: it fails if they differ."""
from typing import List, Optional


def build_transcribe_prompt(target_script_lines: Optional[List[str]] = None, language_hint: str = "auto") -> str:
    script_context = ""
    if target_script_lines:
        clean_lines: List[str] = []
        seen_lines: set[str] = set()
        for raw_x in target_script_lines:
            s_line = str(raw_x).strip()
            if not s_line:
                continue
            key_l = s_line.lower()
            if key_l in seen_lines:
                continue
            seen_lines.add(key_l)
            clean_lines.append(s_line)
            if len(clean_lines) >= 80:
                break
        if clean_lines:
            joined_script = "\n".join(f"- {line}" for line in clean_lines)
            script_context = (
                "\n## Optional Spelling & Vocabulary Reference (FULL MULTI-SPEAKER SCRIPT — NOT THIS SINGLE CLIP):\n"
                "WARNING: The list below is the full multi-character video script. THIS audio clip usually contains ONLY ONE "
                "speaker or ONE scene — many lines below belong to OTHER speakers in OTHER video clips and are NOT in this audio. "
                "Use this list ONLY for spelling proper nouns, brand names, and words you genuinely hear spoken aloud.\n"
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
Transcribe ONLY the intelligible words actually spoken aloud in THIS audio clip, 100% verbatim.
{lang_note}{script_context}
Rules:
1. TRUE VERBATIM CHRONOLOGICAL ORDER & FULL-CLIP PAUSE CONTINUATION: Include EVERY word actually spoken aloud in this audio from 0.0s through the final second, including genuine audible retakes, repeated words, stutters, false starts, and restarted phrases (e.g. "Almost... Almost", "I want... I want to go", "ನಾನು ನಾನು"). NEVER collapse or clean up a genuine audible repeat/stutter just because the reference shows it once — if the speaker audibly says a word or phrase twice in THIS audio, output it twice. Audio clips up to 90s frequently contain 1 to 5 second silent pauses, breath gaps, reaction holds, or multiple consecutive lines or takes by the same speaker — NEVER stop transcribing at a pause! Always listen past every silent gap all the way to the final second of the audio and transcribe EVERY spoken utterance in chronological order.
2. STRICT ANTI-ECHO & SINGLE-CLIP GUARD: NEVER copy, auto-complete, or borrow unspoken lines from the reference script! Especially in teaching / mock-interview / call-and-response scripts where Character A says `Say: "<sentence>"` and Character B repeats `<sentence>` on the next line, those turns are recorded in SEPARATE clips. If THIS audio only speaks a sentence ONCE, transcribe it ONLY ONCE — never emit a second copy from another speaker's script line, and never fabricate neighboring lines or unspoken turns.
3. ZERO-SPEECH GUARD: If the audio contains zero intelligible human speech (only silence, room tone, SFX, background noise, or music), return `"full_transcript": ""` and `"cues": []` — NEVER hallucinate or copy reference script lines onto silent or non-speech audio!
4. AI GIBBERISH GUARD: If the clip ends with unintelligible AI gibberish/babble after the real sentence concludes, DO NOT transcribe the unintelligible gibberish.
5. STRICT BILINGUAL CODE-SWITCHING LAW: Decide language word by word. A spoken English word stays English, in the Latin alphabet (A-Z), even inside a native sentence. A native word stays native, in its own script. Never transliterate English into native script or native words into Latin.
6. PUNCTUATION & VALID JSON: Attach sentence punctuation (.,?!) to the end of the spoken word. Avoid unescaped quotation marks inside JSON string values (prefer omitting decorative quotes around quoted speech).
7. Return ONLY valid JSON with this exact compact structure (do NOT duplicate word arrays inside cues):
{{
  "detected_language_code": "kn",
  "detected_language_name": "Kannada",
  "full_transcript": "Complete verbatim transcript...",
  "cues": [
    {{"start": 0.0, "end": 2.4, "text": "sentence or clause text"}}
  ]
}}"""
