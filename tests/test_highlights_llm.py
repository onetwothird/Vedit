"""Optional AI re-ranking: request shape, snapping, fallbacks, and the CLI wiring. No real network."""
import json
import sys

import httpx
import pytest

import highlights
import highlights_llm
from common import load_json, save_json
from highlights_llm import DEFAULT_MODELS, rank_with_llm

SENTS = [(0.0, 12.0), (12.5, 30.0), (30.5, 61.0), (61.5, 80.0), (80.5, 98.5), (99.0, 125.0)]


def _transcript():
    sents, words = [], []
    for k, (a, b) in enumerate(SENTS):
        text = f"Sentence number {k} talks about the 500 peso mistake."
        sents.append({"w0": len(words), "w1": len(words) + 1, "start": a, "end": b, "text": text})
        words += [{"start": a, "end": a + 1, "text": "Sentence"}, {"start": b - 1, "end": b, "text": "mistake."}]
    return {"source": r"C:\secret\video.mp4", "duration": 130.0, "words": words, "sentences": sents}


def _cands():
    def h(i, a, b, w0, w1, score):
        return {"id": f"h{i:02d}", "start": a, "end": b, "w0": w0, "w1": w1, "score": score,
                "title": f"Offline {i}", "features": {"hook": 1.0}}
    return [h(1, 0.0, 30.0, 0, 3, 80), h(2, 12.5, 61.0, 2, 5, 60), h(3, 61.5, 98.5, 6, 9, 40)]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _openai_reply(clips):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"clips": clips})}}]})


def test_request_contains_no_media():
    seen = {}

    def handler(req):
        seen["body"] = req.content.decode()
        return _openai_reply([{"id": "h03", "title": "x", "start": 61.5, "end": 98.5}])

    rank_with_llm(_transcript(), _cands(), "openai", "k", http=_client(handler))
    body = seen["body"]
    assert "[00:12]" in body and "[01:01]" in body
    assert "path" not in body and ".mp4" not in body and "secret" not in body
    assert "h03" in body


def test_reranks_and_snaps_to_words():
    clips = [{"id": "h03", "title": "The \u20b1500 mistake", "start": 61.2, "end": 98.0, "why": "hook"}]
    out, err = rank_with_llm(_transcript(), _cands(), "openai", "k",
                             http=_client(lambda r: _openai_reply(clips)))
    assert err is None and len(out) == 1
    h = out[0]
    assert h["id"] == "h03" and h["title"] == "The \u20b1500 mistake" and h["score"] == 100
    assert (h["start"], h["end"]) == (61.5, 98.5)       # nearest sentence start / end
    assert (h["w0"], h["w1"]) == (6, 9)
    assert 20 <= h["end"] - h["start"] <= 90


def test_unsafe_ids_are_skipped():
    clips = [{"id": "../../x", "title": "evil", "start": 61.5, "end": 98.5},
             {"id": "a" * 33, "title": "long", "start": 61.5, "end": 98.5},
             {"id": "h01", "title": "ok", "start": 0.0, "end": 30.0}]
    out, err = rank_with_llm(_transcript(), _cands(), "openai", "k", http=_client(lambda r: _openai_reply(clips)))
    assert err is None and [h["id"] for h in out] == ["h01"]


def test_scores_are_rescaled_by_rank():
    clips = [{"id": "h03", "title": "a", "start": 61.5, "end": 98.5},
             {"id": "h01", "title": "b", "start": 0.0, "end": 30.0}]
    out, err = rank_with_llm(_transcript(), _cands(), "openai", "k", http=_client(lambda r: _openai_reply(clips)))
    assert err is None and [h["id"] for h in out] == ["h03", "h01"]
    assert out[0]["score"] == 100 and 0 <= out[1]["score"] < 100


def test_overlapping_and_overlong_and_out_of_range_clips_dropped():
    clips = [{"id": "h03", "title": "ok", "start": 61.5, "end": 98.5},
             {"id": "h02", "title": "overlaps", "start": 80.5, "end": 125.0},
             {"id": "h01", "title": "too long", "start": 0.0, "end": 125.0},
             {"id": "h09", "title": "off the end", "start": 500.0, "end": 560.0}]
    out, err = rank_with_llm(_transcript(), _cands(), "openai", "k", http=_client(lambda r: _openai_reply(clips)))
    assert err is None and [h["id"] for h in out] == ["h03"]


def test_nothing_valid_falls_back():
    cands = _cands()
    clips = [{"id": "h01", "title": "too short", "start": 0.0, "end": 12.0}]
    out, err = rank_with_llm(_transcript(), cands, "openai", "k", http=_client(lambda r: _openai_reply(clips)))
    assert err == "ai_failed" and out == cands


def test_bad_json_falls_back():
    cands = _cands()
    http = _client(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}))
    out, err = rank_with_llm(_transcript(), cands, "openai", "k", http=http)
    assert err == "ai_failed" and out == cands


def test_timeout_falls_back():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    cands = _cands()
    out, err = rank_with_llm(_transcript(), cands, "anthropic", "k", http=_client(handler))
    assert err == "ai_failed" and out == cands


def test_http_error_falls_back():
    cands = _cands()
    out, err = rank_with_llm(_transcript(), cands, "openai", "sk-SECRET",
                             http=_client(lambda r: httpx.Response(401, json={"error": "bad key"})))
    assert err == "ai_failed" and out == cands


def test_anthropic_headers_and_fenced_json():
    seen = {}
    clips = {"clips": [{"id": "h03", "title": "T", "start": 61.5, "end": 98.5}]}

    def handler(req):
        seen["req"] = req
        text = "Here you go:\n```json\n" + json.dumps(clips) + "\n```"
        return httpx.Response(200, json={"content": [{"type": "text", "text": text}]})

    out, err = rank_with_llm(_transcript(), _cands(), "anthropic", "ak-1", http=_client(handler))
    req = seen["req"]
    assert err is None and out[0]["id"] == "h03"
    assert req.method == "POST" and str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "ak-1"
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert req.headers["content-type"] == "application/json"
    body = json.loads(req.content)
    assert body["max_tokens"] == 2000 and body["model"] == DEFAULT_MODELS["anthropic"]


def test_openai_request_shape():
    seen = {}

    def handler(req):
        seen["req"] = req
        return _openai_reply([{"id": "h03", "title": "T", "start": 61.5, "end": 98.5}])

    rank_with_llm(_transcript(), _cands(), "openai", "ok-1", model="gpt-x", http=_client(handler))
    req = seen["req"]
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer ok-1"
    body = json.loads(req.content)
    assert body["response_format"] == {"type": "json_object"} and body["model"] == "gpt-x"


def test_unknown_provider_falls_back():
    cands = _cands()
    assert rank_with_llm(_transcript(), cands, "bogus", "k") == (cands, "ai_failed")  # type: ignore[arg-type]


# --- CLI wiring -------------------------------------------------------------------------------------------------

@pytest.fixture
def cli_wd(tmp_path):
    save_json(_transcript(), tmp_path / "transcript.main.json")
    save_json({"name": "main", "probe": {"duration": 130.0}, "silences": []}, tmp_path / "analysis.main.json")
    return tmp_path


def _run_cli(monkeypatch, wd, *args):
    monkeypatch.setattr(sys, "argv", ["highlights.py", str(wd), *args])
    highlights.main()
    return load_json(wd / "highlights.json")


def test_cli_ai_success(monkeypatch, cli_wd):
    calls = {}

    def fake(transcript, cands, provider, api_key, model=None, http=None, **kw):
        calls.update(provider=provider, key=api_key, model=model)
        return [dict(cands[-1], title="AI title", score=100)], None

    monkeypatch.setenv("VEDIT_AI_KEY", "sekret")
    monkeypatch.setattr(highlights_llm, "rank_with_llm", fake)
    data = _run_cli(monkeypatch, cli_wd, "--ai", "openai", "--ai-model", "m1")
    assert calls == {"provider": "openai", "key": "sekret", "model": "m1"}
    assert data["reason"] is None and data["highlights"][0]["title"] == "AI title"
    assert "sekret" not in (cli_wd / "highlights.json").read_text(encoding="utf-8")


def test_cli_ai_failure_keeps_offline(monkeypatch, cli_wd):
    monkeypatch.setenv("VEDIT_AI_KEY", "sekret")
    monkeypatch.setattr(highlights_llm, "rank_with_llm", lambda t, c, *a, **k: (c, "ai_failed"))
    offline = _run_cli(monkeypatch, cli_wd)["highlights"]
    data = _run_cli(monkeypatch, cli_wd, "--ai", "anthropic")
    assert data["reason"] == "ai_failed" and data["highlights"] == offline and offline


def test_cli_ai_without_key_is_offline(monkeypatch, cli_wd):
    monkeypatch.delenv("VEDIT_AI_KEY", raising=False)

    def boom(*a, **k):
        raise AssertionError("must not be called without a key")

    monkeypatch.setattr(highlights_llm, "rank_with_llm", boom)
    data = _run_cli(monkeypatch, cli_wd, "--ai", "openai")
    assert data["reason"] == "ai_failed" and data["highlights"]
