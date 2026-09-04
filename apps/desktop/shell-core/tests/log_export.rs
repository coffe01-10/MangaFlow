//! V02-54B integration tests: unified log layout + path-safe ZIP export.
//!
//! The archive is validated twice: structurally by the test itself and
//! externally by `python3 -m zipfile`/`zipfile` (independent CRC + layout
//! check, so a writer bug cannot pass by parsing its own output twice).

use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use mangaflow_desktop_shell_core::handshake::{spawn_helper, HelperConfig};
use mangaflow_desktop_shell_core::logs::{
    export_logs_zip, helper_log_path, logs_dir, shell_log_path, RunLog,
};
use mangaflow_desktop_shell_core::protocol::new_token;

fn python() -> PathBuf {
    PathBuf::from(std::env::var("MANGAFLOW_DESKTOP_PYTHON").unwrap_or_else(|_| "python3".into()))
}

fn temp_user_data(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "mangaflow-desktop-logexport-{tag}-{}-{}",
        std::process::id(),
        new_token()
    ));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn python_validate_zip(archive: &Path, expected_members: &[&str]) {
    let script = r#"
import json, sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
assert z.testzip() is None, "corrupt member"
names = z.namelist()
for expected in sys.argv[2:]:
    assert expected in names, (expected, names)
manifest = json.loads(z.read("manifest.json"))
listed = {entry["name"] for entry in manifest["included"]}
for expected in sys.argv[2:]:
    if expected != "manifest.json":
        assert expected in listed, (expected, listed)
print("PYZIP_OK")
"#;
    let output = std::process::Command::new(python())
        .arg("-c")
        .arg(script)
        .arg(archive)
        .args(expected_members)
        .output()
        .expect("python3 must be available (same requirement as the protocol tests)");
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        output.status.success() && stdout.contains("PYZIP_OK"),
        "python zipfile validation failed: {} {}",
        stdout,
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn export_archives_logs_skips_escapes_and_python_validates() {
    let user_data = temp_user_data("collect");
    let token = new_token();
    let logs = logs_dir(&user_data);
    fs::create_dir_all(&logs).unwrap();

    // Shell log via RunLog + a nested subdirectory member, like per-run dirs.
    let run_log = RunLog::create(&user_data, &token).unwrap();
    run_log
        .record("spawn", &serde_json::json!({ "token": token }))
        .unwrap();
    fs::create_dir_all(logs.join("run-1")).unwrap();
    fs::write(logs.join("run-1").join("worker.log"), "worker line\n").unwrap();
    fs::write(helper_log_path(&user_data, &token), "helper stderr\n").unwrap();

    // A symlink inside the logs dir must be skipped, never followed.
    #[cfg(unix)]
    std::os::unix::fs::symlink("/etc/passwd", logs.join("evil-link")).unwrap();
    // An over-cap file must be skipped without failing the export.
    let big = fs::File::create(logs.join("big.log")).unwrap();
    big.set_len(mangaflow_desktop_shell_core::logs::EXPORT_MAX_FILE_BYTES + 1)
        .unwrap();

    let destination = std::env::temp_dir().join(format!("mfd-export-{}.zip", new_token()));
    let expected_total = fs::read(helper_log_path(&user_data, &token)).unwrap().len() as u64
        + fs::read(logs.join("run-1").join("worker.log")).unwrap().len() as u64
        + fs::read(shell_log_path(&user_data, &token)).unwrap().len() as u64;
    let report = export_logs_zip(&user_data, &destination).unwrap();

    assert_eq!(
        report.files,
        vec![
            format!("helper-{token}.stderr.log"),
            "run-1/worker.log".to_string(),
            format!("shell-{token}.log"),
        ]
    );
    let names: Vec<&str> = report.files.iter().map(|s| s.as_str()).collect();
    let skipped_names: Vec<&str> = report.skipped.iter().map(|s| s.name.as_str()).collect();
    #[cfg(unix)]
    assert!(skipped_names.contains(&"evil-link"), "{skipped_names:?}");
    assert!(skipped_names.contains(&"big.log"), "{skipped_names:?}");
    assert_eq!(report.total_bytes, expected_total);
    assert_eq!(&fs::read(&destination).unwrap()[0..2], b"PK");

    let mut expected = names.clone();
    expected.push("manifest.json");
    python_validate_zip(&destination, &expected);

    // The user-data root is untouched apart from the logs themselves.
    assert!(destination.exists());
    let _ = fs::remove_dir_all(&user_data);
    let _ = fs::remove_file(&destination);
}

#[test]
fn export_refuses_destinations_inside_user_data_root() {
    let user_data = temp_user_data("inside");
    let logs = logs_dir(&user_data);
    fs::create_dir_all(&logs).unwrap();
    fs::create_dir_all(user_data.join("runtime")).unwrap();
    // Directly inside, inside logs/, and inside the runtime subtree.
    for destination in [
        user_data.join("export.zip"),
        logs.join("export.zip"),
        user_data.join("runtime").join("x.zip"),
    ] {
        let error = export_logs_zip(&user_data, &destination).unwrap_err();
        assert!(
            matches!(
                error,
                mangaflow_desktop_shell_core::logs::ExportError::DestinationInsideUserData
            ),
            "{destination:?} -> {error}"
        );
    }
    assert_eq!(fs::read_dir(&logs).unwrap().count(), 0, "no archive may appear");
    let _ = fs::remove_dir_all(&user_data);
}

#[test]
fn handshake_writes_run_logs_that_export_contains_without_commands() {
    let user_data = temp_user_data("handshake");
    let config = HelperConfig::stub(&python(), &helper_script());
    let mut spawned = spawn_helper(&config, &user_data).expect("handshake must complete");
    let token = spawned.layout.token.clone();
    let shell_log = fs::read_to_string(shell_log_path(&user_data, &token)).unwrap();
    let events: Vec<String> = shell_log
        .lines()
        .filter_map(|line| {
            serde_json::from_str::<serde_json::Value>(line)
                .ok()?
                .get("event")?
                .as_str()
                .map(str::to_string)
        })
        .collect();
    assert_eq!(
        events,
        vec![
            "spawn".to_string(),
            "ready_verified".to_string(),
            "go_sent".to_string(),
            "healthy".to_string()
        ],
        "{shell_log}"
    );
    // Identity only: no helper arguments, script paths, or env in the log.
    assert!(!shell_log.contains("helper_script"));
    assert!(!shell_log.contains("--api-root"));
    assert!(!shell_log.contains("MANGAFLOW_DESKTOP_HELPER"));
    // Helper stderr log file exists (may be empty on a clean stub run).
    assert!(helper_log_path(&user_data, &token).exists());

    let destination = std::env::temp_dir().join(format!("mfd-export-handshake-{}.zip", new_token()));
    let report = export_logs_zip(&user_data, &destination).unwrap();
    assert!(
        report
            .files
            .iter()
            .any(|name| name == &format!("shell-{token}.log")),
        "{report:?}"
    );
    python_validate_zip(
        &destination,
        &[&format!("shell-{token}.log"), "manifest.json"],
    );

    spawned.tree.stop(Duration::from_secs(5)).unwrap();
    let _ = fs::remove_dir_all(&user_data);
    let _ = fs::remove_file(&destination);
}

fn helper_script() -> PathBuf {
    let from_env = std::env::var("MANGAFLOW_DESKTOP_HELPER").map(PathBuf::from);
    from_env.unwrap_or_else(|_| {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../sidecar/mangaflow_desktop_helper.py")
    })
}
