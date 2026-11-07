"""Build your Reels (works on Windows, macOS and Linux).

    python build.py                          # every story in stories/
    python build.py stories/002_my_topic     # just one story
    python build.py --preview                # fast, low quality (to check timing)
    python build.py --only cards             # just redraw cards + thumbnails
"""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REELS = HERE.parent.parent / "scripts" / "reels.py"  # this kit lives in templates/ of the vedit repo
if REELS.exists():
    base = [sys.executable, str(REELS)]
elif shutil.which("vedit"):  # kit copied somewhere else + vedit installed with pip
    base = ["vedit", "reels"]
else:
    sys.exit("Can't find vedit. Keep this kit inside the vedit folder, or install it:  pip install -e path/to/vedit")
args = sys.argv[1:]
target = next((a for a in args if not a.startswith("-") and a != "cards"), str(HERE))
flags = [a for a in args if a != target]
sys.exit(subprocess.call([*base, "build", target, *flags]))
