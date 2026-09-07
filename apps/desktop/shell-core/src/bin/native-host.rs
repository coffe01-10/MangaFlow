//! Headless owner for the WPF client. stdin EOF is the UI lifetime contract.
use std::io::{BufRead, Write};
use std::path::PathBuf;
use std::sync::mpsc;
use std::time::Duration;

use mangaflow_desktop_shell_core::handshake::{spawn_helper, HelperConfig, SpawnedHelper};

fn required(name: &str) -> Result<PathBuf, String> {
    std::env::var_os(name)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .ok_or_else(|| format!("missing {name}"))
}

fn stop(mut helper: SpawnedHelper) -> Result<(), String> {
    // Do not publish a stopped journal if the owner could not stop its tree.
    let code = helper.tree.stop(Duration::from_secs(5))
        .map_err(|error| format!("sidecar cleanup failed: {error:?}"))?;
    let log_result = helper.log.record("stopped", &serde_json::json!({"exit_code": code}));
    let journal_result = helper.layout.mark_stopped(code);
    log_result.map_err(|error| error.to_string())?;
    journal_result.map_err(|error| error.to_string())
}

fn run() -> Result<(), String> {
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        // A single STOP line, EOF, or broken pipe all request shutdown.
        let mut line = String::new();
        let _ = std::io::stdin().lock().read_line(&mut line);
        let _ = tx.send(());
    });
    let python = required("MANGAFLOW_DESKTOP_PYTHON")?;
    let helper_script = required("MANGAFLOW_DESKTOP_HELPER")?;
    let user_data = required("MANGAFLOW_DESKTOP_USER_DATA")?;
    let stub = std::env::var("MANGAFLOW_NATIVE_TEST_STUB").as_deref() == Ok("1");
    let helper_args = if stub {
        vec!["stub".into(), "--grandchild".into()]
    } else {
        let api_root = required("MANGAFLOW_DESKTOP_API_ROOT")?;
        vec!["app".into(), "--api-root".into(), api_root.to_string_lossy().into_owned(),
             "--user-data".into(), user_data.to_string_lossy().into_owned()]
    };
    if rx.try_recv().is_ok() { return Ok(()); }
    let mut helper = spawn_helper(&HelperConfig {
        python, helper_script, helper_args,
        ready_timeout: Duration::from_secs(20),
        health_timeout: Duration::from_secs(10),
    }, &user_data).map_err(|error| format!("sidecar startup failed: {error:?}"))?;
    if rx.try_recv().is_ok() { return stop(helper); }
    let announcement = serde_json::json!({"api_origin": helper.ready.api_origin});
    let sent = writeln!(std::io::stdout(), "MANGAFLOW_NATIVE_READY {announcement}")
        .and_then(|_| std::io::stdout().flush());
    if sent.is_err() { return stop(helper); }
    loop {
        match rx.recv_timeout(Duration::from_millis(250)) {
            Ok(()) | Err(mpsc::RecvTimeoutError::Disconnected) => return stop(helper),
            Err(mpsc::RecvTimeoutError::Timeout) => {},
        }
        match helper.tree.child.try_wait() {
            Ok(Some(_)) => {
                stop(helper)?;
                return Err("local API process exited; restart the connection".into());
            },
            Ok(None) => {},
            Err(error) => {
                let message = format!("cannot inspect owned helper: {error}");
                stop(helper)?;
                return Err(message);
            },
        }
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
