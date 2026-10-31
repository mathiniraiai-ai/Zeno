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
