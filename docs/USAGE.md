# Using vedit, step by step

## 1. One-time setup (Windows)
```powershell
winget install Gyan.FFmpeg              # then open a NEW terminal
python --version                        # need 3.9+ (winget install Python.Python.3.12)
cd $HOME\Documents\vedit
pip install -e ".[all]"
vedit doctor                            # everything required should say [ok]

# let Claude Code find the skill (a junction is a live link, so edits in Documents apply instantly)
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
New-Item -ItemType Junction -Path "$HOME\.claude\skills\vedit" -Target "$HOME\Documents\vedit"
```
macOS/Linux: same steps, with `brew install ffmpeg` / `apt install ffmpeg` and `ln -s "$PWD" ~/.claude/skills/vedit`.

> The first transcription downloads a Whisper model (~500 MB for `small`). For faster runs on weak PCs use `--model base`. If you have an NVIDIA GPU it is used automatically.

## 2. Edit a raw video with Claude Code
```powershell
cd D:\Videos\episode-12
claude
```
Then say what you want, for example:
- *"Edit raw.mp4 for YouTube. Remove mistakes and boring parts, keep it under 10 minutes, add captions, chapters and 3 thumbnail options."*
- *"Turn raw.mp4 into 3 Shorts with the best moments."*
- *"Use my brand.json and also export a 9:16 version."*

Claude will analyze, draft, refine, render a preview, check frames, render the final, and summarize what it cut. Ask for changes in plain words ("keep the joke at 3:10", "less zoom", "bigger captions") and only the changed clips re-render.

## 3. Or do it yourself with the CLI
```powershell
vedit auto raw.mp4 --preset youtube --thumbs
```
Output appears next to the video: `raw_youtube.mp4`, `raw_youtube.srt`, plus a `raw.edit\` work folder with the transcript, `edl.youtube.json` and thumbnails.

Want to tweak? Open `raw.edit\transcript.main.txt`, edit `raw.edit\edl.youtube.json` (delete a clip, change `"words": [a, b]`), then:
```powershell
vedit render raw.edit\edl.youtube.json --preview     # fast check
vedit render raw.edit\edl.youtube.json               # final
```

## 4. Make a skit from photos
Put photos in a folder, then ask Claude Code: *"Make a 30-second funny skit from these photos. Maria is on the left, Jose on the right. Use Filipino-English voices."*

Or by hand: copy `examples/story.example.json`, edit it, then:
```powershell
vedit story grid story.json        # gridded photos -> choose face "focus" points
vedit story voices story.json      # hear/check timing (cached, free with edge voices)
vedit story build story.json
```
Premium voices (optional): `setx OPENAI_API_KEY "..."` or `setx ELEVENLABS_API_KEY "..."`, then use `"engine": "openai"` / `"elevenlabs"` in `voices`.

## 5. Tips
- **Brand once:** create `~/.vedit/brand.json` from `examples/brand.example.json` and every video gets your logo, colors, intro/outro and music.
- **Batch:** `vedit auto *.mp4 --preset shorts` processes a whole folder.
- **Different language:** `--language tl` (Tagalog), `es`, `ja`...
- **Screen recordings to vertical:** `--preset tutorial --aspect 9:16` uses a blurred background instead of cropping your code away.
- **Something looks off?** `vedit frames output.mp4 --every 5` makes contact sheets so you can see the whole video at a glance.
