# story.json reference (photos + script → voiced video)

```jsonc
{
  "title": "The Lost Wallet",
  "output": {"path": "lost_wallet.mp4", "aspect": "9:16"},
  "voices": {
    "narrator": {"engine": "edge", "voice": "en-US-AndrewNeural", "color": "#FFD400"},
    "maria":    {"engine": "edge", "voice": "en-US-AvaNeural", "rate": "+8%", "color": "#FF6FB5"},
    "jose":     {"engine": "edge", "voice": "en-US-BrianNeural", "pitch": "-4Hz", "color": "#5EC8FF"}
  },
  "music": {"path": "music/bgm.mp3", "volume": 0.12},
  "captions": {"style": "pop"},
  "defaults": {"motion": "auto", "pause_after": 0.25},
  "intro": "intro.mp4",
  "scenes": [
    {"image": "photos/street.jpg", "title": "Monday, 8:02 AM", "motion": "in",
     "sfx": [{"path": "sfx/traffic.mp3", "at": 0, "volume": 0.25}],
     "lines": [
       {"speaker": "narrator", "text": "Maria was already late for work."},
       {"speaker": "maria", "text": "Jose, where is my wallet?!", "focus": [0.3, 0.38], "shot": "close",
        "rate": "+15%", "sfx": "sfx/gasp.wav"},
       {"speaker": "jose", "text": "Did you check your other bag?", "focus": [0.7, 0.38], "shot": "close",
        "pause_after": 0.8}
     ],
     "hold_after": 0.4},
    {"image": "photos/cafe.jpg", "transition": "fade", "motion": "left",
     "lines": [{"speaker": "narrator", "text": "It was in her hand the whole time."}],
     "texts": [{"text": "THE END", "at": 1.0, "duration": 1.5, "position": "center"}]},
    {"video": "clips/reaction.mp4", "in": 2, "duration": 2.0, "keep_audio": true}
  ]
}
```

## Scene
| key | meaning |
|---|---|
| `image` / `video` (+`in`) | the visual. One photo can carry a whole dialogue: use `focus` + `shot` per line to "cut" between characters |
| `motion` | Ken Burns over the scene: `in`, `out`, `left`, `right`, `up`, `down`, `none`, `auto` (varies per scene) |
| `lines` | dialogue / narration, in order |
| `duration` | length when the scene has no lines (a silent beat) |
| `hold_before`, `hold_after` | silent beats in seconds (let jokes land) |
| `transition` | `cut` (default) or `fade` (dip to black into this scene) |
| `title`, `texts[]` | on-screen text (`texts`: text, at, duration, position top/center/lower-third) |
| `sfx[]` | `{path, at, volume, duration}` relative to scene start |
| `fit` | `crop` / `blur` / `pad` (use blur for portrait photos in 16:9 or vice-versa) |

## Line
| key | meaning |
|---|---|
| `speaker` | key in `voices` |
| `text` | what is said. Punctuation drives delivery: `!`, `?`, `...`, commas |
| `focus` | `[x, y]` 0–1 point on the photo (face of the speaker); read it from `vedit story grid` |
| `shot` | `wide` 1.0×, `medium` 1.35×, `close` 1.8×, `extreme` 2.4× (slow push-in during the line) |
| `pause_after` | seconds of silence after the line (default 0.25) |
| `sfx` | path or `{path, at, volume}` played at the line start |
| voice overrides | `voice`, `rate`, `pitch`, `volume` (edge), `style` (openai acting direction), `engine` |

## Voice engines
| engine | cost | quality | notes |
|---|---|---|---|
| `edge` | free | natural neural | default; exact word timings; `vedit tts --list-voices --lang en` |
| `openai` | paid (OPENAI_API_KEY) | very natural | `"style": "whispering, scared"`; voices alloy, ash, ballad, coral, echo, fable, onyx, nova, sage, shimmer, verse |
| `elevenlabs` | paid (ELEVENLABS_API_KEY) | most expressive | `voice` = voice_id; `stability` 0–1 (lower = more emotion) |
| `piper` | free, offline | good | `voice` = path to .onnx model |
| `system` | free | robotic | automatic fallback |

Good edge voices: en-US-AndrewNeural (warm male narrator), en-US-AvaNeural (expressive female), en-US-BrianNeural (casual male),
en-US-EmmaNeural (friendly female), en-GB-RyanNeural, en-AU-NatashaNeural, fil-PH-AngeloNeural, fil-PH-BlessicaNeural.
Multilingual voices (…MultilingualNeural) handle mixed-language lines like Taglish.

## Writing tips for skits
- Hook in line 1 (conflict, question, or a surprising statement). Total ≤ 60 s for Shorts/Reels.
- One idea per line, ≤ 15 words. Let reactions breathe with `pause_after` 0.5–1.0 and `hold_after`.
- Alternate shots: wide (establish) → close on speaker → close on listener's reaction (a silent `duration` scene) → wide for the punchline.
- Punchline: give it its own line, a `pause_after` before it, and an sfx.
- Only use photos the user owns or has rights to; don't clone real people's voices.
