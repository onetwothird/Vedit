use std::path::PathBuf;

fn main() {
    ensure_engine_placeholder();
    tauri_build::build()
}

/// tauri-build requires every `externalBin` to exist as `bin/vedit-engine-<target-triple>[.exe]`.
/// The real PyInstaller engine lands there in Task 22; until then (and on any machine without it) write an
/// empty placeholder so `cargo build`/`cargo test`/`tauri dev` work. Dev runs use VEDIT_DEV_ENGINE=1 (system
/// Python) instead. `bin/` is gitignored; a real binary is never overwritten. A release build refuses to go on
/// without the real engine.
fn ensure_engine_placeholder() {
    let target = std::env::var("TARGET").expect("cargo sets TARGET");
    let ext = if target.contains("windows") { ".exe" } else { "" };
    let path = PathBuf::from("bin").join(format!("vedit-engine-{target}{ext}"));
    println!("cargo:rerun-if-changed={}", path.display());
    let is_placeholder = std::fs::metadata(&path).map_or(true, |m| m.len() == 0);
    if is_placeholder && std::env::var("PROFILE").as_deref() == Ok("release") {
        panic!(
            "release build without the engine: {} is missing or the empty placeholder. \
             Run the Task 22 engine build (PyInstaller vedit-engine) to put the real binary there first.",
            path.display()
        );
    }
    if !path.exists() {
        std::fs::create_dir_all("bin").expect("create bin/");
        std::fs::write(&path, b"").expect("write engine placeholder");
        println!("cargo:warning=wrote placeholder {}; the real engine arrives with the Task 22 build", path.display());
    }
}
