# Third-Party & Bundled Asset License Audit (`LICENSES.md`)

All models, libraries, fonts, and audio/visual assets bundled in or downloaded by **Zeno** have been audited for commercial production safety:

| Component | Path in Repo | License | Commercial-Safe? | Attribution / Notes |
|---|---|---|---|---|
| **Zeno Core Software** | `scripts/`, `agent/`, `styles/`, `service/` | **Supernova Internal Use License** (`LICENSE`) | Yes (Internal) | Copyright (c) 2026 Supernova. All rights reserved. |
| **Meta Wav2Vec2 Base (`95M`)** | `models/torch/` (`WAV2VEC2_ASR_BASE_960H` via `torchaudio`) | **MIT License** | Yes | Meta Platforms, Inc. (No `mms-300m` CC-BY-NC weights are used). |
| **uroman (`v1.3.1.1`)** | `scripts/align_engine/uroman/` | **MIT License** (`scripts/align_engine/uroman/LICENSE.txt`) | Yes | Copyright (c) 2015–2024 Ulf Hermjakob, USC Information Sciences Institute. |
| **YuNet Face Detector** | `models/face_detection_yunet_2023mar.onnx` | **MIT License** (OpenCV Zoo / `libfacedetection`) | Yes | Copyright (c) 2018–2023 Shiqi Yu, Wei Wu, Hanyang Peng (`opencv/opencv_zoo`). |
| **Montserrat-ExtraBold Font (`v7.200`)** | `assets/fonts/Montserrat-ExtraBold.ttf` | **SIL Open Font License 1.1 (OFL-1.1)** | Yes | Copyright 2011 The Montserrat Project Authors (`github.com/JulietaUla/Montserrat`). |
| **Sound Effects (`assets/sfx/`)** | `assets/sfx/*.wav`, `assets/sfx/*.mp3` | **Supernova Proprietary & Synthesized Audio** (`LICENSE` Sec. 7) | Yes (Internal) | Supernova production packs (`sn_*`, `kn20228_*`, `w42_*`, `whoosh.wav`) and synthesized cues (`buzzer.wav`, `correct_ding.wav`, `click.wav`, `ding.wav`, `pop.wav`, `riser.wav`). |
| **Visual Marks & Templates** | `assets/marks/`, `assets/overlays/`, `assets/premiere/` | **Supernova Proprietary** | Yes (Internal) | Generated programmatically via Pillow/NumPy or authored in-house. |
| **Python Runtime Dependencies** | `requirements.txt`, `service/requirements.txt` | **BSD-3 / Apache-2.0 / MIT / HPND / ISC / MPL-2.0** | Yes | Permissive commercial-safe open-source licenses. |

- `silero-vad` (Silero Team, MIT): speech detection over background noise, installed from PyPI by `scripts/gpu_install.py` (not stored in the repository).
