"""Shared fixture for the TS port (app/src/edit/timeline.ts): the same ops must give the same clips and kept_ranges.

Set VEDIT_REGEN_FIXTURES=1 to rewrite the expected values from the Python implementation."""
import json
import os
from pathlib import Path

import pytest

from edl_ops import cut_words, kept_ranges, restore_words, trim

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "edl_ops_cases.json"


def _run(edl, op):
    if op["op"] == "cut":
        return cut_words(edl, op["w0"], op["w1"])
    if op["op"] == "restore":
        return restore_words(edl, op["w0"], op["w1"])
    if op["op"] == "trim":
        return trim(edl, op["w_first"], op["w_last"])
    raise AssertionError(op)


def _states(case, transcript):
    edl = case["edl"]
    out = [{"clips": edl["clips"], "kept_ranges": [list(r) for r in kept_ranges(edl, transcript)]}]
    for op in case["ops"]:
        edl = _run(edl, op)
        out.append({"clips": edl["clips"], "kept_ranges": [list(r) for r in kept_ranges(edl, transcript)]})
    return out


def test_fixture_matches_python():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if os.environ.get("VEDIT_REGEN_FIXTURES") == "1":
        for case in data["cases"]:
            case["expected"] = _states(case, data["transcript"])
        FIXTURE.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    for case in data["cases"]:
        assert case.get("expected"), f"{case['name']}: no expected (run with VEDIT_REGEN_FIXTURES=1)"
        got = _states(case, data["transcript"])
        assert len(got) == len(case["expected"])
        for g, e in zip(got, case["expected"]):
            assert g["clips"] == e["clips"], case["name"]
            assert len(g["kept_ranges"]) == len(e["kept_ranges"])
            for gr, er in zip(g["kept_ranges"], e["kept_ranges"]):
                assert gr == pytest.approx(er, abs=1e-9), case["name"]
