"""Writes app/src/preview/__fixtures__/captions.json: caption_events() for the 9:16 pop pipeline workdir plus the
start of every caption group in the ASS that build() makes for the same input ("ass_starts").

The preview tests (app/src/preview/playback.test.ts) read it, so the live captions are checked against the engine.
tests/test_caption_fixture.py fails when the committed file drifts; regenerate it with

    python tests/fixtures/export_caption_fixture.py          (or VEDIT_REGEN_FIXTURES=1 pytest tests/test_caption_fixture.py)
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "app" / "src" / "preview" / "__fixtures__" / "captions.json"
DIALOGUE = re.compile(r"^Dialogue: 0,(\d+):(\d\d):(\d\d)\.(\d\d),.*$", re.M)


def ass_group_starts(ass: str) -> list[float]:
    """Start of each caption group. Pop groups open with the pop-in tag; a clean group is one event."""
    pop = "fscx90" in ass
    out = []
    for m in DIALOGUE.finditer(ass):
        if pop and "fscx90" not in m.group(0):
            continue
        h, mi, s, cs = (int(x) for x in m.group(1, 2, 3, 4))
        out.append(round(h * 3600 + mi * 60 + s + cs / 100, 2))
    return out


def _round(x):
    if isinstance(x, float):
        return round(x, 4)
    if isinstance(x, list):
        return [_round(v) for v in x]
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    return x


def build_fixture(workdir: Path) -> dict:
    sys.path.insert(0, str(ROOT / "scripts"))
    from captions import build, caption_events
    from edl import Timeline

    tl = Timeline(Path(workdir) / "edl.json")
    data = {**caption_events(tl), "ass_starts": ass_group_starts(build(tl)[0])}
    return json.loads(json.dumps(_round(data)))  # what a JSON round trip gives back


def render(data: dict) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def main():
    env = {**os.environ, "VEDIT_REGEN_FIXTURES": "1"}
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests" / "test_caption_fixture.py")],
                             env=env, cwd=ROOT))


if __name__ == "__main__":
    main()
