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

use mangaflow_desktop_shell_core::base64_encode;
use mangaflow_desktop_shell_core::handshake::{get_status, spawn_helper, HelperConfig, SpawnedHelper};
use mangaflow_desktop_shell_core::logs::export_logs_zip_overwrite;
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

/// Export the unified logs directory to a ZIP archive through a native save
/// dialog; `Ok(None)` means the user cancelled. #149: the destination is
/// chosen exclusively by the user inside the `rfd` dialog — this command no
/// longer accepts a renderer-supplied `destination` argument (an unguarded
/// arbitrary-overwrite primitive), and shell-core refuses an existing
/// destination unless the caller carries the dialog's overwrite
/// confirmation. Returns `Ok(None)` when the user cancels the dialog.
#[tauri::command]
fn desktop_export_logs(paths: tauri::State<ShellPaths>) -> Result<Option<ExportReportDto>, String> {
    let Some(path) = rfd::FileDialog::new()
        .set_title("导出运行日志")
        .set_file_name("mangaflow-logs.zip")
        .add_filter("ZIP 归档", &["zip"])
        .save_file()
    else {
        return Ok(None);
    };
    run_export(&paths.user_data, &path)
}

fn run_export(user_data: &Path, destination: &Path) -> Result<Option<ExportReportDto>, String> {
    // The only sanctioned overwrite of an existing destination: the rfd save
    // dialog above already prompted the user about replacing it (#149).
    export_logs_zip_overwrite(user_data, destination)
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

fn helper_environment() -> Option<(std::path::PathBuf, std::path::PathBuf)> {
    let python = std::env::var_os("MANGAFLOW_DESKTOP_PYTHON")?;
    let script = std::env::var_os("MANGAFLOW_DESKTOP_HELPER")?;
    Some((python.into(), script.into()))
}

/// Build the base helper argv (everything except `--web-dist`). The fake
/// model channel is a dev/acceptance stub (ADR native-windows-client §: tests
/// enable it via an explicit environment switch) — a production launch must
/// not silently install the mock provider, mirroring the WPF leg's refusal
/// to inherit development flags (NativeBackend.cs).
fn build_helper_args(api_root: &str, user_data: &str, fake_channel: bool) -> Vec<String> {
    let mut args = vec![
        "app".to_string(),
        "--api-root".to_string(),
        api_root.to_string(),
        "--user-data".to_string(),
        user_data.to_string(),
    ];
    if fake_channel {
        args.push("--fake-channel".into());
    }
    args
}

/// #275: `--fake-channel` may only be appended when the launcher explicitly
/// opts in via `MANGAFLOW_DESKTOP_FAKE_CHANNEL=1`. Nothing in the repo sets
/// that env var (start-desktop.cmd and the installed form never do; the e2e
/// and D5 scripts pass `--fake-channel` as argv instead), so the flag can
/// only arrive from a deliberately configured launcher environment.
fn fake_channel_requested(env: Option<std::ffi::OsString>) -> bool {
    env.as_deref() == Some(std::ffi::OsStr::new("1"))
}

/// The ONE shutdown path for an owned helper run, used by BOTH the
/// `RunEvent::Exit` handler and the setup-failure path below: an aborted
/// setup must never leave `owner.json` at state "ready" for a dead run.
/// `tree.stop` performs the graceful stop (stdin-EOF cooperative phase, then
/// the fail-closed kill escalation); afterwards the RunLog "stopped"
/// milestone and the ownership-journal `mark_stopped` are recorded.
fn stop_helper(helper: &mut Option<SpawnedHelper>) {
    if let Some(mut spawned) = helper.take() {
        let exit_code = spawned
            .tree
            .stop(std::time::Duration::from_secs(5))
            .ok()
            .flatten();
        // #150: a failing milestone or journal write at shutdown is no longer
        // silently discarded — stderr is the one channel a broken logging
        // system may still use without recursing into itself.
        if let Err(error) = spawned
            .log
            .record("stopped", &serde_json::json!({ "exit_code": exit_code }))
        {
            eprintln!("mangaflow-desktop: run log 'stopped' milestone failed: {error}");
        }
        if let Err(error) = spawned.layout.mark_stopped(exit_code) {
            eprintln!("mangaflow-desktop: marking the ownership journal stopped failed: {error}");
        }
    }
}

fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                // Windows real-machine finding: set_focus alone cannot bring
                // a minimized window back (SetForegroundWindow does not
                // restore), so a second launch while minimized looked like
                // "nothing happened". Unminimize first, then focus.
                let _ = window.unminimize();
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

            // Plan B (W-15): point the helper at the bundled Next standalone
            // bundle; the helper then spawns node, announces the loopback
            // web origin in READY, and the WebView loads it. Absent = the
            // pre-W-15 static-export form.
            let mut helper_args = build_helper_args(
                &api_root.to_string_lossy(),
                &user_data.to_string_lossy(),
                fake_channel_requested(std::env::var_os("MANGAFLOW_DESKTOP_FAKE_CHANNEL")),
            );
            if let Some(web_dist) = std::env::var_os("MANGAFLOW_DESKTOP_WEB_DIST") {
                helper_args.push("--web-dist".into());
                helper_args.push(web_dist.to_string_lossy().into_owned());
            } else {
                // Install form: the packaging step lays the Next standalone
                // tree and the node runtime under the bundled resources
                // (tauri.conf resources); their presence enables plan B
                // without any env var. Resources resolve next to the
                // executable in the installed layout.
                let bundled = std::env::current_exe()
                    .ok()
                    .and_then(|exe| exe.parent().map(|dir| dir.join("web").join("standalone")));
                if let Some(bundled) = bundled {
                    if bundled.join("server.js").is_file() {
                        helper_args.push("--web-dist".into());
                        helper_args.push(bundled.to_string_lossy().into_owned());
                    }
                }
            }

            let config = HelperConfig {
                python,
                helper_script,
                helper_args,
                ready_timeout: std::time::Duration::from_secs(15),
                health_timeout: std::time::Duration::from_secs(10),
            };
            let spawned = spawn_helper(&config, &user_data)
                .map_err(|error| format!("sidecar handshake failed: {error:?}"))?;

            // From here on, EVERY failure path must run the same shutdown
            // bookkeeping as the RunEvent::Exit handler (RunLog "stopped"
            // milestone + ownership-journal mark_stopped): an aborted setup
            // must never leave owner.json at state "ready" for a dead run.
            // stop_helper performs the graceful stop and both records; the
            // OwnedTree drop inside it still does the fail-closed kill.
            let origin = spawned.ready.api_origin.clone();
            let origin_literal = match serde_json::to_string(&origin) {
                Ok(literal) => literal,
                Err(error) => {
                    let message = error.to_string();
                    stop_helper(&mut Some(spawned));
                    return Err(message.into());
                }
            };
            let initialization_script =
                format!("window.__MANGAFLOW_API_ORIGIN__ = {origin_literal};\n");

            app.manage(ApiOrigin(origin));
            app.manage(HelperState(Mutex::new(Some(spawned))));
            app.manage(ShellPaths {
                user_data: user_data.clone(),
            });
            app.manage(PickedState(PickedRegistry::new()));

            // Plan B (W-15): when the helper manages the bundled Next
            // standalone server, the WebView loads that loopback origin —
            // the full production app with rewrites — instead of the static
            // export embedded at compile time. The origin was already
            // verified loopback by the protocol; spawn_helper validated it
            // against the journal, so building the URL here cannot escape
            // the loopback rule.
            let web_url: Option<tauri::WebviewUrl> = {
                let spawned_state = app.state::<HelperState>();
                let guard = spawned_state.inner().0.lock().expect("helper state lock");
                let run = guard.as_ref().expect("helper run present");
                run.ready
                    .web_origin
                    .as_deref()
                    .and_then(|origin| tauri::Url::parse(origin).ok())
                    .map(tauri::WebviewUrl::External)
            };
            let builder = match &web_url {
                Some(url) => tauri::WebviewWindowBuilder::new(app, "main", url.clone()),
                None => tauri::WebviewWindowBuilder::new(
                    app,
                    "main",
                    tauri::WebviewUrl::App("index.html".into()),
                ),
            };
            if let Err(error) = builder
                .title("MangaFlow")
                .inner_size(1280.0, 800.0)
                .initialization_script(&initialization_script)
                .build()
            {
                // Fail-closed kill behavior is unchanged; the shutdown
                // BOOKKEEPING now routes through the same helper as the Exit
                // path — take the run back out of the managed state so the
                // "stopped" milestone and journal update still happen.
                if let Some(state) = app.try_state::<HelperState>() {
                    stop_helper(&mut state.inner().0.lock().expect("helper state lock"));
                }
                return Err(error.into());
            }
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

#[cfg(test)]
mod tests {
    use super::{build_helper_args, fake_channel_requested};

    fn args(fake: bool) -> Vec<String> {
        build_helper_args("C:/api", "C:/data", fake)
    }

    #[test]
    fn production_arg_set_has_no_fake_channel() {
        let produced = args(false);
        assert_eq!(
            produced,
            vec!["app", "--api-root", "C:/api", "--user-data", "C:/data"]
        );
    }

    #[test]
    fn explicit_opt_in_appends_fake_channel() {
        let produced = args(true);
        assert_eq!(produced.last().map(String::as_str), Some("--fake-channel"));
    }

    #[test]
    fn fake_channel_requires_exact_env_switch() {
        assert!(fake_channel_requested(Some("1".into())));
        assert!(!fake_channel_requested(None));
        assert!(!fake_channel_requested(Some("0".into())));
        assert!(!fake_channel_requested(Some("true".into())));
    }
}
