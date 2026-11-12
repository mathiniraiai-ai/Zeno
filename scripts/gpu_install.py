#!/usr/bin/env python3
"""Install the right PyTorch build and NVENC FFmpeg for this computer's GPU, keeping CUDA builds intact.

Windows/Linux with an NVIDIA GPU: PyPI only has CPU torch on Windows, so the CUDA build comes from download.pytorch.org,
and if system FFmpeg lacks `h264_nvenc`, a static NVENC-enabled FFmpeg is installed into `tools/ffmpeg/`.
Mac (Apple Silicon): the normal PyPI build already uses the Apple GPU (MPS) and VideoToolbox (`h264_videotoolbox`).

    python scripts/gpu_install.py                # install requirements (keeps CUDA torch), then ensure CUDA torch + NVENC FFmpeg
    python scripts/gpu_install.py --ensure-gpu   # fast check/upgrade of CUDA torch + NVENC FFmpeg only (used by OTA/preflight)
    python scripts/gpu_install.py --status       # what torch/GPU/encoder this computer uses
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
INDEX = "https://download.pytorch.org/whl/"
PYPI_SIMPLE = "https://pypi.org/simple"

# Live-verified cp312 Windows/Linux CUDA wheel pairs on download.pytorch.org/whl/<tag>/
CUDA_WHEEL_CANDIDATES: Dict[str, List[Tuple[str, str]]] = {
    "cu130": [
        ("torch==2.14.1+cu130", "torchaudio==2.11.0+cu130"),
        ("torch==2.9.1+cu130", "torchaudio==2.9.1+cu130"),
        ("torch", "torchaudio"),
    ],
    "cu128": [
        ("torch==2.9.1+cu128", "torchaudio==2.9.1+cu128"),
        ("torch", "torchaudio"),
    ],
    "cu126": [
        ("torch==2.9.1+cu126", "torchaudio==2.9.1+cu126"),
        ("torch", "torchaudio"),
    ],
}


def _py() -> str:
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        p = ROOT / rel
        if p.is_file():
            return str(p)
    return sys.executable


def _find_uv() -> Optional[str]:
    found = shutil.which("uv")
    if found:
        return found
    home = Path.home()
    ext = ".exe" if (sys.platform.startswith("win") or os.name == "nt") else ""
    for cand in (
        home / ".local" / "bin" / f"uv{ext}",
        home / ".cargo" / "bin" / f"uv{ext}",
    ):
        if cand.is_file():
            return str(cand)
    return None


def find_nvidia_smi() -> Optional[str]:
    """Locate `nvidia-smi` across PATH and standard Windows driver directories."""
    exe = shutil.which("nvidia-smi")
    if exe:
        return exe
    if sys.platform.startswith("win") or os.name == "nt":
        sys_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        prog_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        for cand in (
            sys_root / "System32" / "nvidia-smi.exe",
            prog_files / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
        ):
            if cand.is_file():
                return str(cand)
    else:
        for cand in (Path("/usr/bin/nvidia-smi"), Path("/usr/local/nvidia/bin/nvidia-smi")):
            if cand.is_file():
                return str(cand)
    return None


def cuda_tag(nvidia_smi_text: str) -> Optional[str]:
    """PyTorch wheel tag for the CUDA version the NVIDIA driver supports (None if unknown or too old).

    Handles both classic `CUDA Version: 12.8` and newer 610.x driver headers like `CUDA UMD Version: 13.3`.
    """
    m = re.search(r"CUDA(?:\s+\w+)*\s+Version:\s*(\d+)\.(\d+)", nvidia_smi_text or "", flags=re.IGNORECASE)
    if not m:
        return None
    major, minor = int(m.group(1)), int(m.group(2))
    if major >= 13:
        return "cu130"
    if (major, minor) >= (12, 8):
        return "cu128"
    if (major, minor) >= (12, 6):
        return "cu126"
    return None


def detect_tag() -> Optional[str]:
    """Detect the best PyTorch CUDA wheel tag (`cu130`, `cu128`, `cu126`) for this machine's NVIDIA GPU."""
    exe = find_nvidia_smi()
    if not exe:
        return None
    try:
        out = subprocess.run([exe], capture_output=True, text=True, timeout=15).stdout or ""
    except Exception:
        return None
    tag = cuda_tag(out)
    if tag:
        return tag
    # Fallback: if `nvidia-smi -L` lists a modern NVIDIA RTX/GTX GPU without a standard banner header, try cu128
    try:
        lst = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=10).stdout or ""
        if re.search(r"GPU\s+\d+:\s*NVIDIA", lst, flags=re.IGNORECASE):
            return "cu128"
    except Exception:
        pass
    return None


def installed_torch(py: str) -> str:
    r = subprocess.run([py, "-c", "import torch;print(torch.__version__)"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def is_cuda_ready(py: str) -> bool:
    code = "import torch,sys;sys.exit(0 if ('+cu' in torch.__version__ and torch.cuda.is_available()) else 1)"
    return subprocess.run([py, "-c", code], capture_output=True, text=True).returncode == 0


def requirements_without_torch(src: Path) -> List[str]:
    return [ln for ln in src.read_text(encoding="utf-8").splitlines() if not re.match(r"\s*(torch|torchaudio)\s*[=<>!~]", ln)]


def _uv(*args: str) -> int:
    uv = _find_uv()
    py = _py()
    if uv:
        return subprocess.run([uv, "pip", "install", "--python", py, *args]).returncode
    # Translate uv-only flags so fallback `python -m pip install` never fails with `no such option`
    pip_args: List[str] = []
    skip_next = False
    force_reinstall = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a == "--reinstall":
            force_reinstall = True
            continue
        if a == "--reinstall-package":
            force_reinstall = True
            skip_next = True
            continue
        pip_args.append(a)
    if force_reinstall:
        pip_args = ["--upgrade", "--force-reinstall", *pip_args]
    return subprocess.run([py, "-m", "pip", "install", *pip_args]).returncode


def _install_cuda_pair(py: str, tag: str, torch_spec: str, ta_spec: str) -> int:
    index = INDEX + tag
    uv = _find_uv()
    if uv:
        return subprocess.run(
            [
                uv,
                "pip",
                "install",
                "--python",
                py,
                "--index-url",
                index,
                "--extra-index-url",
                PYPI_SIMPLE,
                "--reinstall-package",
                "torch",
                "--reinstall-package",
                "torchaudio",
                torch_spec,
                ta_spec,
            ]
        ).returncode
    subprocess.run([py, "-m", "pip", "uninstall", "-y", "torch", "torchaudio"], capture_output=True)
    return subprocess.run(
        [
            py,
            "-m",
            "pip",
            "install",
            "--index-url",
            index,
            "--extra-index-url",
            PYPI_SIMPLE,
            torch_spec,
            ta_spec,
        ]
    ).returncode


def install_requirements() -> int:
    """Install `requirements.txt`, preserving any existing CUDA PyTorch build."""
    req = ROOT / "requirements.txt"
    if "+cu" in installed_torch(_py()):
        tmp = Path(tempfile.mkdtemp()) / "requirements_no_torch.txt"
        tmp.write_text("\n".join(requirements_without_torch(req)) + "\n", encoding="utf-8")
        rc = _uv("-r", str(tmp))
    else:
        rc = _uv("-r", str(req))
    # Silero VAD (MIT, 2 MB): finds speech over background noise without onnxruntime.
    try:
        _uv("--no-deps", "silero-vad==6.2.3")
    except Exception:
        pass
    return rc


def _tag_fallback_chain(primary: str) -> Sequence[str]:
    order = ["cu130", "cu128", "cu126"]
    if primary in order:
        return order[order.index(primary) :]
    return [primary]


def ensure_cuda_torch() -> str:
    """Switch to the CUDA build when an NVIDIA GPU is present. Returns a one-line result; failure keeps a working torch."""
    if sys.platform == "darwin":
        return "Apple GPU (MPS) is used by the standard PyTorch build; nothing to install."
    primary_tag = detect_tag()
    if not primary_tag:
        return "no usable NVIDIA GPU/driver found (needs a driver with CUDA 12.6 or newer); using CPU torch."
    py = _py()
    cur_ver = installed_torch(py)
    if "+cu" in cur_ver and is_cuda_ready(py):
        return f"CUDA torch ({cur_ver}) already installed and active on GPU."

    for tag in _tag_fallback_chain(primary_tag):
        pairs = CUDA_WHEEL_CANDIDATES.get(tag, [("torch", "torchaudio")])
        for torch_spec, ta_spec in pairs:
            rc = _install_cuda_pair(py, tag, torch_spec, ta_spec)
            if rc == 0 and is_cuda_ready(py):
                ver = installed_torch(py)
                return f"CUDA torch ({ver}) installed; the NVIDIA GPU is active."

    # Restore working CPU torch if all CUDA tags failed
    _uv("-r", str(ROOT / "requirements.txt"), "--reinstall-package", "torch", "--reinstall-package", "torchaudio")
    return "CUDA torch did not work on this PC (driver mismatch); restored CPU torch."


def ensure_nvenc_ffmpeg() -> str:
    """On Windows with an NVIDIA GPU, ensure FFmpeg includes `h264_nvenc` (downloading gyan.dev static build if system FFmpeg lacks it)."""
    if not (sys.platform.startswith("win") or os.name == "nt"):
        return ""
    if not find_nvidia_smi():
        return ""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import common

        ff = common.resolve_ffmpeg()
        if ff and Path(ff).is_file():
            enc_out = subprocess.run([str(ff), "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10).stdout or ""
            if "h264_nvenc" in enc_out:
                return f"FFmpeg NVENC ready ({ff})"
    except Exception:
        pass

    tools_dir = ROOT / "tools" / "ffmpeg"
    ff_dest = tools_dir / "ffmpeg.exe"
    fp_dest = tools_dir / "ffprobe.exe"
    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    try:
        tools_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="zeno_ffmpeg_") as tmp_s:
            zip_path = Path(tmp_s) / "ffmpeg.zip"
            req = urllib.request.Request(url, headers={"User-Agent": "Zeno-GPU-Installer/1.0"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                zip_path.write_bytes(resp.read())
            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.namelist():
                    low = member.replace("\\", "/").lower()
                    if low.endswith("/bin/ffmpeg.exe"):
                        ff_dest.write_bytes(zf.read(member))
                    elif low.endswith("/bin/ffprobe.exe"):
                        fp_dest.write_bytes(zf.read(member))
        if ff_dest.is_file() and fp_dest.is_file():
            try:
                import common

                common.FFMPEG = common.resolve_ffmpeg()
                common.FFPROBE = common.resolve_ffprobe()
                common._HW_ENCODER.clear()
            except Exception:
                pass
            return f"Installed NVENC-enabled FFmpeg in {ff_dest}"
    except Exception as exc:
        return f"Could not auto-download NVENC FFmpeg ({type(exc).__name__}); install.bat can retry."
    return ""


def main() -> None:
    if "--status" in sys.argv:
        sys.path.insert(0, str(ROOT / "scripts"))
        import common

        print(common.gpu_report())
        return
    if "--ensure-gpu" in sys.argv:
        msg_torch = ensure_cuda_torch()
        msg_ff = ensure_nvenc_ffmpeg()
        print("  " + msg_torch)
        if msg_ff:
            print("  " + msg_ff)
        return
    rc = install_requirements()
    print("  " + ensure_cuda_torch())
    msg_ff = ensure_nvenc_ffmpeg()
    if msg_ff:
        print("  " + msg_ff)
    sys.exit(0 if rc == 0 else rc)


if __name__ == "__main__":
    main()
