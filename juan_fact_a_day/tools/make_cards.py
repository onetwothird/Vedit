"""Draw the cards described in story.json (uses ../../scripts/cards.py with this folder's brand.json).

    python tools/make_cards.py stories/004_xxx/story.json      # one story
    python tools/make_cards.py                                  # every story

Card options are listed in ../../scripts/cards.py and templates/fact_reels/README.md.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "scripts"))
from cards import cards_for_story  # noqa: E402

if __name__ == "__main__":
    for sp in [Path(p) for p in sys.argv[1:]] or sorted((ROOT / "stories").glob("*/story.json")):
        cards_for_story(sp)
