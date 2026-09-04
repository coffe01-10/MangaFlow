//! MangaFlow V02-53B disposable Tauri 2 shell PoC.
//!
//! Startup order implements the frozen ADR protocol (§4.2/§4.4): the helper
//! is spawned and verified FIRST; the WebView is only created after the
//! handshake succeeds, and the verified loopback `api_origin` is injected
//! both as a synchronous initialization-script global (available before any
//! page script) and through the `poc_get_api_origin` invoke command. Nothing
//! relies on `NEXT_PUBLIC_*` or the Next.js rewrite at runtime.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;

use mangaflow_poc_shell_core::handshake::{get_status, spawn_helper, HelperConfig, SpawnedHelper};
use mangaflow_poc_shell_core::protocol::HEALTH_PATH;
use tauri::Manager;

/// Verified loopback API origin of the owned sidecar helper.
struct ApiOrigin(String);

/// Owned helper tree; taken and stopped on app exit.
struct HelperState(Mutex<Option<SpawnedHelper>>);

#[tauri::command]
fn poc_get_api_origin(origin: tauri::State<ApiOrigin>) -> String {
    origin.0.clone()
}

#[tauri::command]
fn poc_health_probe(origin: tauri::State<ApiOrigin>) -> Result<u16, String> {
    get_status(&origin.0, HEALTH_PATH, std::time::Duration::from_secs(2))
        .map(|(status, _)| status)
        .map_err(|error| error.to_string())
}

fn helper_environment() -> Option<(std::path::PathBuf, std::path::PathBuf)> {
    let python = std::env::var_os("MANGAFLOW_POC_PYTHON")?;
    let script = std::env::var_os("MANGAFLOW_POC_HELPER")?;
    Some((python.into(), script.into()))
}

fn stop_helper(helper: &mut Option<SpawnedHelper>) {
    if let Some(mut spawned) = helper.take() {
        let exit_code = spawned
            .tree
            .stop(std::time::Duration::from_secs(5))
            .ok()
            .flatten();
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
            poc_get_api_origin,
            poc_health_probe
        ])
        .setup(|app| {
            // Environment contract for the disposable PoC run (see README):
            // MANGAFLOW_POC_PYTHON / MANGAFLOW_POC_HELPER / MANGAFLOW_POC_API_ROOT.
            let (python, helper_script) = helper_environment()
                .ok_or("MANGAFLOW_POC_PYTHON / MANGAFLOW_POC_HELPER not set")?;
            let api_root = std::env::var_os("MANGAFLOW_POC_API_ROOT")
                .ok_or("MANGAFLOW_POC_API_ROOT not set")?;
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

            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .title("MangaFlow PoC")
            .inner_size(1280.0, 800.0)
            .initialization_script(&initialization_script)
            .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the MangaFlow PoC shell")
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
