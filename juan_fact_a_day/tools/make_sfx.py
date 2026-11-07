"""Generate the channel's sound effects (royalty-free, made from scratch) into assets/sfx/.

    python tools/make_sfx.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "scripts"))
from sfx import make_all  # noqa: E402

if __name__ == "__main__":
    make_all(ROOT / "assets" / "sfx", force=True)
