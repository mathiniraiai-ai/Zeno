# Word-Boundary Calibration & Alignment Model Training (`align_engine`)

## 1. Why Speech Starts & Ends Get Cut Off ("Time Stop / Boundary Issue")

1. **CTC Peaky Emission Bias (`WAV2VEC2_ASR_BASE_960H`)**:
   - CTC neural acoustic models emit token probability spikes at the **high-energy vowel nucleus** of a syllable while predicting `<blank>` (`id=0`) during unvoiced/pre-voiced consonant closures (`/k/`, `/t/`, `/p/`, `/s/`, aspirates `/kh/`, `/bh/`) and word-final codas.
   - In Indian languages (Kannada, Tamil, Hindi, Telugu, Malayalam), initial consonant attacks and sentence-final euphonic vowels (`-u` / schwa / nasal release) sit `15–28 dB` below the stressed vowel peak.
   - As a result, raw CTC `w.start` lags **`60–120ms` after** the physical consonant onset, and raw `w.end` terminates **`100–200ms` before** the final syllable finishes decaying.
2. **Post-Seek Non-Zero PTS Offset ("Time Stop" Freeze)**:
   - Fast input seeking (`ffmpeg -ss <start> -i <src>`) lands on the preceding keyframe and leaves a non-zero packet `STARTPTS`. Without `setpts=PTS-STARTPTS` before `fps={fps}` and `asetpts=PTS-STARTPTS` before `atrim=0:{exact}`, FFmpeg clones the first video frame while shifting the audio cut window.
3. **Transition Overlap Swallowing Speech (`xfade` / `acrossfade`)**:
   - A standard `0.28s` `acrossfade` overlaps the last `280ms` of outgoing segment `i-1` with the first `280ms` of incoming segment `i`, muting the final syllable of the outgoing sentence and the initial word of the incoming sentence.

---

## 2. Built-In Deterministic Acoustic Protections in Zeno

1. **Asymmetric Onset/Coda Energy Verification (`scripts/align_engine/onset_verifier.py`)**:
   - `snap_starts_to_energy`: Uses a low `12%` dB rise fraction above the local noise-floor valley plus a **`-35ms` consonant pre-attack guard**.
   - `extend_ends_to_energy`: Extends word ends forward until energy drops `24 dB` below the 85th-percentile vowel peak (up to `350ms`), plus a **`+45ms` coda release guard**.
2. **Pre-Roll (`140ms`) & Post-Roll (`180ms`) Segment Boundary Guard (`scripts/render.py`)**:
   - `protect_segment_word_boundaries()` verifies every segment against cached `hq_words.json` and 10ms `rms.json` energy, enforcing `>= 140ms` pre-roll before the first word's unvoiced onset and `>= 180ms` post-roll after the last word's vowel tail (while staying strictly clear of any excluded retake words).
3. **Zero-Audio-Loss Transitions (`tpad` Video Clone + `concat` Audio)**:
   - Visual transitions (`whip_left`, `whip_right`, `zoom_burst`, `flash_zoom`, `slide_up`) clone $k$ frames on video via `tpad` for `xfade` + optical blur (`dblur`, `rgbashift`, `lutrgb`) while joining audio via `concat=n=2:v=0:a=1` with `12ms` micro-fades (`afade`), guaranteeing **0.000s of speech overlap** and **0-frame A/V drift**.

---

## 3. How to Train & Calibrate the Alignment Model on Your Studio Data

### Stage 1: Lightweight Boundary Offset Calibrator (Trainable in <10 Mins on CPU/GPU)
Keep the Wav2Vec2 backbone frozen and train a lightweight **Asymmetric Quantile Regressor (`models/boundary_calibrator.onnx`)** on Zeno's own `plan.edl` edits:
1. **Collect Training Pairs from `work/<project>/`**:
   - Pair `(raw_ctc_start, raw_ctc_end)` from `words.json` with `(verified_start, verified_end)` from human/Agent-5 verified `plan.edl` segments.
2. **Features (`±250ms` window at `5ms` hop)**:
   - 40-bin log-Mel spectrogram, `5ms` RMS dB contour, spectral flux, zero-crossing rate (ZCR), CTC non-blank probability `1 - P(blank)`, and initial/final Indic phoneme class.
3. **Loss Function (Pinball / Asymmetric Quantile Loss)**:
   - Train $\Delta t_{\text{start}}$ with $\tau = 0.10$ (penalizing early cuts $9\times$ more than extra pre-roll) and $\Delta t_{\text{end}}$ with $\tau = 0.90$ (penalizing early truncation $9\times$ more than extra post-roll).

### Stage 2: Commercial-Safe Multilingual Backbones, VAD Gating & Entropy-Regularized CTC
1. **Commercial-Safe Backbones & VAD Pre-Gating (MIT / BSD / Apache-2.0 Only)**:
   - Keep **`WAV2VEC2_ASR_BASE_960H`** (`MIT`) + Indic G2P (`indic_phonemes.py` + `uroman`), or pair with commercial-safe alternatives: **OpenAI Whisper** (`MIT`), **Silero VAD** (`MIT`) / **WebRTC VAD** (`BSD-3-Clause`) for speech-boundary gating, **Gemini Audio API** word/phrase timestamps, and deterministic **10ms RMS + Zero-Crossing Rate (ZCR)** onset/coda refinement (`onset_verifier.py`).
2. **Peak-Suppression CTC Loss**:
   - Fine-tune on your studio `(audio16k.wav, script.txt)` pairs with **Entropy Regularization**:
     $$\mathcal{L} = \mathcal{L}_{\text{CTC}} - \lambda \sum_{t} H\big(P(y_t \mid x)\big), \quad \lambda = 0.05$$
   - This prevents CTC from collapsing phoneme emissions into a single 20ms frame spike and spreads token probability across the full duration of the spoken word.

### Stage 3: Montreal Forced Aligner (MFA) Teacher-Student Frame Distillation
1. Run **Montreal Forced Aligner (MFA)** (GMM-HMM 3-state triphone aligner, which models `[onset, nucleus, coda]` explicitly without CTC peakiness) offline on 5–10 hours of your studio takes to generate frame-level phoneme TextGrids.
2. Fine-tune the Wav2Vec2 projection head with **frame-level Cross-Entropy (CE)** against the MFA state labels for millisecond-exact onset and coda boundaries.
