# Fact Reels starter kit

Make 60-second **fact Reels / Shorts / TikToks** with animated fact cards, a narrator voice, word-by-word
subtitles, sound effects and music. Use **your** name, colors and footage.

**You bring the raw material.** Photos and/or raw video clips with nothing on them: no captions, no text, no
voice-over. **The tool does the editing:** cards, subtitles, narration, sound effects, music, loudness and
the 9:16 crop. All you edit is one text file per video (`story.json`).

```
my_channel/
├── brand.json                 ← your name, colors, logo/mascot (the look of every video)
├── channel.json               ← narrator voice, subtitle style, language
├── build.py / BUILD.bat       ← builds the videos
├── brand/                     ← card_background.png (generated for you from brand.json)
├── assets/art/                ← pictures you can put on cards (transparent PNGs)
├── assets/sfx/                ← whoosh / ding / pop (generated for you)
├── _new_story_template/       ← copied for each new video
└── stories/
    └── 002_my_topic/          ← ONE FOLDER PER VIDEO
        ├── story.json         ← the script + what is on screen
        ├── caption.txt        ← the post caption + hashtags + sources
        ├── photos/            ← your photos
        ├── clips/             ← your raw videos
        ├── cards/             ← card images (drawn for you)
        ├── thumbnail.jpg      ← cover image (drawn for you)
        └── 002_my_topic.mp4   ← THE FINISHED VIDEO
```

---

## Step 1. Install (one time)

1. **Python 3.10+**: <https://www.python.org/downloads/> (on Windows, tick *"Add python.exe to PATH"*).
2. **FFmpeg**:
   ```bash
   winget install Gyan.FFmpeg      # Windows
   brew install ffmpeg             # macOS
   sudo apt install ffmpeg         # Debian / Ubuntu
   ```
3. **vedit** (this repository) and its Python packages:
   ```bash
   git clone https://github.com/onetwothird/vedit.git
   cd vedit
   pip install -e ".[all]"
   ```
4. Check that everything is there:
   ```bash
   vedit doctor
   ```

> Building needs internet: the free narrator voice is Microsoft Edge's neural text-to-speech.
> The first talking clip you build downloads the speech-recognition model (about 500 MB, one time).

## Step 2. Make your channel folder

```bash
vedit reels new my_channel
```

This copies the kit to `my_channel/`. Everything below happens inside that folder.

## Step 3. Build the example first (2 minutes)

```bash
vedit reels build my_channel
```

The example story (`stories/000_example`) makes its own sample photo and clips, then builds
`stories/000_example/000_example.mp4`. Watch it. It shows all three kinds of scenes (see Step 6).
If this works, your setup is complete.

## Step 4. Put in your brand

Open `brand.json` and change:

| Key | What it is |
|---|---|
| `name`, `tagline` | printed at the top of every card |
| `colors.accent` | the highlight color (badges, big numbers, `*starred*` words) |
| `colors.bg_top`, `colors.bg_bottom` | the card background gradient |
| `colors.text`, `colors.shadow`, `colors.badge_text` | text, text shadow, text inside the badge |
| `background` | your own 1080×1920 card background, if you have one. Leave the top ~360 px for your header and keep the middle empty |
| `mascot_peek` | optional small character for `"mascot": true` on cards (transparent PNG) |
| `mascot` | optional big character for the thumbnail (transparent PNG) |
| `fonts` | optional, e.g. `{"ExtraBold": "fonts/MyFont-Black.ttf", "SemiBold": "fonts/MyFont-SemiBold.ttf"}` |

Using the generated background? **Delete `brand/card_background.png` after changing the name or colors**,
and the next build redraws it.

Open `channel.json` to pick the narrator voice and language:

| Language | `voices.narrator.voice` | `defaults.transcribe.language` |
|---|---|---|
| English (US) | `en-US-AndrewNeural`, `en-US-AvaNeural` | `en` |
| Filipino | `fil-PH-AngeloNeural`, `fil-PH-BlessicaNeural` | `tl` |
| Spanish | `es-MX-JorgeNeural`, `es-ES-ElviraNeural` | `es` |

List every voice with `edge-tts --list-voices`. Also set the subtitle `highlight` color to match your `accent`.

## Step 5. Start a new video

```bash
vedit reels story my_channel 002_my_topic
```

This creates `stories/002_my_topic/` with empty `photos/` and `clips/` folders. Copy your raw files in:

- **photos/**: JPG/PNG photos (any size; phone photos are rotated correctly).
- **clips/**: raw MP4/MOV videos, straight from your phone or camera. Horizontal clips are center-cropped
  to vertical automatically.

## Step 6. Write `story.json`

A video is a list of **scenes**. Each scene is one of three kinds:

### A. Card scene (photo or plain card) + AI narration
```json
{
 "image": "cards/01.jpg",
 "card": {"badge": "DID YOU KNOW?", "big": "8 MIN", "headline": "for *sunlight* to reach Earth"},
 "sfx": [{"path": "../../assets/sfx/ding.wav", "at": 0, "volume": 0.3}],
 "lines": [
  {"speaker": "narrator", "text": "Did you know?", "pause_after": 0.18},
  {"speaker": "narrator", "text": "Sunlight takes about 8 minutes to reach us.",
   "say": "Sunlight takes about eight minutes to reach us."}
 ]
}
```
The card is drawn into `cards/01.jpg` and animated (each piece pops in and pulses on every new sentence).
To show **your photo** on the card, add `"photo": "photos/x.jpg", "photo_mode": "frame"` (framed under the text)
or `"photo_mode": "full"` (darkened full-screen backdrop). Add `"background": "cards/_plate_01.jpg"` to the
scene so only the text animates.

### B. Raw video (no sound needed) + AI narration
```json
{
 "video": "clips/beach.mp4",
 "in": 2.0,
 "card_image": "cards/02.jpg",
 "card": {"badge": "AND DID YOU KNOW?", "headline": "Sand is mostly *quartz*"},
 "lines": [{"speaker": "narrator", "text": "Most beach sand is tiny bits of quartz."}]
}
```
The clip plays from second `in`. Its own sound is muted, and the narrator talks over it. The card pops in over
the footage, which is dimmed while the card is up. If the clip is shorter than the narration it loops.
Add `"card_hold": 2.5` to hide the card after 2.5 s, or leave out `card` for clean footage with subtitles only.

### C. Raw video of you talking (your real voice)
```json
{
 "video": "clips/me.mp4",
 "in": 0, "out": 9.5,
 "keep_audio": true,
 "card_image": "cards/03.jpg",
 "card": {"badge": "YOUR TURN", "headline": "Comment *below!*"},
 "card_hold": 3
}
```
No `lines`: your own voice is kept, transcribed automatically, and shown as word-by-word subtitles.
`in`/`out` pick the part of the clip to use (leave out `out` to use it to the end).
The card shows for the first `card_hold` seconds (3.5 by default) and then gets out of the way.

**Mix them freely.** For example, a hook card, two B-roll facts, then you on camera for the call to action.

### Writing tips
- **The script pattern that works:** hook question → fact → *"And did you know?"* → fact → *"And one more!"* →
  fact → comment / share / follow. About **10–11 scenes, ~185 words ≈ 60 seconds**.
- The whole narration is voiced in **one continuous take**, so write it as one flowing story.
- `text` is what the subtitle shows. `say` (optional) is what the voice reads. Use `say` when the voice gets
  something wrong:

  | Problem | `text` | `say` |
  |---|---|---|
  | numbers | `7,641` | `seven thousand six hundred forty-one` |
  | years | `1928` | `nineteen twenty-eight` |
  | a name read wrong | `Juan` | `Hu-wan` |
  | Roman numerals | `World War II` | `World War Two` |

### Card options (`"card": {...}`)
| Key | Meaning |
|---|---|
| `badge` | pill-shaped label on top |
| `big` | huge accent-colored text (numbers, names); `big_size` to resize |
| `headline` | main text; `*stars*` = accent color; `headline_size` to resize |
| `sub` | smaller explanation line |
| `art` | picture from `assets/art/<name>.png`, or the built-in `stars3`; `art_scale` to resize |
| `mascot` | `true` = your `mascot_peek` in the bottom-right |
| `bg` | `"dark"` = night version of the background (mysteries) |
| `photo`, `photo_mode`, `photo_focus`, `credit` | your photo on the card (see A) |
| `lineup` | row of people: `[{"photo": "photos/a.jpg", "label": "NAME"}, {"cross": true, "label": "?"}]` |

### Other scene options
`sfx` (sound effects at a time in the scene), `transition: "fade"`, `hold_after` (seconds of silence at the end),
`motion` (`in`, `out`, `left`, `right`, `none`: slow zoom/pan on photos).
Story options: `"music": {"path": "music/bed.mp3", "volume": 0.12}` (ducks under the voice automatically).
Full reference: [references/story.md](../../references/story.md).

### Thumbnail (cover image)
```json
"thumbnail": {"badge": "DID YOU KNOW?", "big": "3 FACTS", "headline": "you'll want to *share*",
              "sticker": "60 SECONDS!", "art": "stars3"}
```

## Step 7. Build

```bash
vedit reels build my_channel/stories/002_my_topic            # one video
vedit reels build my_channel                                 # every video
vedit reels build my_channel/stories/002_my_topic --preview  # fast, half quality: check the timing first
vedit reels build my_channel --only cards                    # only redraw cards + thumbnails (instant)
```
Without the `vedit` command: `python build.py [story folder]` inside your channel folder.
On Windows you can also **double-click `BUILD.bat`**, or drag a story folder onto it.

You get, inside the story folder:
- `002_my_topic.mp4`: the finished 1080×1920 video, loudness-matched for social media
- `002_my_topic.srt`: subtitles file (for platforms that want one)
- `thumbnail.jpg`: the cover image

Change something and build again: unchanged voice lines and animations come from the cache, so rebuilds are fast.

## Step 8. Post

Write `caption.txt` (hook line, question for comments, hashtags, **sources**), upload the MP4, and set
`thumbnail.jpg` as the cover. **Double-check every fact before posting.**

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `FFmpeg not found` | install it (Step 1), then open a new terminal |
| `Missing files ... scene 3: clips/me.mp4` | the path in `story.json` doesn't match the file name (it's relative to the story folder) |
| `art not found` | put `<name>.png` in `assets/art/`, or remove `"art"` |
| `No transcription backend` | `pip install faster-whisper` (only needed for talking clips) |
| The voice mispronounces a word | add `"say"` with a phonetic spelling (Step 6) |
| Subtitles have a wrong word on a talking clip | set the right `defaults.transcribe.language` in `channel.json`; use `"model": "medium"` for better accuracy |
| Text runs off the card | shorten it, or lower `headline_size` / `big_size` |
| Name/colors changed but the cards still look old | delete `brand/card_background.png` and build again |

## Using Claude Code
This repository is also a Claude Code skill. Open the folder in Claude Code and ask, for example:
*"Write a new fact Reel about the Chocolate Hills using the clips in stories/004_chocolate_hills/clips, then build it."*
Claude writes `story.json`, looks at your footage and builds the video.

## License
The kit's code is MIT. Your stories, brand, artwork and videos are yours. The Juan Fact a Day name, mascot and
artwork are **not** part of this kit and are all rights reserved.
