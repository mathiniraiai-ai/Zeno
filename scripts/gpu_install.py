#!/usr/bin/env python3
"""Install the right PyTorch build for this computer's GPU, and install requirements without undoing it.

Windows/Linux with an NVIDIA GPU: PyPI only has CPU torch, so the CUDA build comes from download.pytorch.org.
Mac (Apple Silicon): the normal PyPI build already uses the Apple GPU (MPS); nothing to do.

    python scripts/gpu_install.py            # install requirements (keeps a CUDA torch), then switch to CUDA torch if an NVIDIA GPU is present
    python scripts/gpu_install.py --status   # what torch/GPU/encoder this computer uses
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
TORCH_VERSION = "2.14.1"          # newest release that has CUDA wheels (PyPI's 2.15 is CPU-only on Windows)
INDEX = "https://download.pytorch.org/whl/"


def _py() -> str:
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        p = ROOT / rel
        if p.is_file():
            return str(p)
    return sys.executable


def cuda_tag(nvidia_smi_text: str) -> Optional[str]:
    """PyTorch wheel tag for the CUDA version the NVIDIA driver supports (None if unknown or too old)."""
    m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", nvidia_smi_text or "")
    if not m:
        return None
    major, minor = int(m.group(1)), int(m.group(2))
    if major >= 13:
        return "cu130"
    if (major, minor) >= (12, 6):
        return "cu126"
    return None


def detect_tag() -> Optional[str]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return None
    return cuda_tag(out)


def installed_torch(py: str) -> str:
    r = subprocess.run([py, "-c", "import torch;print(torch.__version__)"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def requirements_without_torch(src: Path) -> List[str]:
    return [ln for ln in src.read_text(encoding="utf-8").splitlines() if not re.match(r"\s*(torch|torchaudio)\s*[=<>!~]", ln)]


def _uv(*args: str) -> int:
    uv = shutil.which("uv")
    if uv:
        return subprocess.run([uv, "pip", "install", "--python", _py(), *args]).returncode
    return subprocess.run([_py(), "-m", "pip", "install", *args]).returncode


def install_requirements() -> int:
    """requirements.txt, but leave a CUDA torch alone (the file lists the CPU build)."""
    req = ROOT / "requirements.txt"
    if "+cu" in installed_torch(_py()):
        tmp = Path(tempfile.mkdtemp()) / "requirements_no_torch.txt"
        tmp.write_text("\n".join(requirements_without_torch(req)) + "\n", encoding="utf-8")
        return _uv("-r", str(tmp))
    return _uv("-r", str(req))


def ensure_cuda_torch() -> str:
    """Switch to the CUDA build when an NVIDIA GPU is present. Returns a one-line result; a failure keeps the working CPU build."""
    if sys.platform == "darwin":
        return "Apple GPU (MPS) is used by the standard PyTorch build; nothing to install."
    tag = detect_tag()
    if not tag:
        return "no usable NVIDIA GPU/driver found (needs a driver with CUDA 12.6 or newer); using CPU torch."
    py = _py()
    if "+" + tag in installed_torch(py):
        return f"CUDA torch ({tag}) already installed."
    index = INDEX + tag
    for ta in (f"torchaudio=={TORCH_VERSION}+{tag}", "torchaudio"):
        rc = _uv("--index-url", index, "--no-deps", "--reinstall", f"torch=={TORCH_VERSION}+{tag}", ta)
        if rc == 0:
            break
    ok = subprocess.run([py, "-c", "import torch,sys;sys.exit(0 if torch.cuda.is_available() else 1)"]).returncode == 0
    if ok:
        return f"CUDA torch ({tag}) installed; the GPU is used."
    _uv("-r", str(ROOT / "requirements.txt"), "--reinstall-package", "torch", "--reinstall-package", "torchaudio")
    return "CUDA torch did not work on this PC (driver?); went back to CPU torch."


def main() -> None:
    if "--status" in sys.argv:
        sys.path.insert(0, str(ROOT / "scripts"))
        import common

        print(common.gpu_report())
        return
    rc = install_requirements()
    print("  " + ensure_cuda_torch())
    sys.exit(0 if rc == 0 else rc)


if __name__ == "__main__":
    main()
