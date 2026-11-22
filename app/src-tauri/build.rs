use std::path::PathBuf;

fn main() {
    ensure_engine_placeholder();
    tauri_build::build()
}

/// tauri-build requires every `externalBin` to exist as `bin/vedit-engine-<target-triple>[.exe]`.
/// The real PyInstaller engine lands there in Task 22; until then (and on any machine without it) write an
/// empty placeholder so `cargo build`/`cargo test`/`tauri dev` work. Dev runs use VEDIT_DEV_ENGINE=1 (system
/// Python) instead. `bin/` is gitignored; a real binary is never overwritten.
fn ensure_engine_placeholder() {
    let target = std::env::var("TARGET").expect("cargo sets TARGET");
    let ext = if target.contains("windows") { ".exe" } else { "" };
    let path = PathBuf::from("bin").join(format!("vedit-engine-{target}{ext}"));
    if !path.exists() {
        std::fs::create_dir_all("bin").expect("create bin/");
        std::fs::write(&path, b"").expect("write engine placeholder");
        println!("cargo:warning=wrote placeholder {}; the real engine arrives with the Task 22 build", path.display());
    }
}
