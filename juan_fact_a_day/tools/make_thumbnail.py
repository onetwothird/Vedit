"""Make the Reel cover photo (thumbnail.jpg, 1080x1920) from the "thumbnail" block of a story.json.

    python tools/make_thumbnail.py stories/001_7641_na_isla/story.json   # one story
    python tools/make_thumbnail.py                                       # every story
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "scripts"))
from cards import thumbnail_for_story  # noqa: E402

if __name__ == "__main__":
    for sp in [Path(p) for p in sys.argv[1:]] or sorted((ROOT / "stories").glob("*/story.json")):
        thumbnail_for_story(sp)
