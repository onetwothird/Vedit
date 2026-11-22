"""Entry point for the frozen engine.

  vedit-engine run <script.py> [args...]      run a bundled script
  vedit-engine serve --port N                local Studio API on 127.0.0.1 (port 0 = any free port);
                                             bearer token from env VEDIT_ENGINE_TOKEN
"""
from __future__ import annotations

import os
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
        if isinstance(e.code, int):
            return e.code
        print(e.code, file=sys.stderr)
        return 1
    return 0


def _serve(args: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="vedit-engine serve",
                                 description="Local Studio API on 127.0.0.1; the bearer token comes from env "
                                             "VEDIT_ENGINE_TOKEN (never argv, which other processes can read).")
    ap.add_argument("--port", type=int, default=0, help="port to listen on (0 = any free port)")
    a = ap.parse_args(args)
    token = os.environ.pop("VEDIT_ENGINE_TOKEN", "")  # popped: job steps inherit os.environ, they must not see it
    if not token.strip():
        print("error: VEDIT_ENGINE_TOKEN is not set; the engine needs a bearer token in that environment variable",
              file=sys.stderr)
        return 2
    from studio_server import serve
    serve(a.port, token)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "run":
        return _run(argv[1], argv[2:])
    if argv and argv[0] == "serve":
        return _serve(argv[1:])
    print("usage: vedit-engine run <script.py> [args...] | vedit-engine serve --port N", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
