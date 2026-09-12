//! V02-54C integration tests: size-based rotation of the unified logs
//! directory. The session-start sweep rotates previous sessions' shell and
//! helper logs; the shell RunLog rotates in-session with its open path kept
//! writable; generations are retained up to the configured cap and flow into
//! exports like any other member.
//!
//! Oversizing uses sparse files (`set_len`): the size is metadata, so the
//! real 12 MiB default threshold is exercised without writing real bytes.

use std::fs;
use std::path::PathBuf;

use mangaflow_desktop_shell_core::logs::{
    export_logs_zip, helper_log_path, logs_dir, shell_log_path, RunLog,
    ROTATION_KEEP_GENERATIONS, ROTATION_THRESHOLD_BYTES,
};
use mangaflow_desktop_shell_core::protocol::new_token;

fn temp_user_data(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "mangaflow-desktop-logrotate-{tag}-{}-{}",
        std::process::id(),
        new_token()
    ));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn generation(logs: &PathBuf, base_name: &str, number: usize) -> PathBuf {
    logs.join(format!("{base_name}.{number}"))
}

#[test]
fn session_start_sweep_rotates_oversized_previous_session_logs() {
    let user_data = temp_user_data("sweep");
    let logs = logs_dir(&user_data);
    fs::create_dir_all(&logs).unwrap();

    let old = new_token();
    let small = new_token();
    fs::File::create(helper_log_path(&user_data, &old))
        .unwrap()
        .set_len(ROTATION_THRESHOLD_BYTES + 1)
        .unwrap();
    fs::File::create(shell_log_path(&user_data, &old))
        .unwrap()
        .set_len(ROTATION_THRESHOLD_BYTES + 1)
        .unwrap();
    fs::write(helper_log_path(&user_data, &small), "small\n").unwrap();
    fs::write(logs.join("unrelated.txt"), "keep\n").unwrap();

    // Session start (RunLog::create) sweeps the logs directory.
    let fresh = new_token();
    let run_log = RunLog::create(&user_data, &fresh).unwrap();
    run_log
        .record("spawn", &serde_json::json!({ "token": fresh }))
        .unwrap();

    // Both oversized previous-session logs moved into generation .1…
    assert!(!helper_log_path(&user_data, &old).exists());
    assert_eq!(
        fs::metadata(generation(&logs, &format!("helper-{old}.stderr.log"), 1))
            .unwrap()
            .len(),
        ROTATION_THRESHOLD_BYTES + 1
    );
    assert!(!shell_log_path(&user_data, &old).exists());
    assert!(generation(&logs, &format!("shell-{old}.log"), 1).exists());
    // …while small and unrelated files stay untouched and the new session
    // writes to its own base file.
    assert_eq!(
        fs::read_to_string(helper_log_path(&user_data, &small)).unwrap(),
        "small\n"
    );
    assert_eq!(fs::read_to_string(logs.join("unrelated.txt")).unwrap(), "keep\n");
    let active = fs::read_to_string(shell_log_path(&user_data, &fresh)).unwrap();
    assert!(active.contains("\"spawn\""));

    let _ = fs::remove_dir_all(&user_data);
}

#[test]
fn session_start_sweep_leaves_below_threshold_logs_alone() {
    // The boundary complement of the sweep test: threshold-1 must NOT
    // rotate (`rotate_file` uses `<` on the metadata length), whatever the
    // base kind — only reaching the threshold triggers rotation.
    let user_data = temp_user_data("below");
    let logs = logs_dir(&user_data);
    fs::create_dir_all(&logs).unwrap();

    let old = new_token();
    for path in [
        helper_log_path(&user_data, &old),
        shell_log_path(&user_data, &old),
    ] {
        fs::File::create(path)
            .unwrap()
            .set_len(ROTATION_THRESHOLD_BYTES - 1)
            .unwrap();
    }

    let fresh = new_token();
    RunLog::create(&user_data, &fresh).unwrap();

    assert!(helper_log_path(&user_data, &old).exists());
    assert!(shell_log_path(&user_data, &old).exists());
    assert!(!generation(&logs, &format!("helper-{old}.stderr.log"), 1).exists());
    assert!(!generation(&logs, &format!("shell-{old}.log"), 1).exists());

    let _ = fs::remove_dir_all(&user_data);
}

#[test]
fn run_log_rotates_mid_session_and_open_path_stays_writable() {
    let user_data = temp_user_data("in-session");
    let token = new_token();
    let logs = logs_dir(&user_data);
    let run_log = RunLog::create(&user_data, &token).unwrap();
    let base = shell_log_path(&user_data, &token);
    run_log.record("before_rotate", &serde_json::json!({})).unwrap();

    // Grow the active file to the threshold out-of-band, like a long session
    // would by appending milestone lines.
    fs::OpenOptions::new()
        .write(true)
        .open(&base)
        .unwrap()
        .set_len(ROTATION_THRESHOLD_BYTES)
        .unwrap();

    run_log.record("after_rotate", &serde_json::json!({})).unwrap();

    // The oversized content became generation .1; the fresh base only holds
    // the line written after the rotation.
    let generation_one = generation(&logs, &format!("shell-{token}.log"), 1);
    assert_eq!(
        fs::metadata(&generation_one).unwrap().len(),
        ROTATION_THRESHOLD_BYTES
    );
    let kept = fs::read_to_string(&generation_one).unwrap();
    assert!(kept.contains("\"before_rotate\""));
    assert!(!kept.contains("\"after_rotate\""));
    assert!(fs::metadata(&base).unwrap().len() < ROTATION_THRESHOLD_BYTES);

    // The open path still accepts writes after rotation.
    run_log.record("post_rotate", &serde_json::json!({})).unwrap();
    let active = fs::read_to_string(&base).unwrap();
    assert!(active.contains("\"after_rotate\""));
    assert!(active.contains("\"post_rotate\""));

    // Retention: no matter how often it rotates, at most KEEP generations
    // exist and the base always comes back.
    for _ in 0..(ROTATION_KEEP_GENERATIONS + 3) {
        fs::OpenOptions::new()
            .write(true)
            .open(&base)
            .unwrap()
            .set_len(ROTATION_THRESHOLD_BYTES)
            .unwrap();
        run_log.record("grow", &serde_json::json!({})).unwrap();
    }
    for number in 1..=(ROTATION_KEEP_GENERATIONS + 2) {
        let path = generation(&logs, &format!("shell-{token}.log"), number);
        assert_eq!(
            path.exists(),
            number <= ROTATION_KEEP_GENERATIONS,
            "{path:?}"
        );
    }
    assert!(fs::metadata(&base).unwrap().len() < ROTATION_THRESHOLD_BYTES);

    drop(run_log);
    let _ = fs::remove_dir_all(&user_data);
}

#[test]
fn rotated_generations_are_exported_with_the_active_log() {
    let user_data = temp_user_data("export");
    let token = new_token();
    let run_log = RunLog::create(&user_data, &token).unwrap();
    let base = shell_log_path(&user_data, &token);
    run_log.record("first", &serde_json::json!({})).unwrap();
    fs::OpenOptions::new()
        .write(true)
        .open(&base)
        .unwrap()
        .set_len(ROTATION_THRESHOLD_BYTES)
        .unwrap();
    run_log.record("second", &serde_json::json!({})).unwrap();

    let destination =
        std::env::temp_dir().join(format!("mfd-export-rotation-{}.zip", new_token()));
    let report = export_logs_zip(&user_data, &destination).unwrap();
    assert!(
        report
            .files
            .iter()
            .any(|name| name == &format!("shell-{token}.log")),
        "{report:?}"
    );
    assert!(
        report
            .files
            .iter()
            .any(|name| name == &format!("shell-{token}.log.1")),
        "{report:?}"
    );
    // The rotated generation is well under the export read cap, so nothing
    // was skipped for size.
    assert!(
        report
            .skipped
            .iter()
            .all(|entry| entry.reason != "too_large"),
        "{report:?}"
    );

    let _ = fs::remove_dir_all(&user_data);
    let _ = fs::remove_file(&destination);
}

/// Sweeping a user-data directory that has no logs/ yet (first session,
/// fresh install) must be a clean no-op — not an error, not a directory
/// creation side effect.
#[test]
fn rotate_logs_is_a_noop_without_a_logs_directory() {
    let user_data = temp_user_data("no-logs-dir");
    assert!(!logs_dir(&user_data).exists());
    mangaflow_desktop_shell_core::logs::rotate_logs(&user_data).unwrap();
    assert!(
        !logs_dir(&user_data).exists(),
        "the no-op must not create the logs directory as a side effect"
    );
    let _ = fs::remove_dir_all(&user_data);
}

/// A stray regular file parked at the logs path (crash debris, user error)
/// is the one state that breaks every future rotation: read_dir fails, so
/// the session-start sweep cannot even enumerate. The sweep stays
/// fail-soft, but the failure must SURFACE — `rotate_logs` reports it as an
/// error the caller prints to stderr, never `Ok`, and never a panic.
/// (Whether session start should additionally survive the unwritable logs
/// path is a design question for `RunLog::create`, out of scope here.)
#[test]
fn rotate_logs_reports_a_file_parked_at_the_logs_path() {
    let user_data = temp_user_data("file-at-logs-path");
    let logs = logs_dir(&user_data);
    fs::create_dir_all(&user_data).unwrap();
    fs::write(&logs, b"not a directory").unwrap();

    let error = mangaflow_desktop_shell_core::logs::rotate_logs(&user_data)
        .err()
        .expect("a file at the logs path must be reported, not swallowed");
    assert_eq!(error.kind(), std::io::ErrorKind::NotADirectory);
    // The parked file is evidence — the failed sweep must not have touched
    // (or deleted) it.
    assert!(logs.is_file(), "the failed sweep must leave the stray file");

    let _ = fs::remove_dir_all(&user_data);
}

#[test]
fn session_sweep_ignores_a_planted_logs_root_link() {
    // #610: with the logs root planted as a link, the rotation sweep must
    // no-op instead of renaming/deleting oversized pattern matches inside
    // whatever the link targets.
    let user_data = temp_user_data("sweep-root-link");
    let foreign = temp_user_data("sweep-root-link-target");
    fs::create_dir_all(&foreign).unwrap();
    let foreign_log_name = format!("shell-{}.log", new_token());
    let foreign_log = foreign.join(&foreign_log_name);
    fs::File::create(&foreign_log)
        .unwrap()
        .set_len(ROTATION_THRESHOLD_BYTES + 1)
        .unwrap();

    let logs = logs_dir(&user_data);
    #[cfg(unix)]
    std::os::unix::fs::symlink(&foreign, &logs).unwrap();
    #[cfg(windows)]
    {
        let _ = std::os::windows::fs::symlink_dir(&foreign, &logs);
    }
    if !logs
        .symlink_metadata()
        .map(|meta| meta.is_symlink())
        .unwrap_or(false)
    {
        eprintln!("root link creation not permitted; skipping the root-link assertions");
        let _ = fs::remove_dir_all(&user_data);
        let _ = fs::remove_dir_all(&foreign);
        return;
    }

    mangaflow_desktop_shell_core::logs::rotate_logs(&user_data).unwrap();

    // The foreign tree is untouched: no generation shift, no size change.
    assert_eq!(
        fs::metadata(&foreign_log).unwrap().len(),
        ROTATION_THRESHOLD_BYTES + 1
    );
    assert!(
        !foreign.join(format!("{foreign_log_name}.1")).exists(),
        "the foreign tree must not gain rotation generations"
    );

    let _ = fs::remove_dir_all(&user_data);
    let _ = fs::remove_dir_all(&foreign);
}
