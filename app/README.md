# Vedit Studio app

- Real videos: `VEDIT_DEV_ENGINE=1 npm run tauri dev` (debug builds only) runs the engine with the system Python (`python scripts/engine_entry.py serve --port 0`, repo root found from `CARGO_MANIFEST_DIR`); Python needs the repo's requirements installed.
- `npm run dev:mock` is a browser demo with a sample clip and a pretend engine; it never reads your files, and the
  page says Demo.
- The preview loads the video, filmstrip and b-roll from the engine (`/projects/{id}/media`,
  `/projects/{id}/filmstrip.jpg`, `/file`), which streams them with seeking. Those GET routes take the token as
  `?token=` because media elements cannot send headers; `/file` serves only b-roll a project's clip already uses.
- Release builds run the bundled engine, `<install dir>/engine/vedit-engine.exe` (PyInstaller, one folder), with
  FFmpeg in `<install dir>/bin/` (passed to the engine as `VEDIT_FFMPEG_DIR`). `python packaging/build_engine.py`
  writes both into `src-tauri/engine/` and `src-tauri/ffmpeg/` (git-ignored); `src-tauri/build.rs` stops a release
  build that doesn't have them. Debug builds, `cargo test` and `tauri dev` don't need them.
- The whole installer: `powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1` from the repo root
  (see [Building the installer](../README.md#building-the-installer)).
- Google and Facebook sign in open in the system browser; Supabase sends it back to `vedit://auth?code=...`, which
  the installer registers for the app (`tauri-plugin-deep-link`; a second launch is forwarded to the running app).
  `vedit://auth` must be in the Supabase project's redirect URLs.
- `cargo test -- --ignored` (in `src-tauri/`) also runs the round-trip test against the real Windows Credential Manager.
