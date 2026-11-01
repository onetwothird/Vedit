# edl.json reference

Paths are relative to the edl.json file. Times can be seconds (`12.5`) or `"1:02.5"`.

```jsonc
{
  "sources": {
    "main": "../raw.mp4",                                    // reference source (transcript.main.json beside edl)
    "cam2": {"path": "../cam2.mp4", "offset": 1.234},        // synced: times/words in MAIN's time base
    "broll1": "../broll/city.mp4",                           // unsynced: its own time base
    "photo": "../img/product.png"                            // still images work as sources too
  },
  "output": {"path": "../final.mp4", "aspect": "16:9",       // 16:9 | 9:16 | 1:1 | 4:5   (or width/height)
             "fps": 30, "crf": 20, "preset": "medium"},
  "fit": "crop",                                             // default fit when aspect differs: crop | blur | pad
  "pad": [0.10, 0.18],                                       // default padding around "words" clips (before, after)
  "intro": {"path": "../intro.mp4", "fade_out": 0.3},        // optional bumpers
  "outro": "../outro.mp4",

  "captions": {"enabled": true, "style": "pop",             // pop | box | clean
               "max_words": 3, "font": "Montserrat", "size": null,
               "color": "#FFFFFF", "highlight": "#FFD400", "uppercase": true,
               "position": "lower",                          // lower | middle | bottom
               "hide_fillers": true, "srt": true,
               "replace": {"cloud code": "Claude Code"},
               "speaker_colors": {"host": "#FFD400"}},

  "audio": {"normalize": true, "target_lufs": -14,           // -14 social/YouTube, -16 podcasts
            "voice_polish": true,                            // compression + presence EQ
            "denoise": false,                                // afftdn, only for noisy rooms
            "music": {"path": "../music.mp3", "volume": 0.15, "duck": true, "fade_in": 1, "fade_out": 2.5}},

  "color": {"contrast": 1.05, "saturation": 1.1, "brightness": 0.0, "gamma": 1.0,
            "lut": "../luts/warm.cube", "sharpen": false, "vignette": false},

  "overlays": [
    {"type": "text", "text": "3 tips for X", "at": {"word": ["main", 120]}, "duration": 2.5,
     "position": "top"},                                     // top | lower-third | center ; optional color, size
    {"type": "video", "path": "../broll/city.mp4", "src_in": 4, "at": {"word": ["main", 300]},
     "duration": 3, "mode": "full"},                         // full | pip  (+ position, scale)
    {"type": "image", "path": "../logo.png", "at": 0, "duration": 4, "mode": "pip",
     "position": "top-right", "scale": 0.2},
    {"type": "audio", "path": "../sfx/whoosh.wav", "at": {"clip": 3, "t": 0}, "volume": 0.6}
  ],

  "clips": [
    {"src": "main", "words": [0, 18], "note": "hook"},
    {"src": "main", "words": [25, 60], "zoom": 1.12},                       // static punch-in
    {"src": "main", "words": [61, 90], "zoom": {"from": 1.0, "to": 1.15}},   // slow push-in
    {"src": "cam2", "words": [91, 120]},                                     // other angle, main audio
    {"src": "broll1", "in": 3, "out": 7.5, "mute": true},
    {"src": "main", "in": "4:10", "out": "4:20", "speed": 1.5},
    {"src": "main", "words": [400, 420], "crop_x": "auto", "fit": "crop", "fade_out": 0.5},
    {"src": "photo", "duration": 3, "zoom": {"from": 1.0, "to": 1.2, "focus_from": [0.2, 0.5], "focus_to": [0.8, 0.5]}}
  ]
}
```

## Clip fields
| field | meaning |
|---|---|
| `src` | source alias (default: main) |
| `words` | `[first, last]` word numbers from transcript.<src>.txt (synced sources use main's) |
| `in`, `out` / `duration` | seconds in source time base |
| `pad` | `[before, after]` seconds around `words` clips, never overlapping neighbor words |
| `speed` | 0.25–4 (audio pitch preserved) |
| `zoom` | number (static) or `{from, to, focus_from?, focus_to?, ease?}` (animated, Ken Burns/pan) |
| `focus` | `[x, y]` subject point in source (0–1) for zooms |
| `crop_x`, `crop_y` | center of the crop window (0–1) when output aspect differs; `"auto"` = face detection |
| `fit` | `crop` (fill), `blur` (fit over blurred fill — screen recordings to vertical), `pad` (letterbox) |
| `audio_src` | take audio from another source (voice-over, external mic) |
| `mute`, `volume` | audio control |
| `fade_in`, `fade_out` | seconds, to/from black (video) and silence (audio) |
| `color` | per-clip override of the global color block |
| `captions` | `false` = no captions over this clip |

## Overlay anchors (`at`)
- number: seconds in the **output** timeline
- `{"word": ["main", 135]}`: when that word is spoken in the edit (skipped if the word was cut)
- `{"clip": 4, "t": 0.5}`: 0.5 s into clip 4 of the output
- `{"src": "main", "t": 92.3}`: source time mapped through the edit
