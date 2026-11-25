"""Acoustic CTC emission extractor backed by Meta omniASR-CTC-300M-v2 (Apache 2.0) and speech-island aligner."""

from __future__ import annotations

import ctypes
import gc
import importlib
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_VOCAB_PATH = Path(__file__).resolve().parent / "data" / "omniasr_vocab.json"

# Keep auxiliary torch caches (e.g. Silero VAD) inside Zeno/models/torch rather than ~/.cache.
_TORCH_HOME = _ROOT / "models" / "torch"
os.environ.setdefault("TORCH_HOME", str(_TORCH_HOME))
# Allow unsupported Apple Silicon MPS ops to fall back cleanly to CPU.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np
import soundfile as sf
import torch

OMNIASR_DEFAULT_REPO = "aadel4/omniASR-CTC-300M-v2"
OMNIASR_DEFAULT_REVISION = "f2b3c39a62c061c27623c3f2d803294fbb954324"

_OMNIASR_REPOS: Dict[str, Tuple[str, Optional[str], str]] = {
    "omniasr_300m_v2": (
        OMNIASR_DEFAULT_REPO,
        OMNIASR_DEFAULT_REVISION,
        "Meta omniASR-CTC-300M-v2",
    ),
    "omniasr_1b_v2": (
        "aadel4/omniASR-CTC-1B-v2",
        None,
        "Meta omniASR-CTC-1B-v2",
    ),
}

_OTA_HEALED_ONCE = False


def _pick_device() -> str:
    """Select NVIDIA CUDA, Apple Silicon MPS, or CPU (`ZENO_DEVICE` overrides via `common.compute_device`)."""
    try:
        from common import compute_device

        return compute_device()
    except Exception:
        env_dev = os.environ.get("ZENO_DEVICE", "").strip().lower()
        if env_dev in ("cuda", "mps", "cpu"):
            return env_dev
        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        return "mps" if (mps is not None and mps.is_available()) else "cpu"


def detect_thermal_profile(device: str) -> str:
    """Select hardware thermal profile (`mac_fanless` | `desktop_cuda` | `cpu_safe`)."""
    env_profile = os.environ.get("ZENO_THERMAL_PROFILE", "").strip().lower()
    if env_profile in ("mac_fanless", "desktop_cuda", "cpu_safe"):
        return env_profile
    dev_l = str(device or "").lower()
    if "cuda" in dev_l:
        return "desktop_cuda"
    if "mps" in dev_l:
        return "mac_fanless"
    return "cpu_safe"


def read_macos_cpu_speed_limit() -> int:
    """Return macOS `CPU_Speed_Limit` percentage from `pmset -g therm` (100 when unthrottled or non-macOS)."""
    if platform.system() != "Darwin":
        return 100
    try:
        out = subprocess.check_output(["pmset", "-g", "therm"], text=True, timeout=2)
        m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", out)
        return int(m.group(1)) if m else 100
    except Exception:
        return 100


def _free_gpu_cache(dev: Any) -> None:
    try:
        dev_str = str(dev)
        if "cuda" in dev_str and torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif "mps" in dev_str and hasattr(torch, "mps"):
            try:
                torch.mps.synchronize()
            except Exception:
                pass
            torch.mps.empty_cache()
    except Exception:
        pass


class ThermalGuard:
    """Sensor-driven per-clip pacing hook: sleeps only when thermally throttled or `ZENO_COOLDOWN_SEC` is set."""

    def __init__(
        self,
        device: str,
        total_clips: int,
        clip_cooldown_sec: Optional[float] = None,
        breather_every: int = 10,
        breather_sec: float = 0.0,
    ):
        self.device = str(device)
        self.total_clips = int(total_clips)
        if clip_cooldown_sec is None:
            env_cd = os.environ.get("ZENO_COOLDOWN_SEC", "").strip()
            if env_cd:
                try:
                    clip_cooldown_sec = max(0.0, float(env_cd))
                except ValueError:
                    clip_cooldown_sec = None
        if clip_cooldown_sec is None:
            clip_cooldown_sec = 0.0
        self.clip_cooldown_sec = float(clip_cooldown_sec)
        self.breather_every = int(breather_every)
        self.breather_sec = float(breather_sec)
        self.completed = 0

    def after_clip(self) -> None:
        self.completed += 1
        _free_gpu_cache(self.device)
        if self.completed >= self.total_clips:
            return
        if read_macos_cpu_speed_limit() < 100:
            gc.collect()
            time.sleep(5.0)
            return
        if self.breather_every > 0 and self.completed % self.breather_every == 0 and self.breather_sec > 0.0:
            gc.collect()
            time.sleep(self.breather_sec)
            return
        if self.clip_cooldown_sec > 0.0:
            time.sleep(self.clip_cooldown_sec)


def _is_valid_omniasr_dir(path: Path) -> bool:
    """Return True if `path` holds real OmniASR weights (`model.safetensors`) and `config.json`."""
    return path.is_dir() and (path / "model.safetensors").is_file() and (path / "config.json").is_file()


def _hf_cache_candidates() -> List[Path]:
    """Return ordered candidate directories that may hold local Hugging Face model snapshots."""
    here = Path(__file__).resolve()
    candidates: List[Path] = []
    for env_key in ("ZENO_HF_CACHE", "HF_HOME"):
        val = os.environ.get(env_key, "").strip()
        if val:
            candidates.append(Path(val).expanduser())
    for parent_idx in (2, 3):
        if len(here.parents) > parent_idx:
            candidates.append(here.parents[parent_idx] / "models" / "hf_cache")
    deduped: List[Path] = []
    seen: set[str] = set()
    for p in candidates:
        key = str(p)
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    return deduped


def _resolve_hf_snapshot(repo_id: str) -> Tuple[str, Dict[str, Any]]:
    """Resolve an existing local snapshot directory for `repo_id` when present in HF cache directories."""
    direct = Path(repo_id)
    if _is_valid_omniasr_dir(direct):
        return str(direct), {"local_files_only": True}

    short_name = repo_id.rstrip("/").split("/")[-1]
    repo_cache_name = f"models--{repo_id.strip('/').replace('/', '--')}"

    for cdir in _hf_cache_candidates():
        if not cdir.is_dir():
            continue
        if _is_valid_omniasr_dir(cdir):
            return str(cdir), {"local_files_only": True}
        sub_exact = cdir / short_name
        if _is_valid_omniasr_dir(sub_exact):
            return str(sub_exact), {"local_files_only": True}

        for hub_dir in (cdir / "hub", cdir / "hf_cache" / "hub", cdir):
            if not hub_dir.is_dir():
                continue
            repo_dir = hub_dir / repo_cache_name
            if not repo_dir.is_dir():
                continue

            ref_main = repo_dir / "refs" / "main"
            if ref_main.is_file():
                try:
                    rev = ref_main.read_text(encoding="utf-8").strip()
                    snap_main = repo_dir / "snapshots" / rev
                    if rev and _is_valid_omniasr_dir(snap_main):
                        return str(snap_main), {"local_files_only": True}
                except Exception:
                    pass

            snaps_root = repo_dir / "snapshots"
            if snaps_root.is_dir():
                snap_dirs = sorted(
                    (p for p in snaps_root.iterdir() if p.is_dir()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                for snap in snap_dirs:
                    if _is_valid_omniasr_dir(snap):
                        return str(snap), {"local_files_only": True}

    return "", {}


def _configure_certifi_env() -> None:
    """Populate `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` from `certifi` when unset."""
    try:
        import certifi

        ca_bundle = certifi.where()
        if ca_bundle and Path(ca_bundle).is_file():
            if not os.environ.get("SSL_CERT_FILE"):
                os.environ["SSL_CERT_FILE"] = ca_bundle
            if not os.environ.get("REQUESTS_CA_BUNDLE"):
                os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle
    except Exception:
        pass


def _ensure_ml_packages_imported() -> Any:
    """Import `transformers`, `huggingface_hub`, and `safetensors`, running `gpu_install.py` once on `ImportError`."""
    global _OTA_HEALED_ONCE
    try:
        import huggingface_hub  # noqa: F401
        import safetensors  # noqa: F401
        from transformers import Wav2Vec2ForCTC

        return Wav2Vec2ForCTC
    except ImportError:
        if not _OTA_HEALED_ONCE:
            _OTA_HEALED_ONCE = True
            gpu_install = _ROOT / "scripts" / "gpu_install.py"
            if gpu_install.is_file():
                print(
                    "[Zeno Aligner] Installing alignment dependencies via scripts/gpu_install.py...",
                    flush=True,
                )
                subprocess.run(
                    [sys.executable, "-B", str(gpu_install)],
                    check=False,
                    timeout=600,
                )
                importlib.invalidate_caches()
        import huggingface_hub  # noqa: F401
        import safetensors  # noqa: F401
        from transformers import Wav2Vec2ForCTC

        return Wav2Vec2ForCTC


def _compute_chunk_windows(
    num_samples: int,
    sr: int = 16000,
    chunk_sec: float = 30.0,
    overlap_sec: float = 1.0,
    max_single_pass_sec: float = 35.0,
) -> List[Tuple[int, int, int, int]]:
    """Partition `num_samples` into `(win_start, win_end, core_start, core_end)` sample windows.

    Core intervals `[core_start, core_end)` tile `[0, num_samples)` without overlap while each
    forward window `[win_start, win_end)` extends by `overlap_sec` on both sides to prevent
    CTC boundary artifacts on long recordings (> 35 s).
    """
    if num_samples <= 0:
        return []
    duration_sec = float(num_samples) / float(sr)
    if duration_sec <= max_single_pass_sec:
        return [(0, num_samples, 0, num_samples)]

    chunk_samples = max(sr, int(round(chunk_sec * sr)))
    pad_samples = max(0, int(round(overlap_sec * sr)))
    min_tail_samples = int(round(2.0 * sr))

    windows: List[Tuple[int, int, int, int]] = []
    core_start = 0
    while core_start < num_samples:
        core_end = min(num_samples, core_start + chunk_samples)
        if 0 < (num_samples - core_end) < min_tail_samples:
            core_end = num_samples
        win_start = max(0, core_start - pad_samples)
        win_end = min(num_samples, core_end + pad_samples)
        windows.append((win_start, win_end, core_start, core_end))
        core_start = core_end

    return windows


class AcousticModel:
    """CTC log-probability emission extractor backed purely by Meta omniASR-CTC-300M-v2."""

    def __init__(self, device: Optional[str] = None, use_fp16: Optional[bool] = None):
        self.torch_model = None
        self._infer_lock = threading.RLock()
        self.device = device or _pick_device()
        self.thermal_profile = detect_thermal_profile(self.device)
        self._use_omniasr = True

        model_raw = os.getenv("ZENO_ALIGN_MODEL", "omniasr_300m_v2").strip()
        model_key = model_raw.lower()
        local_override = Path(model_raw).expanduser() if model_raw else None
        if local_override is not None and local_override.is_dir():
            self._model_choice = "local"
            self._hf_repo_id = str(local_override.resolve())
            self._hf_revision: Optional[str] = None
            self._model_label = f"Meta omniASR ({local_override.name})"
        elif model_key in ("omniasr_1b_v2", "1b", "omniasr-ctc-1b-v2", "aadel4/omniasr-ctc-1b-v2"):
            self._model_choice = "omniasr_1b_v2"
            self._hf_repo_id, self._hf_revision, self._model_label = _OMNIASR_REPOS["omniasr_1b_v2"]
        else:
            self._model_choice = "omniasr_300m_v2"
            self._hf_repo_id, self._hf_revision, self._model_label = _OMNIASR_REPOS["omniasr_300m_v2"]

        is_gpu = ("cuda" in str(self.device)) or ("mps" in str(self.device))
        dtype_override = os.getenv("ZENO_ALIGN_DTYPE", "").strip().lower()
        # FP16 on MPS/CUDA, FP32 on CPU, never BF16.
        if dtype_override in ("fp32", "float32", "f32"):
            self.use_fp16 = False
        elif dtype_override in ("fp16", "float16", "f16", "half"):
            self.use_fp16 = is_gpu
        elif use_fp16 is not None:
            self.use_fp16 = bool(use_fp16) and is_gpu
        else:
            self.use_fp16 = is_gpu
        self._torch_dtype = torch.float16 if self.use_fp16 else torch.float32

        # Sliding-window chunking configuration for long audio (> 35 s).
        self.chunk_sec: float = 30.0
        self.overlap_sec: float = 1.0
        self.max_single_pass_sec: float = 35.0

        self._hf_source: str = ""
        self._hf_load_kwargs: Dict[str, Any] = {}
        self.sample_rate: int = 16000
        self.dictionary: Dict[str, int] = {}
        self.blank_id: int = 0

        local_dir = self._find_local_omniasr_dir()
        if local_dir:
            self._hf_source = local_dir
            self._hf_load_kwargs = {"local_files_only": True}
        self._init_omniasr_metadata()

    def _short_model_name(self) -> str:
        if self._model_choice == "local":
            return Path(self._hf_repo_id).name
        return self._hf_repo_id.rstrip("/").split("/")[-1]

    def _find_local_omniasr_dir(self) -> str:
        """Check local model directories in priority order without making any network calls."""
        if self._model_choice == "local":
            p = Path(self._hf_repo_id)
            if _is_valid_omniasr_dir(p):
                return str(p)

        env_dir = os.environ.get("ZENO_OMNIASR_DIR", "").strip()
        if env_dir:
            p = Path(env_dir).expanduser()
            if _is_valid_omniasr_dir(p):
                return str(p)

        short_name = self._short_model_name()
        bundled_dir = _ROOT / "models" / short_name
        if _is_valid_omniasr_dir(bundled_dir):
            return str(bundled_dir)

        snap_src, _snap_kw = _resolve_hf_snapshot(self._hf_repo_id)
        if snap_src and _is_valid_omniasr_dir(Path(snap_src)):
            return snap_src

        return ""

    def _ensure_local_omniasr_dir(self) -> str:
        """Return a verified local OmniASR directory, downloading symlink-free into `models/` when absent."""
        local_dir = self._find_local_omniasr_dir()
        if local_dir:
            return local_dir

        short_name = self._short_model_name()
        target_dir = _ROOT / "models" / short_name
        target_dir.mkdir(parents=True, exist_ok=True)
        _configure_certifi_env()

        print(
            f"[Zeno Aligner] Downloading {self._model_label} (~1.21 GB, one-time setup) into {target_dir}...",
            flush=True,
        )
        from huggingface_hub import snapshot_download  # type: ignore[import-not-found]

        dl_kwargs: Dict[str, Any] = {
            "repo_id": self._hf_repo_id,
            "local_dir": str(target_dir),
        }
        if self._hf_revision:
            dl_kwargs["revision"] = self._hf_revision
        snapshot_download(**dl_kwargs)

        if _is_valid_omniasr_dir(target_dir):
            return str(target_dir)
        raise RuntimeError(f"Downloaded OmniASR directory missing model.safetensors or config.json: {target_dir}")

    def _init_omniasr_metadata(self) -> None:
        """Load OmniASR `vocab.json`, `config.json`, and `preprocessor_config.json` (with bundled offline fallback)."""
        src_dir = Path(self._hf_source) if self._hf_source else None
        if src_dir is not None and src_dir.is_dir():
            preproc_p = src_dir / "preprocessor_config.json"
            if preproc_p.is_file():
                try:
                    preproc = json.loads(preproc_p.read_text(encoding="utf-8"))
                    self.sample_rate = int(preproc.get("sampling_rate", 16000) or 16000)
                except Exception:
                    self.sample_rate = 16000
            cfg_p = src_dir / "config.json"
            if cfg_p.is_file():
                try:
                    cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
                    pad_id = cfg.get("pad_token_id", 0)
                    self.blank_id = int(pad_id) if pad_id is not None else 0
                except Exception:
                    self.blank_id = 0
            vocab_p = src_dir / "vocab.json"
            if vocab_p.is_file():
                try:
                    raw_vocab = json.loads(vocab_p.read_text(encoding="utf-8"))
                    self.dictionary = {str(k): int(v) for k, v in raw_vocab.items()}
                except Exception:
                    self.dictionary = {}

        if not self.dictionary and _BUNDLED_VOCAB_PATH.is_file():
            try:
                raw_vocab = json.loads(_BUNDLED_VOCAB_PATH.read_text(encoding="utf-8"))
                self.dictionary = {str(k): int(v) for k, v in raw_vocab.items()}
            except Exception:
                self.dictionary = {}

    def _apply_thread_cap(self) -> None:
        """Cap PyTorch CPU threads on fanless macOS / CPU profiles to avoid thermal saturation."""
        if (
            self.thermal_profile in ("mac_fanless", "cpu_safe")
            or "cpu" in str(self.device)
            or "mps" in str(self.device)
        ):
            try:
                torch.set_num_threads(min(max(4, os.cpu_count() or 4), 12))
            except Exception:
                pass

    def _thermal_check_and_cool(self) -> None:
        """Pause briefly and flush GPU cache on macOS ONLY when `pmset -g therm` reports throttling."""
        if platform.system() != "Darwin":
            return
        speed_limit = read_macos_cpu_speed_limit()
        if speed_limit < 100:
            _free_gpu_cache(self.device)
            gc.collect()
            print(
                f"[Zeno Aligner] macOS thermal guard active (CPU_Speed_Limit={speed_limit}%); cooling 5.0s..."
            )
            time.sleep(5.0)

    def _ensure_model_loaded(self) -> None:
        with self._infer_lock:
            if self.torch_model is not None:
                return
            self._apply_thread_cap()

            Wav2Vec2ForCTC = _ensure_ml_packages_imported()

            local_dir = self._ensure_local_omniasr_dir()
            self._hf_source = local_dir
            self._hf_load_kwargs = {"local_files_only": True}

            dtype_label = "fp16" if self.use_fp16 else "fp32"
            print(
                f"[Zeno Aligner] Loading {self._model_label} "
                f"(Apache 2.0, {dtype_label}, device: {self.device}, thermal_guard: sensor)..."
            )
            model = Wav2Vec2ForCTC.from_pretrained(
                self._hf_source,
                low_cpu_mem_usage=True,
                **self._hf_load_kwargs,
            )

            # Refresh vocab/config from the resolved model directory if needed, keeping bundled fallback.
            self._init_omniasr_metadata()
            if not self.dictionary:
                from transformers import Wav2Vec2CTCTokenizer  # type: ignore[import-not-found]

                tok = Wav2Vec2CTCTokenizer.from_pretrained(self._hf_source, **self._hf_load_kwargs)
                self.dictionary = {str(k): int(v) for k, v in tok.get_vocab().items()}

            pad_id = getattr(model.config, "pad_token_id", 0)
            self.blank_id = int(pad_id) if pad_id is not None else 0

            try:
                self.torch_model = model.to(device=torch.device(self.device), dtype=self._torch_dtype)
            except Exception as move_err:
                print(
                    f"[Zeno Aligner] Device '{self.device}' ({dtype_label}) load failed ({move_err}); "
                    "falling back to CPU fp32.",
                    file=sys.stderr,
                )
                self.device = "cpu"
                self.use_fp16 = False
                self._torch_dtype = torch.float32
                self.thermal_profile = "cpu_safe"
                self._apply_thread_cap()
                self.torch_model = model.to(device=torch.device("cpu"), dtype=torch.float32)

            self.torch_model.eval()
            print(
                f"[Zeno Aligner] Acoustic model ready "
                f"(vocab size: {len(self.dictionary)}, blank_id: {self.blank_id})."
            )

    def unload_model(self) -> None:
        """Unload PyTorch weights and release cached memory back to the OS."""
        with self._infer_lock:
            if self.torch_model is not None:
                del self.torch_model
                self.torch_model = None
                _free_gpu_cache(self.device)
                gc.collect()
                try:
                    libc = ctypes.CDLL("libc.so.6")
                    libc.malloc_trim(0)
                except Exception:
                    pass
                print("[Zeno Aligner] Alignment model unloaded.")

    def _fallback_to_cpu_fp32(self, reason: str) -> None:
        """Relocate loaded weights to CPU float32 after a GPU/MPS error or non-finite logits."""
        with self._infer_lock:
            if self.torch_model is None:
                return
            if self.device == "cpu" and self._torch_dtype == torch.float32:
                return
            print(
                f"[Zeno Aligner] Device '{self.device}' ({self._torch_dtype}) failed ({reason}); "
                "falling back to CPU float32.",
                file=sys.stderr,
            )
            _free_gpu_cache(self.device)
            self.device = "cpu"
            self.use_fp16 = False
            self._torch_dtype = torch.float32
            self.thermal_profile = "cpu_safe"
            self._apply_thread_cap()
            self.torch_model = self.torch_model.to(device=torch.device("cpu"), dtype=torch.float32)
            self.torch_model.eval()

    def _forward_chunk(self, norm_chunk: np.ndarray) -> np.ndarray:
        """Run one normalized float32 audio chunk through omniASR-CTC -> `[T, V]` log_probs."""
        chunk = np.ascontiguousarray(norm_chunk, dtype=np.float32)
        # Feature encoder requires >= 400 samples (25 ms) for its Conv1d stack.
        if 0 < chunk.size < 400:
            chunk = np.pad(chunk, (0, 400 - chunk.size), mode="constant")

        with self._infer_lock:
            def _run_on_current_device() -> np.ndarray:
                dev = next(self.torch_model.parameters()).device
                dtype = next(self.torch_model.parameters()).dtype
                tensor_wave = torch.from_numpy(chunk).unsqueeze(0).to(device=dev, dtype=dtype)
                outputs = None
                logits = None
                try:
                    with torch.inference_mode():
                        outputs = self.torch_model(input_values=tensor_wave)
                        logits = outputs.logits
                        if not bool(torch.isfinite(logits).all().item()):
                            raise RuntimeError("Non-finite logits detected in omniASR forward pass")
                        log_probs = torch.nn.functional.log_softmax(logits.float(), dim=-1)[0].cpu().numpy()
                finally:
                    del tensor_wave, outputs, logits

                log_probs = np.asarray(log_probs, dtype=np.float32)
                if not np.all(np.isfinite(log_probs)):
                    raise RuntimeError("Non-finite log_probs detected after softmax")
                return log_probs

            try:
                return _run_on_current_device()
            except Exception as exc:
                if self.device != "cpu" or self._torch_dtype != torch.float32:
                    self._fallback_to_cpu_fp32(str(exc))
                    return _run_on_current_device()
                raise

    def _extract_emissions_from_waveform(self, raw_waveform: np.ndarray) -> Tuple[np.ndarray, float, float]:
        """Normalize `raw_waveform`, run single-pass or 30s-chunked inference, and return `(log_probs, dur, tpf)`."""
        audio = np.asarray(raw_waveform, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            vocab_size = max(1, len(self.dictionary))
            return np.zeros((0, vocab_size), dtype=np.float32), 0.0, 0.02

        if not np.all(np.isfinite(audio)):
            audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)

        num_samples = int(audio.size)
        duration_sec = float(num_samples) / float(self.sample_rate)

        # Global zero-mean unit-variance normalization preserves loudness balance across chunks.
        if num_samples > 1:
            mean = float(np.mean(audio))
            std = float(np.sqrt(float(np.var(audio)) + 1e-7))
            norm_audio = ((audio - mean) / std).astype(np.float32, copy=False)
        else:
            norm_audio = audio

        self._thermal_check_and_cool()

        windows = _compute_chunk_windows(
            num_samples=num_samples,
            sr=self.sample_rate,
            chunk_sec=self.chunk_sec,
            overlap_sec=self.overlap_sec,
            max_single_pass_sec=self.max_single_pass_sec,
        )

        if len(windows) == 1:
            log_probs = self._forward_chunk(norm_audio)
        else:
            pieces: List[np.ndarray] = []
            for win_idx, (win_start, win_end, core_start, core_end) in enumerate(windows):
                if win_idx > 0:
                    self._thermal_check_and_cool()
                chunk_lp = self._forward_chunk(norm_audio[win_start:win_end])
                t_win = int(chunk_lp.shape[0])
                win_len = max(1, win_end - win_start)
                f_start = 0 if core_start == win_start else int(round(t_win * (core_start - win_start) / win_len))
                f_end = t_win if core_end == win_end else int(round(t_win * (core_end - win_start) / win_len))
                f_start = max(0, min(t_win - 1, f_start))
                f_end = max(f_start + 1, min(t_win, f_end))
                pieces.append(chunk_lp[f_start:f_end])
            log_probs = (
                np.concatenate(pieces, axis=0)
                if pieces
                else np.zeros((0, max(1, len(self.dictionary))), dtype=np.float32)
            )

        num_frames = int(log_probs.shape[0])
        time_per_frame = (duration_sec / float(num_frames)) if num_frames > 0 else 0.02
        return log_probs, duration_sec, time_per_frame

    def extract_emissions(self, audio_path: str) -> Tuple[np.ndarray, np.ndarray, float, float]:
        """Extract frame-level CTC log-probabilities across an entire audio file."""
        self._ensure_model_loaded()

        try:
            raw_waveform, sr = sf.read(audio_path, dtype="float32")
            if len(raw_waveform.shape) > 1:
                raw_waveform = np.mean(raw_waveform, axis=1)
            if sr != self.sample_rate:
                import librosa

                raw_waveform = librosa.resample(raw_waveform, orig_sr=sr, target_sr=self.sample_rate)
        except Exception:
            import librosa

            raw_waveform, sr = librosa.load(audio_path, sr=self.sample_rate, mono=True)

        log_probs, duration_sec, time_per_frame = self._extract_emissions_from_waveform(raw_waveform)
        return log_probs, raw_waveform, duration_sec, time_per_frame

    def extract_emissions_range(
        self,
        audio_path: str,
        start_sec: float,
        end_sec: float,
        pad_sec: float = 0.35,
        total_duration: Optional[float] = None,
    ) -> Tuple[np.ndarray, np.ndarray, float, float, float]:
        """Extract CTC log-probability emissions for a bounded `[start_sec, end_sec]` window.

        Applies symmetric padding around the window so vocal attacks and syllable codas are
        preserved, seeking directly on disk to keep memory bounded on long recordings.

        Returns:
            `(log_probs, raw_waveform_slice, slice_duration_sec, time_per_frame, actual_start_sec)`
        """
        self._ensure_model_loaded()

        actual_start = max(0.0, float(start_sec) - float(pad_sec))
        if total_duration is not None and total_duration > 0:
            actual_end = min(float(total_duration), float(end_sec) + float(pad_sec))
        else:
            actual_end = float(end_sec) + float(pad_sec)

        if actual_end <= actual_start + 0.1:
            actual_end = actual_start + 0.5

        raw_waveform = None
        try:
            info = sf.info(audio_path)
            file_sr = info.samplerate
            start_frame = max(0, int(actual_start * file_sr))
            stop_frame = min(info.frames, int(actual_end * file_sr))
            if stop_frame > start_frame:
                raw_waveform, sr = sf.read(audio_path, start=start_frame, stop=stop_frame, dtype="float32")
                if len(raw_waveform.shape) > 1:
                    raw_waveform = np.mean(raw_waveform, axis=1)
                if sr != self.sample_rate:
                    import librosa

                    raw_waveform = librosa.resample(raw_waveform, orig_sr=sr, target_sr=self.sample_rate)
        except Exception:
            pass

        if raw_waveform is None or len(raw_waveform) == 0:
            import librosa

            dur = max(0.2, actual_end - actual_start)
            raw_waveform, sr = librosa.load(
                audio_path, offset=actual_start, duration=dur, sr=self.sample_rate, mono=True
            )

        log_probs, slice_duration_sec, time_per_frame = self._extract_emissions_from_waveform(raw_waveform)
        return log_probs, raw_waveform, slice_duration_sec, time_per_frame, actual_start


class AudioCaptionAlignmentAgent:
    """Anchors caption cues to detected speech islands using cumulative syllable weighting."""

    def __init__(
        self,
        min_word_dur_sec: float = 0.15,
        max_word_dur_sec: float = 0.75,
        silence_threshold_sec: float = 0.30,
        char_base_rate_sec: float = 0.055,
    ):
        self.min_word_dur_sec = min_word_dur_sec
        self.max_word_dur_sec = max_word_dur_sec
        self.silence_threshold_sec = silence_threshold_sec
        self.char_base_rate_sec = char_base_rate_sec

    def estimate_syllables(self, word: str) -> int:
        """Estimate syllable count for Latin and Indic abugida words (Kannada, Telugu, Tamil, Malayalam, Hindi, etc.)."""
        if not word:
            return 1
        w = re.sub(r"[^a-zA-Z0-9]", "", str(word).lower())
        if not w:
            letters = len(re.findall(r"[\u0900-\u0D7F]", str(word)))
            viramas = len(re.findall(r"[\u094D\u09CD\u0A4D\u0ACD\u0B4D\u0BCD\u0C4D\u0CCD\u0D4D]", str(word)))
            return max(1, letters - viramas)
        if len(w) <= 3:
            return 1
        matches = re.findall(r"[aeiouy]{1,2}", w)
        return max(1, len(matches)) if matches else 1

    def align_cues(
        self,
        cues: list,
        speech_islands: Optional[list] = None,
        timeline_duration_sec: float = 0.0,
        is_demo: bool = False,
        was_chunked: bool = False,
    ) -> list:
        """Align subtitle cues against speech islands, or fall back to proportional timing."""
        if not cues:
            return []

        if not is_demo and speech_islands and len(speech_islands) > 0:
            return self.align_with_speech_islands(cues, speech_islands)

        aligned = [dict(c) for c in cues]
        total_duration = (
            timeline_duration_sec
            if timeline_duration_sec > 0
            else float(aligned[-1].get("end", len(aligned) * 0.5))
        )
        global_shift = -0.150 if was_chunked else 0.0

        for i in range(len(aligned)):
            current = aligned[i]
            next_cue = aligned[i + 1] if i + 1 < len(aligned) else None
            start = float(current.get("start", current.get("startTime", 0.0)))
            end = float(current.get("end", current.get("endTime", start + 0.5)))

            if next_cue:
                next_start = float(next_cue.get("start", next_cue.get("startTime", end)))
                if end > next_start:
                    end = next_start
                gap = next_start - end
                if 0 < gap < self.silence_threshold_sec:
                    end = next_start
            else:
                if total_duration > 0 and end > total_duration:
                    end = total_duration

            current["start"] = round(start, 3)
            current["end"] = round(max(start + self.min_word_dur_sec, end), 3)

        scaled = self.compensate_acoustic_drift(aligned, total_duration)
        if global_shift != 0.0:
            for c in scaled:
                s = max(0.0, c["start"] + global_shift)
                e = max(s + self.min_word_dur_sec, c["end"] + global_shift)
                c["start"] = round(s, 3)
                c["end"] = round(e, 3)

        return scaled

    def align_with_speech_islands(self, cues: list, islands: list) -> list:
        """Anchor cues inside speech islands using syllable density and boundary rebalancing."""
        words = []
        for c in cues:
            w_dict = dict(c)
            w_dict["text"] = str(w_dict.get("word", w_dict.get("text", "")))
            words.append(w_dict)

        if not words or not islands:
            return words

        total_speech_sec = sum(isl["end"] - isl["start"] for isl in islands)
        if total_speech_sec <= 0:
            return words

        by_island: List[List[Dict[str, Any]]] = [[] for _ in islands]
        weights = [max(1, self.estimate_syllables(w["text"])) for w in words]
        total_weight = sum(weights)

        island_idx = 0
        island_end_cumulative = islands[0]["end"] - islands[0]["start"]
        current_weight_sum = 0

        for i, w in enumerate(words):
            word_weight = weights[i]
            pos = ((current_weight_sum + word_weight / 2.0) / total_weight) * total_speech_sec
            current_weight_sum += word_weight

            while island_idx < len(islands) - 1 and pos >= island_end_cumulative:
                island_idx += 1
                island_end_cumulative += islands[island_idx]["end"] - islands[island_idx]["start"]
            by_island[island_idx].append(w)

        for _ in range(4):
            changed = False
            densities = []
            for i, isl in enumerate(islands):
                if not by_island[i]:
                    densities.append(0.0)
                    continue
                wts = [max(1, self.estimate_syllables(w["text"])) for w in by_island[i]]
                dur = isl["end"] - isl["start"]
                densities.append(sum(wts) / dur if dur > 0 else 0.0)

            non_zero = sorted(d for d in densities if d > 0)
            if not non_zero:
                break
            median_density = non_zero[len(non_zero) // 2]

            for i in range(len(islands) - 1):
                island_a = by_island[i]
                island_b = by_island[i + 1]
                if not island_a or not island_b:
                    continue
                den_a = densities[i]
                den_b = densities[i + 1]
                if den_a > median_density * 2.5 and den_b < median_density * 0.4:
                    island_b.insert(0, island_a.pop())
                    changed = True
                elif den_b > median_density * 2.5 and den_a < median_density * 0.4:
                    island_a.append(island_b.pop(0))
                    changed = True
            if not changed:
                break

        result = []
        for i, isl in enumerate(islands):
            island_words = by_island[i]
            if not island_words:
                continue

            isl_start = isl["start"]
            isl_end = isl["end"]
            isl_dur = max(0.05, isl_end - isl_start)

            if len(island_words) == 1:
                w = island_words[0]
                w["start"] = round(isl_start, 3)
                w["end"] = round(isl_end, 3)
                result.append(w)
                continue

            w_weights = [max(1, self.estimate_syllables(w["text"])) for w in island_words]
            isl_total_weight = sum(w_weights)
            max_possible_min_dur = isl_dur / len(island_words)
            dynamic_min_dur = min(self.min_word_dur_sec, max_possible_min_dur)

            cursor = isl_start
            for idx, w in enumerate(island_words):
                share = (w_weights[idx] / isl_total_weight) * isl_dur
                start = cursor
                max_allowed_end = isl_end - (len(island_words) - 1 - idx) * dynamic_min_dur
                end = min(max_allowed_end, start + max(dynamic_min_dur, share))
                cursor = end
                w["start"] = round(start, 3)
                w["end"] = round(end, 3)
                result.append(w)

        result.sort(key=lambda x: x.get("start", 0.0))
        return result

    def compensate_acoustic_drift(self, cues: list, total_duration_sec: float) -> list:
        """Scale timestamps proportionally if cumulative timing exceeds timeline duration."""
        if not total_duration_sec or total_duration_sec <= 0 or not cues:
            return cues
        last_end = float(cues[-1].get("end", 0.0))
        if last_end > total_duration_sec + 1.0:
            scale_ratio = total_duration_sec / last_end
            for c in cues:
                c["start"] = round(float(c.get("start", 0.0)) * scale_ratio, 3)
                c["end"] = round(float(c.get("end", 0.0)) * scale_ratio, 3)
        return cues
