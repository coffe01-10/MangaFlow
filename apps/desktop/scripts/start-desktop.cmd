@echo off
rem MangaFlow desktop launcher (dev form): wires the sidecar environment the
rem shell expects, then starts the release build (falls back to debug).
rem Double-clickable; the repo layout is resolved relative to this script.
setlocal
set "REPO=%~dp0..\..\.."

if exist "%REPO%\apps\desktop\src-tauri\target\release\mangaflow-desktop-shell.exe" (
  set "SHELL_EXE=%REPO%\apps\desktop\src-tauri\target\release\mangaflow-desktop-shell.exe"
) else (
  set "SHELL_EXE=%REPO%\apps\desktop\src-tauri\target\debug\mangaflow-desktop-shell.exe"
)

if not exist "%SHELL_EXE%" (
  echo shell exe not found; run: cd apps\desktop\src-tauri ^&^& cargo build
  pause
  exit /b 1
)
if not exist "%REPO%\.venv-desktop\Scripts\python.exe" (
  echo .venv-desktop missing; run apps\desktop\scripts\run-sidecar-e2e.sh once
  pause
  exit /b 1
)

set "MANGAFLOW_DESKTOP_PYTHON=%REPO%\.venv-desktop\Scripts\python.exe"
set "MANGAFLOW_DESKTOP_HELPER=%REPO%\apps\desktop\sidecar\mangaflow_desktop_helper.py"
set "MANGAFLOW_DESKTOP_API_ROOT=%REPO%\apps\api"
if exist "%REPO%\apps\desktop\dist\web-standalone\server.js" (
  set "MANGAFLOW_DESKTOP_WEB_DIST=%REPO%\apps\desktop\dist\web-standalone"
)

start "" "%SHELL_EXE%"
endlocal
