"""Fake engine step for job-queue tests.

usage: fake_step.py STAGE STEPS SLEEP EXIT_CODE [STDERR_MESSAGE] [--grandchild PIDFILE] [--env NAME]
Emits STEPS progress lines (pct 100/STEPS ... 100), sleeping SLEEP seconds between them, then exits EXIT_CODE.
"""
import argparse
import json
import os
import subprocess
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("stage")
ap.add_argument("steps", type=int)
ap.add_argument("sleep", type=float)
ap.add_argument("exit_code", type=int)
ap.add_argument("message", nargs="?", default="")
ap.add_argument("--grandchild", metavar="PIDFILE")
ap.add_argument("--env", metavar="NAME", help="print the value of this env var to stdout (secrets test)")
a = ap.parse_args()

if a.grandchild:
    gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(a.grandchild, "w") as f:
        f.write(str(gc.pid))
if a.env:
    print(os.environ.get(a.env, ""), flush=True)
for i in range(1, a.steps + 1):
    row = {"stage": a.stage, "pct": 100.0 * i / a.steps, "message": f"{a.stage} {i}/{a.steps}"}
    print("@@progress " + json.dumps(row), file=sys.stderr, flush=True)
    time.sleep(a.sleep)
if a.message:
    print(a.message, file=sys.stderr, flush=True)
sys.exit(a.exit_code)
