use std::path::Path;

fn main() {
    check_bundled_engine();
    tauri_build::build()
}

/// A release build bundles engine/ (PyInstaller engine) and ffmpeg/ (FFmpeg) as resources, both written by
/// `python packaging/build_engine.py`. Debug builds, `cargo test` and `tauri dev` don't need them: dev runs use
/// VEDIT_DEV_ENGINE=1 (system Python on ../scripts). Both folders are git-ignored.
fn check_bundled_engine() {
    let exe = if std::env::var("TARGET").unwrap_or_default().contains("windows") { ".exe" } else { "" };
    let engine = format!("engine/vedit-engine{exe}");
    let ffmpeg = format!("ffmpeg/ffmpeg{exe}");
    println!("cargo:rerun-if-changed={engine}");
    println!("cargo:rerun-if-changed={ffmpeg}");
    if std::env::var("PROFILE").as_deref() == Ok("release") {
        for need in [&engine, &ffmpeg] {
            if !Path::new(need).is_file() {
                panic!("release build without {need}: run `python packaging/build_engine.py` from the repo first");
            }
        }
    }
    // tauri-build checks that every resource source exists, also in debug builds
    for dir in ["engine", "ffmpeg"] {
        if !Path::new(dir).exists() {
            std::fs::create_dir_all(dir).expect("create resource folder");
        }
    }
}
