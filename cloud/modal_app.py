"""Vedit on Modal: the engine's API (FastAPI, cloud mode) and the functions its job steps run on.

    modal deploy cloud/modal_app.py             # from the repo root
    modal run cloud/modal_app.py::download_models   # once: Whisper models onto the volume

Needs the Modal secret "vedit-cloud" with: SUPABASE_URL, SUPABASE_JWT_SECRET (or JWKS), SUPABASE_SECRET_KEY,
R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET (default vedit-media), VEDIT_ORIGINS, and optionally
VEDIT_GLOBAL_LIMIT_SECONDS and VEDIT_PAUSED=1. Nothing here is imported by the engine or its tests.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
REPO = "/repo"
DATA = "/data"
GPU = "L4"  # the Task 15 cost/speed check may switch this to "T4"

app = modal.App("vedit-engine")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "fonts-dejavu")
    .env({"VEDIT_CLOUD": "1", "VEDIT_DATA_ROOT": DATA, "VEDIT_MODELS": f"{DATA}/models",
          "PYTHONPATH": f"{REPO}/scripts", "PYTHONIOENCODING": "utf-8"})
    # the package is installed from the repo itself (pip install -e .[cloud]); fastapi/uvicorn come with [studio]
    .add_local_file(ROOT / "pyproject.toml", f"{REPO}/pyproject.toml", copy=True)
    .add_local_file(ROOT / "README.md", f"{REPO}/README.md", copy=True)
    .add_local_dir(ROOT / "scripts", f"{REPO}/scripts", copy=True, ignore=["**/__pycache__/**"])
    .run_commands(f"pip install -e '{REPO}[studio,cloud]'")
    .pip_install("faster-whisper", "ctranslate2", "nvidia-cublas-cu12", "nvidia-cudnn-cu12")
    .add_local_dir(ROOT / "fonts", f"{REPO}/fonts")
    .add_local_dir(ROOT / "templates", f"{REPO}/templates")
)

volume = modal.Volume.from_name("vedit-data", create_if_missing=True)
secrets = [modal.Secret.from_name("vedit-cloud")]
# step output lines, one partition per job id (partitions expire on their own a day after their last put)
lines_queue = modal.Queue.from_name("vedit-step-lines", create_if_missing=True)
# the visitor's AI / TTS keys for a step, keyed by job id: the step pops its entry, the API deletes it at job end
secret_dict = modal.Dict.from_name("vedit-step-secrets", create_if_missing=True)

def _nvidia_library_path() -> None:
    """CTranslate2 loads libcublas / libcudnn from the nvidia-* wheels; children inherit this."""
    try:
        import nvidia.cublas.lib
        import nvidia.cudnn.lib
        dirs = [os.path.dirname(nvidia.cublas.lib.__file__), os.path.dirname(nvidia.cudnn.lib.__file__)]
    except Exception:  # noqa: BLE001 - CPU image paths: nothing to add
        return
    os.environ["LD_LIBRARY_PATH"] = ":".join(dirs + [p for p in [os.environ.get("LD_LIBRARY_PATH")] if p])


def _step(job_id: str, stage: str, argv: list[str], env: dict[str, str], home: str | None) -> dict:
    sys.path.insert(0, f"{REPO}/scripts")
    import cloud_runner

    return cloud_runner.execute_step(job_id, stage, argv, env, home,
                                     lines=cloud_runner.ModalLines(lines_queue, job_id), secrets=secret_dict,
                                     commit=volume.commit, reload=volume.reload)


@app.function(image=image, gpu=GPU, timeout=3600, volumes={DATA: volume}, secrets=secrets)
def step_gpu(job_id: str, stage: str, argv: list[str], env: dict[str, str], home: str | None) -> dict:
    _nvidia_library_path()
    return _step(job_id, stage, argv, env, home)


@app.function(image=image, cpu=4, timeout=3600, volumes={DATA: volume}, secrets=secrets)
def step_cpu(job_id: str, stage: str, argv: list[str], env: dict[str, str], home: str | None) -> dict:
    return _step(job_id, stage, argv, env, home)


@app.function(image=image, timeout=3600, volumes={DATA: volume}, secrets=secrets)
def download_models() -> None:
    """Admin: fetch every Whisper model the cloud uses into /data/models (VEDIT_MODELS)."""
    import subprocess

    sys.path.insert(0, f"{REPO}/scripts")
    import models

    Path(f"{DATA}/models").mkdir(parents=True, exist_ok=True)
    for name in models.MODELS:
        subprocess.run([sys.executable, f"{REPO}/scripts/models.py", "download", name], check=True)
    volume.commit()


def _cloud_config():
    sys.path.insert(0, f"{REPO}/scripts")
    import cloud_config
    import cloud_runner
    from cloud_storage import R2Storage
    from cloud_usage import SupabaseUsage

    cfg = cloud_config.from_env()
    if cfg is None:
        raise RuntimeError("VEDIT_CLOUD=1 is not set in the image")
    cfg.storage = R2Storage(os.environ["R2_ACCOUNT_ID"], os.environ.get("R2_BUCKET", "vedit-media"),
                            os.environ["R2_ACCESS_KEY_ID"], os.environ["R2_SECRET_ACCESS_KEY"])
    global_limit = os.environ.get("VEDIT_GLOBAL_LIMIT_SECONDS")
    cfg.usage = SupabaseUsage(cfg.supabase_url, os.environ["SUPABASE_SECRET_KEY"],
                              global_limit=float(global_limit) if global_limit else None)
    cfg.runner_factory = lambda: cloud_runner.ModalRunner(
        step_gpu, step_cpu, lambda job_id: cloud_runner.ModalLines(lines_queue, job_id), secret_dict,
        reload=volume.reload, commit=volume.commit, from_id=modal.FunctionCall.from_id)
    return cfg


def _keep_warm_while_busy(engine, interval: float = 60.0) -> None:
    """The API container follows the running steps: while any job runs, keep one container up (scaledown would
    otherwise stop it after 5 idle minutes, and the next one would have to recover every job)."""
    warm = False

    def loop():
        nonlocal warm
        while True:
            try:
                busy = any(j.state in ("queued", "running") for j in list(engine._jobs.values()))
                if busy != warm:
                    api.update_autoscaler(min_containers=1 if busy else 0)
                    warm = busy
            except Exception as e:  # noqa: BLE001
                print(f"vedit: keep-warm update failed: {e}", file=sys.stderr)
            threading.Event().wait(interval)

    threading.Thread(target=loop, name="vedit-keep-warm", daemon=True).start()


@app.function(image=image, volumes={DATA: volume}, secrets=secrets, max_containers=1, scaledown_window=300,
              timeout=3600)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def api():
    sys.path.insert(0, f"{REPO}/scripts")
    from studio_server import create_app

    web = create_app(None, cloud=_cloud_config())
    _keep_warm_while_busy(web.state.engine)
    return web
