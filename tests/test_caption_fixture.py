"""The TS preview reads app/src/preview/__fixtures__/captions.json. It must be exactly what Python makes today."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import export_caption_fixture as fx  # noqa: E402


def test_committed_caption_fixture_matches_python(pipeline_workdir_9x16):
    data = fx.build_fixture(pipeline_workdir_9x16)
    if os.environ.get("VEDIT_REGEN_FIXTURES") == "1":
        fx.FIXTURE.write_text(fx.render(data), encoding="utf-8")
    assert fx.FIXTURE.is_file(), "missing fixture (run with VEDIT_REGEN_FIXTURES=1)"
    assert json.loads(fx.FIXTURE.read_text(encoding="utf-8")) == data, \
        "captions.json drifted from caption_events (VEDIT_REGEN_FIXTURES=1 rewrites it)"


def test_fixture_has_events_and_matching_ass_starts(pipeline_workdir_9x16):
    data = fx.build_fixture(pipeline_workdir_9x16)
    assert data["events"] and [round(e["start"], 2) for e in data["events"]] == data["ass_starts"]
