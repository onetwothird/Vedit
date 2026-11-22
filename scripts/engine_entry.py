"""Entry point for the frozen engine: `vedit-engine run <script.py> [args...]` runs a bundled script."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


sys.path.insert(0, str(HERE))


def _run(script: str, args: list[str]) -> int:
    path = Path(script)
    if not path.is_absolute():
        path = HERE / script
    sys.argv = [str(path), *args]
    try:
        runpy.run_path(str(path), run_name="__main__")
    except SystemExit as e:
        if e.code is None:
            return 0
        return e.code if isinstance(e.code, int) else 1
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "run":
        return _run(argv[1], argv[2:])
    print("usage: vedit-engine run <script.py> [args...]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
