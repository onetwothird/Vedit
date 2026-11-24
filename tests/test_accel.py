import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import accel  # noqa: E402
import proxy  # noqa: E402


def test_gpu_runs_the_model_asked_for_with_int8_weights():
    plan = accel.speech_plan("large-v3", on_gpu=True, installed=lambda name: False)
    assert plan == {"model": "large-v3", "device": "cuda", "compute": "int8_float16", "batch": accel.GPU_BATCH}


def test_cpu_swaps_large_v3_for_turbo_when_it_is_downloaded():
    plan = accel.speech_plan("large-v3", on_gpu=False, installed=lambda name: name == "large-v3-turbo")
    assert plan["model"] == "large-v3-turbo"
    assert (plan["device"], plan["compute"]) == ("cpu", "int8")


def test_cpu_keeps_large_v3_when_turbo_is_missing():
    assert accel.speech_plan("large-v3", on_gpu=False, installed=lambda name: False)["model"] == "large-v3"


def test_cpu_keeps_models_with_no_faster_stand_in():
    assert accel.speech_plan("small", on_gpu=False, installed=lambda name: True)["model"] == "small"


def test_device_cpu_env_turns_the_gpu_off(monkeypatch):
    monkeypatch.setenv("VEDIT_DEVICE", "cpu")
    accel.gpu_ready.cache_clear()
    accel.nvenc_ready.cache_clear()
    try:
        assert accel.gpu_ready() is False
        assert accel.nvenc_ready("ffmpeg") is False
    finally:
        accel.gpu_ready.cache_clear()
        accel.nvenc_ready.cache_clear()


def test_nvenc_is_off_when_ffmpeg_cannot_run():
    accel.nvenc_ready.cache_clear()
    try:
        assert accel.nvenc_ready(str(Path("no-such-dir") / "ffmpeg-missing")) is False
    finally:
        accel.nvenc_ready.cache_clear()


def test_proxy_codec_keeps_a_keyframe_every_second_on_both_encoders():
    gpu = proxy.video_codec("ffmpeg", "30", gpu=True)
    cpu = proxy.video_codec("ffmpeg", "30", gpu=False)
    assert gpu[gpu.index("-c:v") + 1] == "h264_nvenc" and gpu[gpu.index("-g") + 1] == "30"
    assert "-no-scenecut" in gpu
    assert cpu[cpu.index("-c:v") + 1] == "libx264" and cpu[cpu.index("-g") + 1] == "30"
    assert cpu[cpu.index("-sc_threshold") + 1] == "0"


def test_turbo_is_a_managed_model():
    import models

    assert models.MODELS["large-v3-turbo"]["for"] == ["tl", "taglish"]
