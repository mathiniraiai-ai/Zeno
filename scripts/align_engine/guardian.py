"""
Kavioo Transcription Guardian Layer (Auditor & Auto-Healing Engine)

Provides real-time validation and self-healing for ASR / Gemini transcriptions:
1. Audits raw transcripts against language-specific orthography policies.
2. Detects English loanwords or entire English sentences mistakenly transliterated into native Indic script.
3. Detects single-letter hallucinations and token loops.
4. Tier 1 Auto-Healing: Instant, deterministic in-memory normalization via 22-Language Lexicon Engine (<1ms).
5. Tier 2 Auto-Healing: Targeted single-turn correction re-prompt if multi-word English sentences or severe anomalies are found.
"""

import re
import logging
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field

from .lexicon import lexicon_engine

logger = logging.getLogger("kavioo.guardian")

VALID_SINGLE_LETTERS = {"a", "i", "o", "u", "s", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0"}

class ViolationSeverity(str, Enum):
    INFO = "INFO"
    TIER1_HEALABLE = "TIER1_HEALABLE"  # Can be healed instantly in-memory without LLM call
    TIER2_CRITICAL = "TIER2_CRITICAL"   # Requires targeted LLM correction call

class ViolationType(str, Enum):
    TRANSLITERATED_WORD = "TRANSLITERATED_WORD"
    TRANSLITERATED_SENTENCE = "TRANSLITERATED_SENTENCE"
    SINGLE_LETTER_HALLUCINATION = "SINGLE_LETTER_HALLUCINATION"
    REPETITIVE_LOOP = "REPETITIVE_LOOP"
    COVERAGE_ANOMALY = "COVERAGE_ANOMALY"

@dataclass
class GuardianViolation:
    violation_type: ViolationType
    severity: ViolationSeverity
    token_or_phrase: str
    suggested_fix: Optional[str] = None
    details: str = ""

@dataclass
class GuardianAuditReport:
    is_valid: bool
    requires_tier2_healing: bool
    violations: List[GuardianViolation] = field(default_factory=list)
    healed_text: Optional[str] = None
    healed_words: Optional[List[Dict[str, Any]]] = None

class TranscriptionGuardian:
    """
    Guardian layer that audits and heals transcripts from LLM/ASR engines.
    """

    def audit(
        self,
        full_transcript: str,
        words: List[Any],
        target_language: str = "te",
        script_type: str = "Native",
        audio_duration: Optional[float] = None
    ) -> GuardianAuditReport:
        """
        Performs a full audit of the transcript.
        Returns a structured GuardianAuditReport with detected violations and recommended actions.
        """
        violations: List[GuardianViolation] = []
        requires_tier2 = False

        normalized_word_list: List[str] = []
        for w in words:
            if isinstance(w, dict):
                w_txt = str(w.get("word") or w.get("w") or w.get("text") or "").strip()
                if w_txt:
                    normalized_word_list.append(w_txt)
            elif isinstance(w, str):
                if w.strip():
                    normalized_word_list.append(w.strip())

        raw_text = full_transcript or " ".join(normalized_word_list)

        # 1. Check for single-letter hallucinations
        for token in normalized_word_list:
            clean = token.strip(".,?!:;\"'()[]")
            if len(clean) == 1 and clean.isalpha() and clean.isascii() and clean.lower() not in VALID_SINGLE_LETTERS:
                violations.append(GuardianViolation(
                    violation_type=ViolationType.SINGLE_LETTER_HALLUCINATION,
                    severity=ViolationSeverity.TIER1_HEALABLE,
                    token_or_phrase=token,
                    suggested_fix=None,
                    details=f"Isolated invalid single ASCII letter '{clean}' detected."
                ))

        # 2. Check for repetitive loops
        rep_count = 1
        for i in range(1, len(normalized_word_list)):
            if normalized_word_list[i].lower() == normalized_word_list[i-1].lower() and len(normalized_word_list[i]) > 1:
                rep_count += 1
                if rep_count >= 4:
                    violations.append(GuardianViolation(
                        violation_type=ViolationType.REPETITIVE_LOOP,
                        severity=ViolationSeverity.TIER2_CRITICAL,
                        token_or_phrase=normalized_word_list[i],
                        details=f"Token '{normalized_word_list[i]}' repeated {rep_count} times in sequence."
                    ))
                    requires_tier2 = True
                    break
            else:
                rep_count = 1

        # 3. Check for transliterated English words or phrases in Indic script
        lang_code = (target_language or "te").lower()[:2]
        consecutive_transliterated = 0
        transliterated_spans: List[str] = []

        for token in normalized_word_list:
            clean_tok = token.strip(".,?!:;\"'()[]")
            # Check if token is a known loanword in the lexicon
            normalized_tok = lexicon_engine.sanitize_token(clean_tok, lang_code)
            if normalized_tok != clean_tok:
                # Transliterated loanword found!
                consecutive_transliterated += 1
                transliterated_spans.append(f"{clean_tok} -> {normalized_tok}")
                
                # Check if it is a multi-word sequence of transliterated tokens (like "వన్ బాయ్ ఇస్ వాకింగ్")
                if consecutive_transliterated >= 3:
                    requires_tier2 = True
                    violations.append(GuardianViolation(
                        violation_type=ViolationType.TRANSLITERATED_SENTENCE,
                        severity=ViolationSeverity.TIER2_CRITICAL,
                        token_or_phrase=" ".join(transliterated_spans[-3:]),
                        details="Consecutive transliterated English words detected. Multi-word English sentence was written in native script."
                    ))
                else:
                    violations.append(GuardianViolation(
                        violation_type=ViolationType.TRANSLITERATED_WORD,
                        severity=ViolationSeverity.TIER1_HEALABLE,
                        token_or_phrase=clean_tok,
                        suggested_fix=normalized_tok,
                        details=f"Loanword '{clean_tok}' should be in standard English '{normalized_tok}'."
                    ))
            else:
                consecutive_transliterated = 0

        # 4. Check audio duration coverage anomaly
        if audio_duration and audio_duration > 5.0 and len(normalized_word_list) < 2:
            violations.append(GuardianViolation(
                violation_type=ViolationType.COVERAGE_ANOMALY,
                severity=ViolationSeverity.TIER2_CRITICAL,
                token_or_phrase=raw_text,
                details=f"Audio duration is {audio_duration:.1f}s but only {len(normalized_word_list)} words transcribed."
            ))
            requires_tier2 = True

        # Perform Tier 1 In-Memory Healing
        healed_words_res, healed_text_res = self.heal_tier1(words, lang_code)

        return GuardianAuditReport(
            is_valid=len(violations) == 0,
            requires_tier2_healing=requires_tier2,
            violations=violations,
            healed_text=healed_text_res,
            healed_words=healed_words_res
        )

    def heal_tier1(
        self,
        words: List[Any],
        target_language: str = "te"
    ) -> Tuple[List[Dict[str, Any]], str]:
        """
        Executes instant, deterministic in-memory healing (<1ms) on word tokens:
        - Replaces known transliterated loanwords with standard English.
        - Detaches agglutinative suffixes (e.g. teacher-ని, performance-ని, office-ல).
        - Strips isolated single-letter hallucination artifacts.
        """
        healed_words: List[Dict[str, Any]] = []
        lang_code = (target_language or "te").lower()[:2]

        for item in words:
            if isinstance(item, dict):
                raw_w = str(item.get("word") or item.get("w") or item.get("text") or "").strip()
                s_time = item.get("start", 0.0)
                e_time = item.get("end", 0.0)
            elif isinstance(item, str):
                raw_w = item.strip()
                s_time = 0.0
                e_time = 0.0
            else:
                continue

            if not raw_w:
                continue

            # Strip single-letter hallucinations
            clean_w = raw_w.strip(".,?!:;\"'()[]")
            if len(clean_w) == 1 and clean_w.isalpha() and clean_w.isascii() and clean_w.lower() not in VALID_SINGLE_LETTERS:
                continue

            # Apply Lexicon Sanitization & Suffix Decoupling
            # Preserve trailing punctuation
            punct_match = re.search(r"([.,?!:;]+)$", raw_w)
            trailing_punct = punct_match.group(1) if punct_match else ""
            core_token = raw_w[:-len(trailing_punct)] if trailing_punct else raw_w

            sanitized_core = lexicon_engine.sanitize_token(core_token, lang_code) or core_token
            final_word = f"{sanitized_core}{trailing_punct}"

            healed_words.append({
                "word": final_word,
                "start": s_time,
                "end": e_time
            })

        healed_text = " ".join(w["word"] for w in healed_words)
        return healed_words, healed_text

    def build_healing_prompt(
        self,
        audit_report: GuardianAuditReport,
        original_transcript: str,
        target_language: str = "te",
        language_label: str = "Telugu"
    ) -> str:
        """
        Constructs a minimal, hyper-targeted healing prompt for Gemini to fix only the defective segments.
        """
        issues_summary = []
        for v in audit_report.violations:
            if v.severity == ViolationSeverity.TIER2_CRITICAL:
                issues_summary.append(f"- {v.details} (Offending phrase: '{v.token_or_phrase}')")

        issues_block = "\n".join(issues_summary) or "- Spoken English dialogue sentences were transliterated into native script."

        prompt = f"""You are correcting an automated transcription for professional video subtitles.

## Target Configuration
- Language: {language_label}
- Script: Native {language_label} + English Latin Script (A-Z)

## Previous Draft with Detected Flaws
{original_transcript}

## Audit Findings to Correct:
{issues_block}

## Instructions for Correction:
1. Transcribe the audio 100% verbatim.
2. STRICT BILINGUAL LAW: Any English speech (complete English sentences, English phrases, practice dialogue, technical words) MUST be written in standard ENGLISH (Latin letters: A-Z). Never write English words using {language_label} script characters.
3. Native {language_label} speech must remain in natural conversational {language_label} script.
4. Output ONLY valid JSON matching this schema:
{{
  "language": "{target_language}",
  "language_name": "{language_label}",
  "script_type": "Native",
  "full_transcript": "Corrected punctuated transcript text...",
  "words": ["word1", "word2", "word3"]
}}
"""
        return prompt

guardian_engine = TranscriptionGuardian()
