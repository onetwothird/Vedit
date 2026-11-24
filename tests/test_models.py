"""Managed Whisper model downloads: status, download progress, delete, CLI, transcribe model loading."""
import json
import sys
import time
import types
from pathlib import Path

import pytest

import models


@pytest.fixture
def mdir(tmp_path, monkeypatch):
    d = tmp_path / "models"
    monkeypatch.setenv("VEDIT_MODELS", str(d))
    monkeypatch.setenv("VEDIT_PROGRESS", "1")
    return d


def fake_snapshot(delay=0.0):
    def snap(repo, local_dir=None, **kw):
        assert repo == models.MODELS["small"]["repo"]
        for f in models.REQUIRED_FILES:
            (Path(local_dir) / f).write_bytes(b"x" * 1024)
            time.sleep(delay)
        return local_dir
    return snap


def install_fake_hub(monkeypatch, snap):
    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = snap
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)


def pcts(capsys):
    err = capsys.readouterr().err
    return [json.loads(line[len("@@progress "):]) for line in err.splitlines() if line.startswith("@@progress ")]


def test_model_dir_env_and_default(mdir, monkeypatch):
    assert models.model_dir() == mdir
    monkeypatch.delenv("VEDIT_MODELS")
    monkeypatch.setenv("LOCALAPPDATA", "X:/local")
    assert models.model_dir().parts[-2:] == ("vedit", "models")


def test_status_empty(mdir):
    assert models.status() == {"small": {"installed": False, "size_mb": 484},
                               "large-v3": {"installed": False, "size_mb": 3090},
                               "large-v3-turbo": {"installed": False, "size_mb": 1620}}


def test_installed_needs_sentinel_and_files(mdir):
    (mdir / "small").mkdir(parents=True)
    (mdir / "small" / "model.bin").write_bytes(b"x")
    assert not models.is_installed("small")
    (mdir / "small" / "config.json").write_text("{}")
    assert not models.is_installed("small")  # both files but no sentinel: an interrupted download
    models._mark_complete("small")
    assert models.is_installed("small") and models.status()["small"]["installed"]
    (mdir / "small" / "model.bin").unlink()
    assert not models.is_installed("small")


def test_download_emits_progress_ending_at_100(mdir, monkeypatch, capsys):
    monkeypatch.setattr(models, "POLL_SECONDS", 0.01)
    install_fake_hub(monkeypatch, fake_snapshot(delay=0.05))
    out = models.download("small")
    rows = pcts(capsys)
    assert out == mdir / "small" and models.is_installed("small")
    assert all(r["stage"] == "model" for r in rows) and rows[-1]["pct"] == 100
    assert all(r["pct"] < 100 for r in rows[:-1])  # capped until snapshot_download returns
    assert [r["pct"] for r in rows] == sorted(r["pct"] for r in rows)


def test_progress_capped_at_99_when_bytes_exceed_estimate(mdir, monkeypatch, capsys):
    monkeypatch.setattr(models, "POLL_SECONDS", 0.01)
    monkeypatch.setitem(models.MODELS["small"], "size_mb", 0.0001)  # tiny estimate: bytes exceed it
    install_fake_hub(monkeypatch, fake_snapshot(delay=0.05))
    models.download("small")
    rows = pcts(capsys)
    assert max(r["pct"] for r in rows[:-1]) == 99 and rows[-1]["pct"] == 100


def test_download_failure_propagates_without_100(mdir, monkeypatch, capsys):
    def boom(repo, local_dir=None, **kw):
        raise RuntimeError("network down")
    install_fake_hub(monkeypatch, boom)
    with pytest.raises(RuntimeError):
        models.download("small")
    assert all(r["pct"] < 100 for r in pcts(capsys))


def test_interrupted_download_is_not_installed(mdir, monkeypatch):
    def partial(repo, local_dir=None, **kw):
        (Path(local_dir) / "model.bin").write_bytes(b"x")
        (Path(local_dir) / "config.json").write_text("{}")
        raise ConnectionError("dropped midway")
    install_fake_hub(monkeypatch, partial)
    with pytest.raises(ConnectionError):
        models.download("small")
    assert not models.is_installed("small") and not (mdir / "small" / models.SENTINEL).exists()


def test_download_writes_sentinel_and_delete_removes_it(mdir, monkeypatch):
    install_fake_hub(monkeypatch, fake_snapshot())
    models.download("small")
    meta = json.loads((mdir / "small" / models.SENTINEL).read_text())
    assert meta["repo"] == models.MODELS["small"]["repo"] and meta["finished"]
    models.delete("small")
    assert not (mdir / "small").exists() and not models.is_installed("small")


def test_unknown_name_rejected(mdir):
    for fn in (models.download, models.delete):
        with pytest.raises(KeyError):
            fn("../evil")


def test_delete_removes_only_that_model(mdir):
    for n in ("small", "large-v3"):
        (mdir / n).mkdir(parents=True)
        (mdir / n / "model.bin").write_bytes(b"x")
    models.delete("small")
    assert not (mdir / "small").exists() and (mdir / "large-v3").exists()
    models.delete("small")  # already gone: fine


def test_cli_status_and_unknown(mdir, capsys):
    assert models.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["small"]["installed"] is False
    with pytest.raises(SystemExit):
        models.main(["delete", "nope"])


def test_transcribe_loads_local_folder_when_installed(mdir, monkeypatch):
    import transcribe
    seen = []

    class FakeWhisper:
        def __init__(self, name, **kw):
            seen.append((name, kw))
            raise RuntimeError("stop")

    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(WhisperModel=FakeWhisper, BatchedInferencePipeline=object))
    monkeypatch.setenv("VEDIT_DEVICE", "cpu")  # the CPU path, whatever card this machine has
    import accel
    accel.gpu_ready.cache_clear()
    mdir.mkdir()
    wav = mdir / "x.wav"
    wav.write_bytes(b"")

    def attempt():
        with pytest.raises(BaseException):  # noqa: B017 - transcribe's fallbacks may exit; we only read the call
            transcribe.transcribe_words(str(wav), "small", "en", False)

    attempt()
    (mdir / "small").mkdir()
    for f in models.REQUIRED_FILES:
        (mdir / "small" / f).write_bytes(b"x")
    models._mark_complete("small")
    attempt()
    assert seen[0][0] == "small" and seen[0][1]["download_root"] == str(mdir)
    assert seen[-1][0] == str(mdir / "small") and "download_root" not in seen[-1][1]
