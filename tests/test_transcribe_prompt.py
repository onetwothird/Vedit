"""Tagalog prompt choice and the guard against Whisper repeating its own prompt."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from transcribe import FILLER_PROMPT, TAGLISH_PROMPT, echoes_prompt, pick_prompt  # noqa: E402


def test_tagalog_gets_taglish_prompt_not_english():
    assert pick_prompt("tl", True) == TAGLISH_PROMPT
    assert pick_prompt("fil", False) == TAGLISH_PROMPT
    assert pick_prompt("en", True) == FILLER_PROMPT
    assert pick_prompt("en", False) is None


def test_drops_low_confidence_prompt_echo():
    # what large/small Whisper produced on a 3 s beep with the Taglish prompt
    fake = [("Hindi", 0.0), ("ba", 0.002), ("mas", 0.004), ("maganda", 0.018), ("kung", 0.001),
            ("paano", 0.012), ("natin", 0.374), ("ito", 0.336), ("gagawin?", 0.142)]
    assert echoes_prompt(fake, TAGLISH_PROMPT)


def test_keeps_real_speech_that_uses_prompt_words():
    real = [("Hindi", 0.95), ("ba", 0.9), ("natin", 0.92), ("ito", 0.88), ("gagawin?", 0.9)]
    assert not echoes_prompt(real, TAGLISH_PROMPT)


def test_keeps_low_confidence_words_that_are_not_the_prompt():
    mumbled = [("oh", 0.2), ("hindi", 0.3), ("na", 0.1), ("talaga", 0.2), ("eh", 0.1)]
    assert not echoes_prompt(mumbled, TAGLISH_PROMPT)


def test_no_prompt_or_short_segment_is_never_dropped():
    assert not echoes_prompt([("Hindi", 0.0), ("ba", 0.0), ("mas", 0.0)], None)
    assert not echoes_prompt([("Hindi", 0.0), ("ba", 0.0)], TAGLISH_PROMPT)
