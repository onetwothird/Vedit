# Build the Vedit Studio installer (Windows), from the repo root:
#   powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
#
# 1. a clean virtual environment (build\venv) with only the engine's dependencies, so nothing else installed on
#    this PC ends up in the app
# 2. packaging\build_engine.py: the engine folder and FFmpeg, into app\src-tauri\engine and app\src-tauri\ffmpeg
# 3. npm run tauri build: the NSIS installer in app\src-tauri\target\release\bundle\nsis
#
# Update signing: set TAURI_SIGNING_PRIVATE_KEY to the path or contents of the private updater key (kept outside the
# repo, for example %USERPROFILE%\.tauri\vedit-updater.key) and TAURI_SIGNING_PRIVATE_KEY_PASSWORD (empty if none).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root "build\venv\Scripts\python.exe"
if (-not (Test-Path $py)) { python -m venv build\venv }
& $py -m pip install --upgrade pip --quiet
& $py -m pip install --quiet -e ".[studio,all]" "pyinstaller>=6.10"
& $py packaging\build_engine.py @args
if ($LASTEXITCODE -ne 0) { throw "engine build failed" }

if (-not $env:TAURI_SIGNING_PRIVATE_KEY) {
  $key = Join-Path $env:USERPROFILE ".tauri\vedit-updater.key"
  if (Test-Path $key) {
    $env:TAURI_SIGNING_PRIVATE_KEY = Get-Content $key -Raw
    if ($null -eq $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD) { $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = "" }
  }
}
Set-Location app
npm ci
npm run tauri build
if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
Get-ChildItem src-tauri\target\release\bundle\nsis
