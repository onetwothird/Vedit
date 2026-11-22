# vedit Studio app

- Dev: `VEDIT_DEV_ENGINE=1 npm run tauri dev` runs the engine with the system Python (`python scripts/engine_entry.py serve --port 0`, repo root found from `CARGO_MANIFEST_DIR`); Python needs the repo's requirements installed.
- Release builds spawn the `bin/vedit-engine` sidecar (`src-tauri/bin/vedit-engine-<target-triple>.exe`, the PyInstaller engine from Task 22). `src-tauri/bin/` is gitignored.
- `src-tauri/build.rs` writes an empty placeholder there when the file is missing, so `cargo build`, `cargo test` and `tauri dev` work without it; a build that still has the placeholder cannot start its engine (`engine_start_failed`).
- `cargo test -- --ignored` (in `src-tauri/`) also runs the round-trip test against the real Windows Credential Manager.
