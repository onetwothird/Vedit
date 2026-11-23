# Vedit Studio app

- Real videos: `VEDIT_DEV_ENGINE=1 npm run tauri dev` (debug builds only) runs the engine with the system Python (`python scripts/engine_entry.py serve --port 0`, repo root found from `CARGO_MANIFEST_DIR`); Python needs the repo's requirements installed.
- `npm run dev:mock` is a browser demo with a sample clip and a pretend engine; it never reads your files, and the
  page says Demo.
- The preview loads the video, filmstrip and b-roll from the engine (`/projects/{id}/media`,
  `/projects/{id}/filmstrip.jpg`, `/file`), which streams them with seeking. Those GET routes take the token as
  `?token=` because media elements cannot send headers; `/file` serves only b-roll a project's clip already uses.
- Release builds spawn the `bin/vedit-engine` sidecar (`src-tauri/bin/vedit-engine-<target-triple>.exe`, the PyInstaller engine from Task 22). `src-tauri/bin/` is gitignored.
- `src-tauri/build.rs` writes an empty placeholder there when the file is missing, so `cargo build`, `cargo test` and `tauri dev` work without it; a release build fails while the placeholder is there (run the Task 22 engine build first).
- `cargo test -- --ignored` (in `src-tauri/`) also runs the round-trip test against the real Windows Credential Manager.
