//! Protocol constants, payload types, and verification rules shared with the
//! Python helper (`sidecar/mangaflow_desktop_helper.py`). The journal carries
//! identity only — never commands, environment, or secrets.

use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

pub const READY_PREFIX: &str = "MANGAFLOW_READY ";
pub const GO_PREFIX: &str = "MANGAFLOW_GO ";
pub const RUNTIME_DIR_PREFIX: &str = "mangaflow-desktop-";
pub const JOURNAL_NAME: &str = "owner.json";
pub const PROTOCOL_VERSION: u64 = 1;
pub const HEALTH_PATH: &str = "/api/v1/health";

#[derive(Debug, Clone)]
pub struct ReadyPayload {
    pub token: String,
    pub pid: u32,
    pub api_origin: String,
    pub port: u16,
    /// Plan B (W-15): loopback origin of the helper-spawned Next standalone
    /// web server, present only when the helper manages one. The WebView
    /// loads this instead of the static export.
    pub web_origin: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum VerifyError {
    BadLine,
    BadJson,
    TokenMismatch,
    PidMismatch,
    OriginNotLoopback,
    JournalMissing,
    JournalMismatch(&'static str),
    StartTimeMismatch,
}

fn is_loopback_origin(origin: &str) -> bool {
    // ADR D9: the API must bind the loopback adapter only.
    origin
        .strip_prefix("http://127.0.0.1:")
        .and_then(|port| port.parse::<u16>().ok())
        .is_some()
}

/// Parse and verify the `MANGAFLOW_READY {json}` line against expectations.
pub fn verify_ready_line(
    line: &str,
    token: &str,
    expected_pid: u32,
) -> Result<ReadyPayload, VerifyError> {
    verify_ready_line_where(line, token, |pid| pid == expected_pid)
}

/// [`verify_ready_line`] with a caller-supplied PID predicate.
///
/// The default is exact equality with the spawned child. On Windows the
/// predicate also accepts a PID that lives inside the shell's Job Object:
/// CPython 3.12 venvs use a launcher-style `python.exe` that spawns the real
/// interpreter as a child process, so the helper announcing readiness is a
/// grandchild while the shell only knows the launcher PID. Job membership
/// preserves the identity invariant (the announcer must belong to the tree
/// this shell owns and can kill); the secret token still gates spoofing.
pub fn verify_ready_line_where<P>(
    line: &str,
    token: &str,
    pid_owned: P,
) -> Result<ReadyPayload, VerifyError>
where
    P: Fn(u32) -> bool,
{
    let payload = line
        .strip_prefix(READY_PREFIX)
        .ok_or(VerifyError::BadLine)?;
    let value: serde_json::Value =
        serde_json::from_str(payload).map_err(|_| VerifyError::BadJson)?;
    if value["token"].as_str() != Some(token) {
        return Err(VerifyError::TokenMismatch);
    }
    let pid = value["pid"].as_u64().ok_or(VerifyError::BadJson)? as u32;
    if !pid_owned(pid) {
        return Err(VerifyError::PidMismatch);
    }
    let origin = value["api_origin"]
        .as_str()
        .ok_or(VerifyError::BadJson)?
        .to_string();
    if !is_loopback_origin(&origin) {
        return Err(VerifyError::OriginNotLoopback);
    }
    let port: u16 = origin
        .rsplit(':')
        .next()
        .and_then(|port| port.parse().ok())
        .ok_or(VerifyError::BadJson)?;
    let web_origin = match value["web_origin"].as_str() {
        Some(origin) => {
            if !is_loopback_origin(origin) {
                return Err(VerifyError::OriginNotLoopback);
            }
            Some(origin.to_string())
        }
        None => None,
    };
    Ok(ReadyPayload {
        token: token.to_string(),
        pid,
        api_origin: origin,
        port,
        web_origin,
    })
}

/// Verify the readiness journal the helper published (identity fields only).
pub fn verify_journal(journal: &Path, ready: &ReadyPayload) -> Result<(), VerifyError> {
    let text = std::fs::read_to_string(journal).map_err(|_| VerifyError::JournalMissing)?;
    let value: serde_json::Value =
        serde_json::from_str(&text).map_err(|_| VerifyError::JournalMismatch("unparsable"))?;
    if value["version"].as_u64() != Some(PROTOCOL_VERSION) {
        return Err(VerifyError::JournalMismatch("version"));
    }
    if value["token"].as_str() != Some(ready.token.as_str()) {
        return Err(VerifyError::JournalMismatch("token"));
    }
    if value["state"].as_str() != Some("ready") {
        return Err(VerifyError::JournalMismatch("state"));
    }
    if value["pid"].as_u64() != Some(ready.pid as u64) {
        return Err(VerifyError::JournalMismatch("pid"));
    }
    if value["api_origin"].as_str() != Some(ready.api_origin.as_str()) {
        return Err(VerifyError::JournalMismatch("api_origin"));
    }
    match (&ready.web_origin, value["web_origin"].as_str()) {
        (None, None) => {}
        (Some(ready_origin), Some(journal_origin)) => {
            if ready_origin != journal_origin {
                return Err(VerifyError::JournalMismatch("web_origin"));
            }
        }
        _ => return Err(VerifyError::JournalMismatch("web_origin")),
    }
    #[cfg(unix)]
    {
        let announced = value["pid_starttime"].as_u64();
        let actual = crate::ownership::pid_starttime(ready.pid);
        // On Linux the journal anchors PID identity to /proc start time, the
        // equivalent of the Windows creation-time check in owned_processes.
        if announced.is_some() && announced != actual {
            return Err(VerifyError::StartTimeMismatch);
        }
    }
    Ok(())
}

/// Shell-owned runtime layout under the user data directory.
#[derive(Debug, Clone)]
pub struct RuntimeLayout {
    pub user_data: PathBuf,
    pub token: String,
}

impl RuntimeLayout {
    /// Create a fresh runtime directory with a 32-hex owner token, mirroring
    /// the canonical-path checks of `scripts/owned_processes.py`, and write
    /// the shell-side ownership journal BEFORE anything is spawned.
    /// `owned_processes.py` makes ownership durable before resuming the
    /// suspended child; this shell writes its record even earlier — before
    /// the child process exists at all. The helper overwrites this journal
    /// atomically with the readiness state once it publishes readiness.
    pub fn create(user_data: &Path) -> std::io::Result<RuntimeLayout> {
        let token = new_token();
        let runtime = user_data
            .join("runtime")
            .join(format!("{RUNTIME_DIR_PREFIX}{token}"));
        std::fs::create_dir_all(&runtime)?;
        let resolved = runtime.canonicalize()?;
        let expected_name = std::ffi::OsString::from(format!("{RUNTIME_DIR_PREFIX}{token}"));
        if resolved.file_name() != Some(expected_name.as_os_str()) {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "runtime path/ownership mismatch",
            ));
        }
        let layout = RuntimeLayout {
            user_data: user_data.to_path_buf(),
            token,
        };
        write_journal_atomic(
            &layout.journal_path(),
            &serde_json::json!({
                "version": PROTOCOL_VERSION,
                "token": layout.token,
                "state": "created",
                "shell_pid": std::process::id(),
                "created_at": unix_now(),
            }),
        )?;
        Ok(layout)
    }

    pub fn runtime_dir(&self) -> PathBuf {
        self.user_data
            .join("runtime")
            .join(format!("{RUNTIME_DIR_PREFIX}{}", self.token))
    }

    pub fn journal_path(&self) -> PathBuf {
        self.runtime_dir().join(JOURNAL_NAME)
    }

    /// Record the owner-side terminal state after the tree has been stopped.
    pub fn mark_stopped(&self, exit_code: Option<i32>) -> std::io::Result<()> {
        let journal = self.journal_path();
        let mut value: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(&journal).unwrap_or_else(|_| "{}".into()),
        )
        .unwrap_or_else(|_| serde_json::json!({}));
        value["state"] = "stopped".into();
        value["stopped_at"] = unix_now().into();
        if let Some(code) = exit_code {
            value["exit_code"] = code.into();
        }
        write_journal_atomic(&journal, &value)
    }
}

/// Atomic journal write (pending file + rename, same shape as the helper and
/// `owned_processes.py`). Identity fields only — never commands, env, secrets.
fn write_journal_atomic(journal: &Path, record: &serde_json::Value) -> std::io::Result<()> {
    let pending = journal.with_file_name(format!(
        "{}.pending",
        journal.file_name().unwrap_or_default().to_string_lossy()
    ));
    std::fs::write(&pending, serde_json::to_string(record).unwrap())?;
    std::fs::rename(&pending, journal)
}

/// Session-start sweep of stale runtime directories (#264).
///
/// Every session creates a fresh `runtime/mangaflow-desktop-<token>/` that no
/// production path ever deletes, so the directory accumulates for the
/// install's lifetime and stale journals blur operator forensics. Mirroring
/// `rotate_logs`' placement and contract, this sweep runs from
/// [`crate::logs::RunLog::create`] while no session owns those files and is
/// best-effort: a failure is reported to stderr and never blocks the session
/// start. A runtime directory is deleted only when BOTH hold:
///
/// 1. its journal carries a terminal state ("stopped" — the shell's
///    `mark_stopped`; "failed" — the helper's startup failure), and
/// 2. the journal's mtime (the terminal write) is older than the grace
///    window, so a just-finished session stays inspectable.
///
/// "created"/"ready" directories are NEVER touched — the native-host (WPF)
/// leg shares this layout without a single-instance mutex, so a non-terminal
/// directory may belong to a live session. Foreign names (not
/// `mangaflow-desktop-<32 hex>`), symlinks/junctions planted at a candidate
/// name, unparsable journals, and anything that does not canonically resolve
/// inside the runtime root are all left untouched.
pub const RUNTIME_SWEEP_GRACE_SECONDS: u64 = 24 * 60 * 60;

/// Whether a runtime-directory base name is one this shell may sweep.
fn is_runtime_dir_name(name: &str) -> bool {
    name.strip_prefix(RUNTIME_DIR_PREFIX).is_some_and(|token| {
        token.len() == 32
            && token
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}

/// [`sweep_runtime_dirs`] with an explicit grace window (tests use 0 /
/// `u64::MAX` instead of rewriting mtimes).
pub fn sweep_runtime_dirs_with(user_data: &Path, grace_seconds: u64) -> std::io::Result<()> {
    let runtime = user_data.join("runtime");
    let Ok(runtime_canonical) = runtime.canonicalize() else {
        return Ok(()); // no runtime directory yet — nothing to sweep
    };
    for entry in std::fs::read_dir(&runtime)? {
        let Ok(entry) = entry else { continue };
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.contains('/') || name.contains('\\') || !is_runtime_dir_name(&name) {
            continue;
        }
        let dir = entry.path();
        // Never remove through a planted link: a symlink/junction at a
        // candidate name is foreign media, not a session directory.
        match entry.file_type() {
            Ok(file_type) if file_type.is_dir() => {}
            _ => continue,
        }
        match dir.canonicalize() {
            Ok(canonical) if canonical.starts_with(&runtime_canonical) => {}
            _ => continue,
        }
        let journal = dir.join(JOURNAL_NAME);
        let Ok(text) = std::fs::read_to_string(&journal) else {
            continue; // no readable journal — conservative: keep the directory
        };
        let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) else {
            continue; // unparsable journal — keep
        };
        let terminal = matches!(value["state"].as_str(), Some("stopped") | Some("failed"));
        if !terminal {
            continue; // created/ready/unknown may belong to a live session
        }
        let Ok(modified) = std::fs::metadata(&journal).and_then(|meta| meta.modified()) else {
            continue;
        };
        let Ok(age) = SystemTime::now().duration_since(modified) else {
            continue;
        };
        if age.as_secs() < grace_seconds {
            continue;
        }
        if let Err(error) = std::fs::remove_dir_all(&dir) {
            eprintln!(
                "mangaflow-desktop: stale runtime sweep failed for {name}: {error}"
            );
        }
    }
    Ok(())
}

/// Session-start sweep with the production 24h grace window.
pub fn sweep_runtime_dirs(user_data: &Path) -> std::io::Result<()> {
    sweep_runtime_dirs_with(user_data, RUNTIME_SWEEP_GRACE_SECONDS)
}

pub fn new_token() -> String {
    // 128 bits from the OS CSPRNG; format mirrors owned_processes (32 hex).
    let mut bytes = [0u8; 16];
    fill_random(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn fill_random(buffer: &mut [u8]) {
    #[cfg(unix)]
    {
        use std::io::Read;
        let mut file = std::fs::File::open("/dev/urandom").expect("open /dev/urandom");
        file.read_exact(buffer).expect("read /dev/urandom");
    }
    #[cfg(windows)]
    {
        // Win32 CSPRNG via the CNG system-preferred provider; runtime behavior
        // is exercised only on Windows (NOT RUN in the Linux sandbox).
        use windows::Win32::Security::Cryptography::{
            BCryptGenRandom, BCRYPT_USE_SYSTEM_PREFERRED_RNG,
        };
        unsafe {
            let status = BCryptGenRandom(None, buffer, BCRYPT_USE_SYSTEM_PREFERRED_RNG);
            if status.0 != 0 {
                panic!("BCryptGenRandom failed with NTSTATUS {}", status.0);
            }
        }
    }
}

pub fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

#[cfg(test)]
mod tests {
    use super::*;

    const TOKEN: &str = "0123456789abcdef0123456789abcdef";

    #[test]
    fn accepts_wellformed_ready_line() {
        let line = format!(
            "{READY_PREFIX}{{\"token\":\"{TOKEN}\",\"pid\":4242,\"api_origin\":\"http://127.0.0.1:39001\"}}"
        );
        let payload = verify_ready_line(&line, TOKEN, 4242).unwrap();
        assert_eq!(payload.port, 39001);
        assert_eq!(payload.api_origin, "http://127.0.0.1:39001");
        assert!(payload.web_origin.is_none());
    }

    #[test]
    fn accepts_web_origin_and_validates_it_is_loopback() {
        let line = format!(
            "{READY_PREFIX}{{\"token\":\"{TOKEN}\",\"pid\":4242,\"api_origin\":\"http://127.0.0.1:39001\",\"web_origin\":\"http://127.0.0.1:39002\"}}"
        );
        let payload = verify_ready_line(&line, TOKEN, 4242).unwrap();
        assert_eq!(payload.web_origin.as_deref(), Some("http://127.0.0.1:39002"));

        let hostile = format!(
            "{READY_PREFIX}{{\"token\":\"{TOKEN}\",\"pid\":4242,\"api_origin\":\"http://127.0.0.1:39001\",\"web_origin\":\"http://10.0.0.9:39002\"}}"
        );
        assert!(matches!(
            verify_ready_line(&hostile, TOKEN, 4242),
            Err(VerifyError::OriginNotLoopback)
        ));

        // Journal must carry exactly the announced web origin: missing when
        // announced, present when absent, and a mismatched value all fail.
        let dir = std::env::temp_dir().join(format!("mfd-webo-{}-{}", std::process::id(), new_token()));
        std::fs::create_dir_all(&dir).unwrap();
        let journal = dir.join(JOURNAL_NAME);
        let payload = verify_ready_line(&line, TOKEN, 4242).unwrap();
        let good = serde_json::json!({
            "version": PROTOCOL_VERSION, "token": TOKEN, "state": "ready",
            "pid": 4242, "api_origin": "http://127.0.0.1:39001",
            "web_origin": "http://127.0.0.1:39002",
        });
        std::fs::write(&journal, good.to_string()).unwrap();
        assert!(verify_journal(&journal, &payload).is_ok());
        std::fs::write(&journal, serde_json::json!({
            "version": PROTOCOL_VERSION, "token": TOKEN, "state": "ready",
            "pid": 4242, "api_origin": "http://127.0.0.1:39001",
        }).to_string()).unwrap();
        assert!(matches!(
            verify_journal(&journal, &payload),
            Err(VerifyError::JournalMismatch("web_origin"))
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_wrong_token_or_pid() {
        let line = format!(
            "{READY_PREFIX}{{\"token\":\"{TOKEN}\",\"pid\":4242,\"api_origin\":\"http://127.0.0.1:39001\"}}"
        );
        assert!(matches!(
            verify_ready_line(&line, "ffffffffffffffffffffffffffffffff", 4242),
            Err(VerifyError::TokenMismatch)
        ));
        assert!(matches!(
            verify_ready_line(&line, TOKEN, 1),
            Err(VerifyError::PidMismatch)
        ));
    }

    #[test]
    fn rejects_non_loopback_origin() {
        let line = format!(
            "{READY_PREFIX}{{\"token\":\"{TOKEN}\",\"pid\":4242,\"api_origin\":\"http://10.1.2.3:8000\"}}"
        );
        assert!(matches!(
            verify_ready_line(&line, TOKEN, 4242),
            Err(VerifyError::OriginNotLoopback)
        ));
    }

    #[test]
    fn rejects_garbage_lines() {
        assert!(matches!(
            verify_ready_line("hello", TOKEN, 1),
            Err(VerifyError::BadLine)
        ));
        assert!(matches!(
            verify_ready_line(&format!("{READY_PREFIX}not-json"), TOKEN, 1),
            Err(VerifyError::BadJson)
        ));
    }

    #[test]
    fn runtime_layout_journals_ownership_before_any_spawn() {
        let dir = std::env::temp_dir().join(format!(
            "mangaflow-desktop-journal-test-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let layout = RuntimeLayout::create(&dir).unwrap();

        let value: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(layout.journal_path()).unwrap())
                .unwrap();
        // The shell proves it owns the runtime directory before any child
        // process exists (owned_processes writes its durable record before
        // resuming; the shell writes it before even creating the process).
        assert_eq!(value["state"], "created");
        assert_eq!(value["token"], layout.token.as_str());
        assert_eq!(value["version"], PROTOCOL_VERSION);
        assert_eq!(value["shell_pid"], serde_json::json!(std::process::id()));
        // Identity fields only.
        assert!(value.get("command").is_none() && value.get("env").is_none());

        let _ = std::fs::remove_dir_all(&dir);
    }

    /// #264 sweep fixtures: build a runtime directory with the given journal.
    fn runtime_fixture(user_data: &Path, token: &str, journal: &str) -> PathBuf {
        let dir = user_data
            .join("runtime")
            .join(format!("{RUNTIME_DIR_PREFIX}{token}"));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(JOURNAL_NAME), journal).unwrap();
        dir
    }

    #[test]
    fn sweep_removes_only_stale_terminal_runtime_dirs() {
        let user_data = std::env::temp_dir().join(format!(
            "mangaflow-desktop-rtsweep-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&user_data);
        std::fs::create_dir_all(&user_data).unwrap();
        let stopped = runtime_fixture(
            &user_data,
            &"1".repeat(32),
            &serde_json::json!({"version": 1, "token": "1".repeat(32), "state": "stopped"}).to_string(),
        );
        let failed = runtime_fixture(
            &user_data,
            &"2".repeat(32),
            &serde_json::json!({"version": 1, "token": "2".repeat(32), "state": "failed"}).to_string(),
        );
        let ready = runtime_fixture(
            &user_data,
            &"3".repeat(32),
            &serde_json::json!({"version": 1, "token": "3".repeat(32), "state": "ready"}).to_string(),
        );
        let created = runtime_fixture(
            &user_data,
            &"4".repeat(32),
            &serde_json::json!({"version": 1, "token": "4".repeat(32), "state": "created"}).to_string(),
        );
        let unparsable = runtime_fixture(&user_data, &"5".repeat(32), "not json");
        let foreign = {
            let dir = user_data.join("runtime").join("foreign-dir");
            std::fs::create_dir_all(&dir).unwrap();
            dir
        };
        let short_token = {
            let dir = user_data
                .join("runtime")
                .join(format!("{RUNTIME_DIR_PREFIX}{}", "a".repeat(8)));
            std::fs::create_dir_all(&dir).unwrap();
            dir
        };

        // grace = 0: everything terminal counts as stale and is removed;
        // non-terminal, unparsable, and foreign names survive.
        sweep_runtime_dirs_with(&user_data, 0).unwrap();
        assert!(!stopped.exists());
        assert!(!failed.exists());
        assert!(ready.exists());
        assert!(created.exists());
        assert!(unparsable.exists());
        assert!(foreign.exists());
        assert!(short_token.exists());

        // A huge grace keeps even terminal directories (fresh sessions stay
        // inspectable).
        let stopped2 = runtime_fixture(
            &user_data,
            &"6".repeat(32),
            &serde_json::json!({"version": 1, "token": "6".repeat(32), "state": "stopped"}).to_string(),
        );
        sweep_runtime_dirs_with(&user_data, u64::MAX).unwrap();
        assert!(stopped2.exists());

        let _ = std::fs::remove_dir_all(&user_data);
    }

    /// A junction/symlink planted at a runtime-directory name is never
    /// removed through (Unix: real symlink; Windows: skipped entry type).
    #[test]
    fn sweep_never_removes_through_planted_links() {
        let user_data = std::env::temp_dir().join(format!(
            "mangaflow-desktop-rtlink-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&user_data);
        std::fs::create_dir_all(&user_data).unwrap();
        let outside = std::env::temp_dir().join(format!(
            "mangaflow-desktop-rtlink-out-{}",
            new_token()
        ));
        std::fs::create_dir_all(&outside).unwrap();
        std::fs::write(
            outside.join(JOURNAL_NAME),
            serde_json::json!({"version": 1, "token": "7".repeat(32), "state": "stopped"}).to_string(),
        )
        .unwrap();
        #[cfg(unix)]
        {
            std::fs::create_dir_all(user_data.join("runtime")).unwrap();
            std::os::unix::fs::symlink(
                &outside,
                user_data
                    .join("runtime")
                    .join(format!("{RUNTIME_DIR_PREFIX}{}", "7".repeat(32))),
            )
            .unwrap();
            sweep_runtime_dirs_with(&user_data, 0).unwrap();
            // The link entry is not a dir from read_dir's file_type: skipped,
            // and the target directory survives untouched.
            assert!(outside.join(JOURNAL_NAME).exists());
        }
        let _ = std::fs::remove_dir_all(&user_data);
        let _ = std::fs::remove_dir_all(&outside);
    }
}
