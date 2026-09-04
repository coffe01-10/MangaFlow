//! MangaFlow desktop shell (V02-54 delivery + V02-54B desktop UX).
//!
//! Startup order implements the frozen ADR protocol (§4.2/§4.4): the helper
//! is spawned and verified FIRST; the WebView is only created after the
//! handshake succeeds, and the verified loopback `api_origin` is injected
//! both as a synchronous initialization-script global (available before any
//! page script) and through the `desktop_get_api_origin` invoke command. Nothing
//! relies on `NEXT_PUBLIC_*` or the Next.js rewrite at runtime.
//!
//! V02-54B adds the user-facing shell commands for log export and local
//! file/directory picking. All validation lives in shell-core; the commands
//! here only open native dialogs and marshal results.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use mangaflow_desktop_shell_core::handshake::{get_status, spawn_helper, HelperConfig, SpawnedHelper};
use mangaflow_desktop_shell_core::logs::export_logs_zip;
use mangaflow_desktop_shell_core::picker::{
    read_registered_file, validate_picked_directory, validate_picked_file, PickError, PickKind,
    PickedFile, PickedRegistry,
};
use mangaflow_desktop_shell_core::protocol::HEALTH_PATH;
use serde::Serialize;
use tauri::Manager;

/// Verified loopback API origin of the owned sidecar helper.
struct ApiOrigin(String);

/// Owned helper tree; taken and stopped on app exit.
struct HelperState(Mutex<Option<SpawnedHelper>>);

/// Read-only shell paths shared with the invoke commands.
struct ShellPaths {
    user_data: PathBuf,
}

/// Session-scoped registry of validated file picks (see shell-core picker).
struct PickedState(PickedRegistry);

#[derive(Serialize)]
struct ExportReportDto {
    destination: PathBuf,
    files: Vec<String>,
    skipped: Vec<String>,
    total_bytes: u64,
}

#[derive(Serialize)]
struct PickedFileDto {
    path: String,
    name: String,
    size_bytes: u64,
    kind: String,
}

#[derive(Serialize)]
struct PickedDirectoryDto {
    path: String,
    name: String,
}

#[derive(Serialize)]
struct ReadPickedFileDto {
    name: String,
    size_bytes: u64,
    content_base64: String,
}

#[tauri::command]
fn desktop_get_api_origin(origin: tauri::State<ApiOrigin>) -> String {
    origin.0.clone()
}

#[tauri::command]
fn desktop_health_probe(origin: tauri::State<ApiOrigin>) -> Result<u16, String> {
    get_status(&origin.0, HEALTH_PATH, std::time::Duration::from_secs(2))
        .map(|(status, _)| status)
        .map_err(|error| error.to_string())
}

/// Export the unified logs directory to a ZIP archive. Without a
/// `destination` argument a native save dialog lets the user choose the
/// path; with one, the same shell-core validation applies (absolute, no
/// `.`/`..`, never inside the user-data root). Returns `Ok(None)` when the
/// user cancels the dialog.
#[tauri::command]
fn desktop_export_logs(
    destination: Option<String>,
    paths: tauri::State<ShellPaths>,
) -> Result<Option<ExportReportDto>, String> {
    let destination = match destination {
        Some(raw) => PathBuf::from(raw),
        None => {
            return match rfd::FileDialog::new()
                .set_title("导出运行日志")
                .set_file_name("mangaflow-logs.zip")
                .add_filter("ZIP 归档", &["zip"])
                .save_file()
            {
                Some(path) => run_export(&paths.user_data, &path),
                None => Ok(None),
            }
        }
    };
    run_export(&paths.user_data, &destination)
}

fn run_export(user_data: &Path, destination: &Path) -> Result<Option<ExportReportDto>, String> {
    export_logs_zip(user_data, destination)
        .map(|report| {
            Some(ExportReportDto {
                destination: report.destination,
                files: report.files,
                skipped: report
                    .skipped
                    .iter()
                    .map(|entry| format!("{}: {}", entry.name, entry.reason))
                    .collect(),
                total_bytes: report.total_bytes,
            })
        })
        .map_err(|error| error.to_string())
}

/// Open a native file dialog for 原作/素材 selection and register the
/// validated result. `kind` is `source_text` or `reference_image`; policies
/// mirror the API upload boundaries. `Ok(None)` = user cancelled.
#[tauri::command]
fn desktop_pick_file(
    kind: String,
    picked: tauri::State<PickedState>,
) -> Result<Option<PickedFileDto>, String> {
    let kind = PickKind::parse(&kind).ok_or("unknown pick kind; expected source_text | reference_image")?;
    let (label, extensions) = kind.dialog_filter();
    let chosen = rfd::FileDialog::new()
        .set_title(match kind {
            PickKind::SourceText => "选择原作/正文文件",
            PickKind::ReferenceImage => "选择图片素材",
        })
        .add_filter(label, extensions)
        .pick_file();
    let Some(chosen) = chosen else {
        return Ok(None);
    };
    let validated = validate_picked_file(&chosen, kind).map_err(pick_error_message)?;
    let dto = picked_file_dto(&validated);
    picked.0.register(&validated);
    Ok(Some(dto))
}

/// Open a native directory dialog (import roots). Validation rejects
/// symlinks and traversal shapes; `Ok(None)` = user cancelled.
#[tauri::command]
fn desktop_pick_directory() -> Result<Option<PickedDirectoryDto>, String> {
    let chosen = rfd::FileDialog::new()
        .set_title("选择目录")
        .pick_folder();
    let Some(chosen) = chosen else {
        return Ok(None);
    };
    let validated = validate_picked_directory(&chosen).map_err(pick_error_message)?;
    Ok(Some(PickedDirectoryDto {
        path: validated.path.to_string_lossy().into_owned(),
        name: validated.name,
    }))
}

/// Read back a previously picked file so the page can upload it through the
/// ordinary API upload endpoints. Every call re-validates membership in the
/// session registry and the full pick policy.
#[tauri::command]
fn desktop_read_picked_file(
    path: String,
    picked: tauri::State<PickedState>,
) -> Result<ReadPickedFileDto, String> {
    if path.is_empty() {
        return Err(pick_error_message(PickError::EmptyPath));
    }
    let (validated, bytes) = read_registered_file(&picked.0, Path::new(&path))
        .map_err(pick_error_message)?;
    Ok(ReadPickedFileDto {
        name: validated.name,
        size_bytes: validated.size_bytes,
        content_base64: base64_encode(&bytes),
    })
}

fn picked_file_dto(picked: &PickedFile) -> PickedFileDto {
    PickedFileDto {
        path: picked.path.to_string_lossy().into_owned(),
        name: picked.name.clone(),
        size_bytes: picked.size_bytes,
        kind: match picked.kind {
            PickKind::SourceText => "source_text".into(),
            PickKind::ReferenceImage => "reference_image".into(),
        },
    }
}

fn pick_error_message(error: PickError) -> String {
    error.to_string()
}

/// Standard base64 (RFC 4648, padded) so ≤20 MiB picked files survive the
/// JSON IPC bridge without a byte-per-number array blow-up.
fn base64_encode(data: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = chunk.get(1).copied().unwrap_or(0) as u32;
        let b2 = chunk.get(2).copied().unwrap_or(0) as u32;
        let triple = b0 << 16 | b1 << 8 | b2;
        out.push(ALPHABET[(triple >> 18) as usize & 0x3f] as char);
        out.push(ALPHABET[(triple >> 12) as usize & 0x3f] as char);
        out.push(if chunk.len() > 1 {
            ALPHABET[(triple >> 6) as usize & 0x3f] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            ALPHABET[triple as usize & 0x3f] as char
        } else {
            '='
        });
    }
    out
}

fn helper_environment() -> Option<(std::path::PathBuf, std::path::PathBuf)> {
    let python = std::env::var_os("MANGAFLOW_DESKTOP_PYTHON")?;
    let script = std::env::var_os("MANGAFLOW_DESKTOP_HELPER")?;
    Some((python.into(), script.into()))
}

fn stop_helper(helper: &mut Option<SpawnedHelper>) {
    if let Some(mut spawned) = helper.take() {
        let exit_code = spawned
            .tree
            .stop(std::time::Duration::from_secs(5))
            .ok()
            .flatten();
        let _ = spawned
            .log
            .record("stopped", &serde_json::json!({ "exit_code": exit_code }));
        let _ = spawned.layout.mark_stopped(exit_code);
    }
}

fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
            }
        }))
        .invoke_handler(tauri::generate_handler![
            desktop_get_api_origin,
            desktop_health_probe,
            desktop_export_logs,
            desktop_pick_file,
            desktop_pick_directory,
            desktop_read_picked_file
        ])
        .setup(|app| {
            // Environment contract for the desktop run (see README):
            // MANGAFLOW_DESKTOP_PYTHON / MANGAFLOW_DESKTOP_HELPER / MANGAFLOW_DESKTOP_API_ROOT.
            let (python, helper_script) = helper_environment()
                .ok_or("MANGAFLOW_DESKTOP_PYTHON / MANGAFLOW_DESKTOP_HELPER not set")?;
            let api_root = std::env::var_os("MANGAFLOW_DESKTOP_API_ROOT")
                .ok_or("MANGAFLOW_DESKTOP_API_ROOT not set")?;
            let user_data = app.path().app_local_data_dir()?;

            let config = HelperConfig {
                python,
                helper_script,
                helper_args: vec![
                    "app".into(),
                    "--api-root".into(),
                    api_root.to_string_lossy().into_owned(),
                    "--user-data".into(),
                    user_data.to_string_lossy().into_owned(),
                    "--fake-channel".into(),
                ],
                ready_timeout: std::time::Duration::from_secs(15),
                health_timeout: std::time::Duration::from_secs(10),
            };
            let spawned = spawn_helper(&config, &user_data)
                .map_err(|error| format!("sidecar handshake failed: {error:?}"))?;

            // Synchronous injection: available before any page script runs.
            // The origin is embedded as a JSON string literal so every byte is
            // escaped by serde_json instead of raw format! interpolation.
            let origin = spawned.ready.api_origin.clone();
            let origin_literal =
                serde_json::to_string(&origin).map_err(|error| error.to_string())?;
            let initialization_script =
                format!("window.__MANGAFLOW_API_ORIGIN__ = {origin_literal};\n");

            app.manage(ApiOrigin(origin));
            app.manage(HelperState(Mutex::new(Some(spawned))));
            app.manage(ShellPaths {
                user_data: user_data.clone(),
            });
            app.manage(PickedState(PickedRegistry::new()));

            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .title("MangaFlow")
            .inner_size(1280.0, 800.0)
            .initialization_script(&initialization_script)
            .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the MangaFlow desktop shell")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app.try_state::<HelperState>() {
                    stop_helper(&mut state.inner().0.lock().expect("helper state lock"));
                }
            }
        });
}

fn main() {
    run();
}
