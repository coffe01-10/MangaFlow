//! V02-54B integration tests: unified log layout + path-safe ZIP export.
//!
//! The archive is validated twice: structurally by the test itself and
//! externally by `python3 -m zipfile`/`zipfile` (independent CRC + layout
//! check, so a writer bug cannot pass by parsing its own output twice).

mod common;

use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use mangaflow_desktop_shell_core::handshake::{spawn_helper, HelperConfig};
use mangaflow_desktop_shell_core::logs::{
    export_logs_zip, export_logs_zip_overwrite, helper_log_path, logs_dir, shell_log_path, RunLog,
};
use mangaflow_desktop_shell_core::protocol::new_token;

use common::python;

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
        .expect("a python interpreter must be available (same requirement as the protocol tests)");
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

#[cfg(unix)]
#[test]
fn export_skips_an_unreadable_subdirectory_instead_of_aborting() {
    // #241-5b covers unreadable files; a locked subdirectory (read_dir
    // fails) must join the same skip-and-report policy instead of aborting
    // the whole export with a bare Io error.
    let user_data = temp_user_data("lockedsub");
    let logs = logs_dir(&user_data);
    fs::create_dir_all(&logs).unwrap();
    fs::write(logs.join("keeper.log"), "kept line\n").unwrap();
    let locked = logs.join("run-locked");
    fs::create_dir_all(&locked).unwrap();
    fs::write(locked.join("secret.log"), "locked line\n").unwrap();

    let mut perms = fs::metadata(&locked).unwrap().permissions();
    use std::os::unix::fs::PermissionsExt;
    perms.set_mode(0o000);
    fs::set_permissions(&locked, perms).unwrap();
    if unsafe { libc::geteuid() } == 0 {
        // Root reads through the permission bit mask; the in-crate tests
        // skip the same way. The mode is restored above, before this
        // skip-return, so the cleanup below stays safe.
        let mut restore = fs::metadata(&locked).unwrap().permissions();
        restore.set_mode(0o755);
        fs::set_permissions(&locked, restore).unwrap();
        eprintln!("running as root; the locked-subdirectory case is skipped");
        let _ = fs::remove_dir_all(&user_data);
        return;
    }

    let destination = std::env::temp_dir().join(format!("mfd-export-lockedsub-{}.zip", new_token()));
    let report = export_logs_zip(&user_data, &destination).unwrap();
    assert_eq!(report.files, vec!["keeper.log".to_string()], "{report:?}");
    assert!(
        report
            .skipped
            .iter()
            .any(|entry| entry.name == "run-locked" && entry.reason.starts_with("readdir:")),
        "{report:?}"
    );

    // Restore access so the cleanup can actually remove the tree.
    let mut perms = fs::metadata(&locked).unwrap().permissions();
    perms.set_mode(0o755);
    fs::set_permissions(&locked, perms).unwrap();
    let _ = fs::remove_dir_all(&user_data);
    let _ = fs::remove_file(&destination);
}

#[test]
fn export_includes_a_member_at_exactly_the_per_member_cap() {
    // The cap is inclusive (`>` skips): a member of exactly
    // EXPORT_MAX_FILE_BYTES must be archived, and the bounded reader
    // (take at the cap) must still deliver it whole.
    let user_data = temp_user_data("exactcap");
    let logs = logs_dir(&user_data);
    fs::create_dir_all(&logs).unwrap();
    let capped = fs::File::create(logs.join("exact-cap.log")).unwrap();
    capped
        .set_len(mangaflow_desktop_shell_core::logs::EXPORT_MAX_FILE_BYTES)
        .unwrap();

    let destination = std::env::temp_dir().join(format!("mfd-export-exactcap-{}.zip", new_token()));
    let report = export_logs_zip(&user_data, &destination).unwrap();
    assert_eq!(report.files, vec!["exact-cap.log".to_string()], "{report:?}");
    assert_eq!(
        report.total_bytes,
        mangaflow_desktop_shell_core::logs::EXPORT_MAX_FILE_BYTES
    );

    let _ = fs::remove_dir_all(&user_data);
    let _ = fs::remove_file(&destination);
}

#[test]
fn export_overwrite_replaces_and_never_orphans_the_pending_sibling() {
    // #149: the confirmed-overwrite path replaces an existing archive. On
    // POSIX the placement is an atomic rename; on failure paths the pending
    // sibling this export created must never be left orphaned next to the
    // (possibly removed) destination.
    let user_data = temp_user_data("overwrite");
    let logs = logs_dir(&user_data);
    fs::create_dir_all(&logs).unwrap();
    fs::write(logs.join("first.log"), "first line\n").unwrap();

    let destination =
        std::env::temp_dir().join(format!("mfd-overwrite-{}.zip", new_token()));
    export_logs_zip(&user_data, &destination).unwrap();

    fs::write(logs.join("second.log"), "second line\n").unwrap();
    let second = export_logs_zip_overwrite(&user_data, &destination).unwrap();
    assert!(
        second.files.contains(&"second.log".to_string()),
        "{second:?}"
    );
    assert_eq!(&fs::read(&destination).unwrap()[0..2], b"PK");
    let pending = destination.with_file_name(format!(
        "{}.pending",
        destination.file_name().unwrap().to_string_lossy()
    ));
    assert!(!pending.exists(), "no pending sibling may survive a success");

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

/// Red team 2026-09-09 (#310): `collect_members` recursion is depth-capped.
/// A same-user planted tree nested beyond the cap degrades to a reported
/// skip ("max_depth") instead of a stack overflow; shallow members still
/// archive normally.
#[test]
fn export_skips_members_beyond_the_recursion_depth_cap() {
    let user_data = temp_user_data("deep");
    let logs = logs_dir(&user_data);
    fs::create_dir_all(&logs).unwrap();
    let mut dir = logs.clone();
    for _ in 0..40 {
        dir = dir.join("n");
    }
    fs::create_dir_all(&dir).unwrap();
    fs::write(dir.join("deep.log"), "deep\n").unwrap();
    fs::write(logs.join("shallow.log"), "shallow\n").unwrap();

    let destination = std::env::temp_dir().join(format!("mfd-deep-{}.zip", new_token()));
    let report = export_logs_zip(&user_data, &destination).unwrap();

    assert!(
        report.files.iter().any(|name| name == "shallow.log"),
        "{report:?}"
    );
    assert!(
        report
            .skipped
            .iter()
            .any(|entry| entry.reason == "max_depth"),
        "{report:?}"
    );
    assert!(
        report.files.iter().all(|name| !name.ends_with("deep.log")),
        "nothing beyond the cap may be archived: {report:?}"
    );

    let _ = fs::remove_dir_all(&user_data);
    let _ = fs::remove_file(&destination);
}

/// Rotation staging debris (`.rotating` / `.rotating-oldest` siblings of a
/// ROTATABLE base — a crash or rollback caught mid-rotation) must be
/// skipped with the rotation_staging reason: archiving it would duplicate
/// the oldest generation under a second name. The polarity control: a user
/// file that merely ENDS in ".rotating" but is not a rotatable base
/// archives like any other member — dropping the is_rotatable_base_name
/// gate would silently exclude user files from exports.
#[test]
fn export_skips_rotation_staging_debris_but_not_lookalike_user_files() {
    let user_data = temp_user_data("staging-debris");
    let token = new_token();
    let logs = logs_dir(&user_data);
    fs::create_dir_all(&logs).unwrap();
    let run_log = RunLog::create(&user_data, &token).unwrap();
    run_log
        .record("spawn", &serde_json::json!({ "token": token }))
        .unwrap();

    // Debris of the real base: both staging suffixes.
    fs::write(logs.join(format!("shell-{token}.log.rotating")), "mid-shift\n").unwrap();
    fs::write(
        logs.join(format!("shell-{token}.log.rotating-oldest")),
        "staged oldest\n",
    )
    .unwrap();
    // Lookalike: ends in .rotating but is NOT a rotatable base name.
    fs::write(logs.join("notes.rotating"), "user content\n").unwrap();

    let destination = std::env::temp_dir().join(format!("mfd-export-{}.zip", new_token()));
    let report = export_logs_zip(&user_data, &destination).unwrap();

    // read_dir order is filesystem-dependent; compare as sorted sets.
    let mut skipped: Vec<(String, &str)> = report
        .skipped
        .iter()
        .map(|s| (s.name.clone(), s.reason.as_str()))
        .collect();
    skipped.sort_unstable();
    let expected = vec![
        (format!("shell-{token}.log.rotating"), "rotation_staging"),
        (format!("shell-{token}.log.rotating-oldest"), "rotation_staging"),
    ];
    assert_eq!(skipped, expected, "{skipped:?}");
    // The lookalike user file archives normally, alongside the run log.
    // (read_dir order is filesystem-dependent; compare as sorted sets.)
    let mut files = report.files.clone();
    files.sort_unstable();
    let mut expected_files = vec![format!("shell-{token}.log"), "notes.rotating".to_string()];
    expected_files.sort_unstable();
    assert_eq!(files, expected_files, "{report:?}");

    let _ = fs::remove_dir_all(&user_data);
    let _ = fs::remove_file(&destination);
}

/// A FIFO planted in the logs directory must be skipped with the
/// not_a_regular_file reason — and, critically, the exporter must never
/// OPEN it: a plain `fs::read` on a pipe blocks until a writer appears,
/// so a regression to read-without-type-check would hang every export
/// until someone writes to the pipe. The export runs on a thread with a
/// bounded join so a regression surfaces as a test failure, not a hang.
/// Unix-only: mkfifo is a libc call.
#[test]
#[cfg(unix)]
fn export_skips_a_fifo_without_opening_it() {
    use std::ffi::CString;
    use std::sync::mpsc;

    let user_data = temp_user_data("fifo");
    let token = new_token();
    let logs = logs_dir(&user_data);
    fs::create_dir_all(&logs).unwrap();
    let run_log = RunLog::create(&user_data, &token).unwrap();
    run_log
        .record("spawn", &serde_json::json!({ "token": token }))
        .unwrap();

    let fifo = logs.join("planted-pipe");
    let cpath = CString::new(fifo.as_os_str().as_encoded_bytes()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(cpath.as_ptr(), 0o644) }, 0);

    let (sender, receiver) = mpsc::channel();
    let thread_user_data = user_data.clone();
    std::thread::spawn(move || {
        let destination =
            std::env::temp_dir().join(format!("mfd-export-{}.zip", new_token()));
        let result = export_logs_zip(&thread_user_data, &destination)
            .map(|report| (report, destination));
        let _ = sender.send(result);
    });
    let outcome = receiver
        .recv_timeout(std::time::Duration::from_secs(30))
        .expect("export must finish — a hang means the exporter opened the FIFO");
    let (report, destination) = outcome.expect("export succeeds");

    let skipped: Vec<(&str, &str)> = report
        .skipped
        .iter()
        .map(|s| (s.name.as_str(), s.reason.as_str()))
        .collect();
    assert_eq!(skipped, vec![("planted-pipe", "not_a_regular_file")], "{skipped:?}");
    // The run log alone is archived; the FIFO contributed nothing.
    let mut files = report.files.clone();
    files.sort_unstable();
    assert_eq!(files, vec![format!("shell-{token}.log")], "{report:?}");

    let _ = fs::remove_dir_all(&user_data);
    let _ = fs::remove_file(&destination);
}

/// A fresh install has a logs directory with nothing rotatable in it; the
/// export must still succeed as a manifest-only archive — `included` empty
/// and `skipped` empty — rather than failing or omitting the manifest. The
/// manifest must never claim contents the archive does not have; here it
/// claims none.
#[test]
fn export_of_an_empty_logs_dir_yields_a_manifest_only_archive() {
    let user_data = temp_user_data("empty-logs");
    let logs = logs_dir(&user_data);
    fs::create_dir_all(&logs).unwrap();

    let destination = std::env::temp_dir().join(format!("mfd-export-{}.zip", new_token()));
    let report = export_logs_zip(&user_data, &destination).unwrap();

    assert!(report.files.is_empty(), "{report:?}");
    assert!(report.skipped.is_empty(), "{report:?}");
    assert_eq!(report.total_bytes, 0, "{report:?}");
    // The archive is exactly the manifest member, and the manifest's
    // included list is empty (validated by python's zipfile, the same
    // external-reader discipline as the collect test above).
    let archive = fs::read(&destination).unwrap();
    assert_eq!(&archive[0..2], b"PK");
    let output = std::process::Command::new(python())
        .arg("-c")
        .arg(
            "import json, sys, zipfile\n\
             manifest = json.load(zipfile.ZipFile(sys.argv[1]).open('manifest.json'))\n\
             names = zipfile.ZipFile(sys.argv[1]).namelist()\n\
             assert names == ['manifest.json'], names\n\
             assert manifest['included'] == [], manifest\n\
             print('EMPTY_OK')",
        )
        .arg(&destination)
        .output()
        .expect("python zipfile validation runs");
    assert!(
        output.status.success()
            && String::from_utf8_lossy(&output.stdout).contains("EMPTY_OK"),
        "python zipfile validation failed: {} {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let _ = fs::remove_dir_all(&user_data);
    let _ = fs::remove_file(&destination);
}
