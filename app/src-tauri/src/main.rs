// No console window in release builds on Windows.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod secrets;
mod sidecar;

use std::path::Path;

use sidecar::{Engine, EngineInfo};
use tauri::{AppHandle, Manager, RunEvent, State, WindowEvent};

const MAIN_WINDOW: &str = "main";

#[tauri::command]
async fn engine_info(app: AppHandle, engine: State<'_, Engine>) -> Result<EngineInfo, String> {
    engine.info(&app).await
}

#[tauri::command]
async fn set_ai_key(provider: String, key: String) -> Result<(), String> {
    secrets::set_key(&provider, &key)
}

#[tauri::command]
async fn get_ai_key(provider: String) -> Result<Option<String>, String> {
    secrets::get_key(&provider)
}

#[tauri::command]
async fn delete_ai_key(provider: String) -> Result<(), String> {
    secrets::delete_key(&provider)
}

/// `explorer.exe /select,<path>` for an existing absolute local file or folder. No shell is involved.
#[tauri::command]
async fn show_in_folder(path: String) -> Result<(), String> {
    let path = local_existing_path(&path)?;
    open_in_file_manager(path).map_err(|e| {
        eprintln!("[vedit] show_in_folder: {e}");
        "show_in_folder_failed".to_string()
    })
}

/// Only absolute local paths that exist: no relative paths, no UNC/network or `\\?\` / `\\.\` device paths.
/// The check runs before `exists()`, which would otherwise open an SMB connection for a UNC path.
fn local_existing_path(path: &str) -> Result<&Path, String> {
    let p = Path::new(path);
    if !is_local_absolute(p) {
        return Err("invalid_path".into());
    }
    if !p.exists() {
        return Err("path_not_found".into());
    }
    Ok(p)
}

/// Windows: a plain drive path with a root (`C:\...`), judged by how Rust parses it, so mixed separators such as
/// `/\server\share` (UNC) or `/\?\C:\x` (verbatim) are caught too. `C:x` (drive-relative) has no root.
#[cfg(windows)]
fn is_local_absolute(p: &Path) -> bool {
    use std::path::{Component, Prefix};
    matches!(p.components().next(), Some(Component::Prefix(pre)) if matches!(pre.kind(), Prefix::Disk(_)))
        && p.has_root()
}

#[cfg(not(windows))]
fn is_local_absolute(p: &Path) -> bool {
    p.is_absolute() && !p.to_string_lossy().starts_with("//")
}

#[cfg(windows)]
fn select_arg(path: &Path) -> String {
    // explorer only understands backslashes; the whole thing must be one argument.
    format!("/select,{}", path.to_string_lossy().replace('/', "\\"))
}

#[cfg(windows)]
fn open_in_file_manager(path: &Path) -> std::io::Result<()> {
    // explorer.exe exits with 1 even on success, so its status is not checked.
    std::process::Command::new("explorer.exe").arg(select_arg(path)).spawn().map(drop)
}

#[cfg(target_os = "macos")]
fn open_in_file_manager(path: &Path) -> std::io::Result<()> {
    std::process::Command::new("open").arg("-R").arg(path).spawn().map(drop)
}

#[cfg(all(unix, not(target_os = "macos")))]
fn open_in_file_manager(path: &Path) -> std::io::Result<()> {
    let folder = if path.is_dir() { path } else { path.parent().unwrap_or(path) };
    std::process::Command::new("xdg-open").arg(folder).spawn().map(drop)
}

fn main() {
    let app = tauri::Builder::default()
        // Must be first: a second launch focuses the running window instead of starting another engine.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        // vedit:// links (the browser hands Google/Facebook sign in back to the app); after single-instance, which
        // forwards a link opened while the app runs to this instance
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Engine::new())
        .setup(|app| {
            // Installed builds register vedit:// in the installer; a dev run registers it for this exe.
            #[cfg(all(debug_assertions, windows))]
            {
                use tauri_plugin_deep_link::DeepLinkExt;
                if let Err(e) = app.deep_link().register_all() {
                    eprintln!("[vedit] vedit:// link registration: {e}");
                }
            }
            // Start the engine while the UI loads; engine_info awaits the same start.
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let _ = handle.state::<Engine>().info(&handle).await;
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            engine_info,
            set_ai_key,
            get_ai_key,
            delete_ai_key,
            show_in_folder
        ])
        .build(tauri::generate_context!())
        .expect("failed to build the Vedit Studio app");

    app.run(|handle, event| match event {
        RunEvent::Exit => handle.state::<Engine>().shutdown(),
        RunEvent::WindowEvent { label, event: WindowEvent::Destroyed, .. } if label == MAIN_WINDOW => {
            handle.state::<Engine>().shutdown()
        }
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_paths_are_rejected() {
        assert_eq!(local_existing_path(r"C:\definitely\not\here\x.mp4").unwrap_err(), "path_not_found");
    }

    #[test]
    fn relative_paths_are_rejected_even_if_they_exist() {
        // cargo runs tests from the manifest dir, so these exist relative to the cwd.
        assert!(Path::new("Cargo.toml").exists());
        for p in ["", "Cargo.toml", "./Cargo.toml", "src", r"..\src-tauri\Cargo.toml"] {
            assert_eq!(local_existing_path(p).unwrap_err(), "invalid_path", "{p:?}");
        }
    }

    #[cfg(windows)]
    #[test]
    fn drive_relative_and_rooted_paths_are_rejected() {
        for p in [r"C:Cargo.toml", r"\Windows", "/Windows"] {
            assert_eq!(local_existing_path(p).unwrap_err(), "invalid_path", "{p:?}");
        }
    }

    #[test]
    fn unc_and_device_paths_are_rejected() {
        let here = env!("CARGO_MANIFEST_DIR");
        for p in [
            r"\\server\share\x.mp4".to_string(),
            "//server/share/x.mp4".to_string(),
            r"\\localhost\C$\Windows".to_string(),
            format!(r"\\?\{here}"),
            r"/\server\share\x.mp4".to_string(),
            r"\/server\share\x.mp4".to_string(),
            r"/\.\C:\x".to_string(),
            r"/\?\C:\x".to_string(),
            r"\\?\C:\x".to_string(),
            r"\\.\C:\x".to_string(),
            r"\\?\UNC\server\share\x".to_string(),
        ] {
            assert_eq!(local_existing_path(&p).unwrap_err(), "invalid_path", "{p:?}");
        }
    }

    #[test]
    fn existing_absolute_paths_are_accepted() {
        let here = env!("CARGO_MANIFEST_DIR");
        assert!(local_existing_path(here).is_ok());
        assert!(local_existing_path(&format!("{here}/Cargo.toml")).is_ok());
    }

    #[cfg(windows)]
    #[test]
    fn select_arg_is_one_backslashed_argument() {
        assert_eq!(select_arg(Path::new("C:/a b/c.mp4")), r"/select,C:\a b\c.mp4");
    }
}
