# Juan Fact a Day: Reels Factory

60-second "Alam mo ba?" fact Reels in Filipino: animated cards, AI narration and word-by-word subtitles.

```
juan_fact_a_day/
├── BUILD.bat                  ← double-click: builds every story (drag a story folder onto it = just that one)
├── channel.json               ← shared look & voice: narrator voice, subtitle style, pacing, animation
├── brand.json                 ← card colors, background, art + mascot paths (read by the shared ../scripts/cards.py)
├── POST_KIT.md                ← page setup + posting tips
├── brand/                     ← profile/cover/mascot + card_background.png (the empty card template)
├── assets/
│   ├── art/                   ← reusable illustrations for cards (jeepney, yoyo_red, crown, islands_map…)
│   └── sfx/                   ← whoosh / ding / pop (made by tools/make_sfx.py)
├── tools/
│   ├── make_cards.py          ← draws the cards described in story.json
│   ├── make_thumbnail.py      ← draws the cover photo (thumbnail.jpg)
│   └── make_sfx.py            ← makes the sound effects
├── _new_story_template/       ← copy this to start a new story
└── stories/
    └── 001_7641_na_isla/      ← ONE FOLDER PER VIDEO
        ├── story.json         ← the script (lines, pronunciation fixes, cards)
        ├── caption.txt        ← Facebook caption + hashtags + sources
        ├── thumbnail.jpg      ← cover photo for the Reel (from "thumbnail" in story.json)
        ├── cards/             ← card images
        ├── 001_7641_na_isla.mp4   ← finished video (not pushed to GitHub)
        └── story.build/       ← cache (safe to delete, not pushed)
```

## Make a new video
1. Copy `_new_story_template` into `stories/` and rename it, e.g. `stories/004_chocolate_hills`.
2. Edit `story.json`:
   - set `"output": {"path": "004_chocolate_hills.mp4"}`
   - one scene per card. Each scene has `lines` (what the narrator says) and a `card` (what's on screen).
   - Script pattern: **"Alam mo ba?"** → fact → **"At alam mo rin ba?"** → fact → **"At heto pa!"** → fact → comment/share/follow.
   - Aim for 10–11 scenes, about 185 words, to land around **60 seconds**. The whole script is voiced in one continuous take, so write it as one flowing story.
3. Write `caption.txt`, and fill in `"thumbnail"` (the cover: badge, big, headline, sticker, art).
4. Drag the story folder onto `BUILD.bat`. The MP4 appears in the same folder.

## Pronunciation (`say`)
`text` is what the **subtitle** shows. `say` (optional) is what the **voice** reads. Use it when the voice gets something wrong:

| Problem | text | say |
|---|---|---|
| Numbers read in slow, formal Tagalog | `7,641` | `seven thousand six hundred forty-one` |
| Years | `1928` | `nineteen twenty-eight` |
| "Juan" read as "one" | `Juan Fact a Day` | `Hu-wan Fact a Day` |
| Roman numerals | `World War II` | `World War Two` |

## Card options (`"card": {...}`)
`badge` (yellow pill) · `big` (huge yellow text, for numbers or names) · `headline` (`*stars*` = yellow) · `sub` (small line) ·
`art` (a file name from `assets/art/`, or `stars3`) · `art_scale` · `mascot: true` (Juan peeks in, bottom-right) · `headline_size`.
A scene without `card` uses its image as-is (any 1080×1920 picture works).

## Requirements (one time)
- Python 3.10+, FFmpeg (`winget install Gyan.FFmpeg`). `BUILD.bat` installs the Python packages.
- The voice is Microsoft Edge's free Filipino voice (`fil-PH-AngeloNeural`), so building needs internet.

Double-check every fact before posting. Sources go in `caption.txt`.

## Make your own channel like this
Want the same format with your own name, colors and footage (raw videos too, including you talking on camera)?
Use the starter kit: [templates/fact_reels](../templates/fact_reels/README.md).

## License
The code here is MIT (open source). The Juan Fact a Day name, mascot, artwork and video scripts are all rights reserved; see [LICENSE.md](LICENSE.md).
