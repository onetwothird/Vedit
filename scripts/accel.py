"""Hardware the engine can speed up with: an NVIDIA GPU for speech recognition and for the preview encode.

faster-whisper runs on CTranslate2, which sees an NVIDIA card on its own: PyTorch is not needed (a CPU-only PyTorch
used to hide the card). On Windows the GPU also needs NVIDIA's CUDA 12 cuBLAS and cuDNN 9 libraries; they come from
the `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` pip wheels or a folder of their DLLs at $VEDIT_CUDA
(default: <models folder>/../cuda). Without them, or without a card, everything runs on the CPU as before.
"""
from __future__ import annotations

import functools
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

CUDA_DLLS = ("cublas64_12.dll", "cudnn64_9.dll")  # what CTranslate2 4.x loads on Windows
GPU_BATCH = 8
CPU_BATCH = 8
# a model too slow for a CPU and the close, faster model to use there instead (when it is downloaded)
CPU_SWAP = {"large-v3": "large-v3-turbo"}


def cuda_dirs() -> list[Path]:
    """Folders that may hold the CUDA DLLs: $VEDIT_CUDA (or the managed folder beside the models), then the bin
    folders of the nvidia-* pip wheels."""
    import models

    out = [Path(os.environ.get("VEDIT_CUDA") or models.model_dir().parent / "cuda")]
    try:
        spec = importlib.util.find_spec("nvidia")
    except (ImportError, ValueError):
        spec = None
    for root in (spec.submodule_search_locations or []) if spec else []:
        for sub in ("cublas", "cudnn", "cuda_nvrtc"):
            out.append(Path(root) / sub / "bin")
    return [d for d in out if d.is_dir()]


@functools.lru_cache(maxsize=None)
def _load_cuda_dlls() -> bool:
    """Put the CUDA DLL folders on the DLL search path; True when every library CTranslate2 needs can be loaded."""
    if sys.platform != "win32":
        return True  # Linux finds libcublas/libcudnn through the usual library path
    import ctypes

    for d in cuda_dirs():
        try:
            os.add_dll_directory(str(d))
        except OSError:
            continue
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    try:
        for name in CUDA_DLLS:
            ctypes.WinDLL(name)
    except OSError:
        return False
    return True


@functools.lru_cache(maxsize=None)
def gpu_ready() -> bool:
    """True when speech recognition can run on an NVIDIA GPU. VEDIT_DEVICE=cpu turns the GPU off."""
    if os.environ.get("VEDIT_DEVICE", "").lower() == "cpu":
        return False
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() < 1:
            return False
    except Exception:  # noqa: BLE001 - no ctranslate2, or a driver that cannot answer: CPU
        return False
    return _load_cuda_dlls()


def speech_plan(model: str, on_gpu: bool, installed=None) -> dict:
    """How to run `model`: on the GPU as asked (int8 weights, float16 maths: large-v3 fits a 4 GB card); on a CPU,
    the faster stand-in from CPU_SWAP when it is downloaded. `installed(name)` says whether a model is downloaded."""
    if on_gpu:
        return {"model": model, "device": "cuda", "compute": "int8_float16", "batch": GPU_BATCH}
    if installed is None:
        import models

        installed = models.is_installed
    swap = CPU_SWAP.get(model)
    use = swap if swap and installed(swap) else model
    return {"model": use, "device": "cpu", "compute": "int8", "batch": CPU_BATCH}


@functools.lru_cache(maxsize=None)
def nvenc_ready(ffmpeg: str) -> bool:
    """True when this ffmpeg can encode H.264 on an NVIDIA GPU (the encoder is listed AND a real encode works:
    builds list h264_nvenc even on machines without a card). VEDIT_DEVICE=cpu turns it off."""
    if os.environ.get("VEDIT_DEVICE", "").lower() == "cpu":
        return False
    try:
        r = subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.2", "-c:v", "h264_nvenc",
                            "-f", "null", "-"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0
