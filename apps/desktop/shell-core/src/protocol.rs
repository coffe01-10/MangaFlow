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

/// Human-readable failure reasons for the startup protocol's verification
/// steps; every variant names the step that failed so log lines localize
/// without extra context.
impl std::fmt::Display for VerifyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            VerifyError::BadLine => write!(f, "READY 行前缀不正确"),
            VerifyError::BadJson => write!(f, "READY 行不是合法 JSON（或字段越界）"),
            VerifyError::TokenMismatch => write!(f, "READY 行 token 与本壳不匹配"),
            VerifyError::PidMismatch => write!(f, "READY 宣布的 pid 不属于本壳"),
            VerifyError::OriginNotLoopback => write!(f, "宣布的 origin 不是回环地址"),
            VerifyError::JournalMissing => write!(f, "ownership journal 不存在或不可读"),
            VerifyError::JournalTooLarge => write!(f, "ownership journal 超过读取上限"),
            VerifyError::JournalMismatch("non-utf8") => {
                write!(f, "ownership journal 不是 UTF-8 文本")
            }
            VerifyError::JournalMismatch(field) => {
                write!(f, "ownership journal 字段不匹配：{field}")
            }
            VerifyError::StartTimeMismatch => write!(f, "journal 的进程启动时间与 /proc 不符"),
        }
    }
}

impl std::error::Error for VerifyError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum VerifyError {
    BadLine,
    BadJson,
    TokenMismatch,
    PidMismatch,
    OriginNotLoopback,
    JournalMissing,
    /// The journal read exceeded [`JOURNAL_MAX_BYTES`] — identity fields are
    /// a few hundred bytes, so an oversized file is malformed by definition.
    JournalTooLarge,
    JournalMismatch(&'static str),
    StartTimeMismatch,
}

fn is_loopback_origin(origin: &str) -> bool {
    // ADR D9: the API must bind the loopback adapter only. The port is
    // matched as pure digits: `u16::from_str` accepts a leading '+', so
    // "http://127.0.0.1:+80" would slip a malformed origin past the
    // loopback gate on a parsing technicality.
    origin.strip_prefix("http://127.0.0.1:").is_some_and(|port| {
        !port.is_empty()
            && port.bytes().all(|byte| byte.is_ascii_digit())
            && port.parse::<u16>().is_ok()
    })
}

/// Length-independent equality for the owner token. Both sides already
/// hold the token (env at spawn, the published READY line), so this is
/// defense in depth — a probing helper must not be able to infer the
/// secret one byte at a time from comparison timing. Runs in time that
/// depends only on the announced length, never on WHERE a mismatch is.
fn token_matches(announced: &str, expected: &str) -> bool {
    let (left, right) = (announced.as_bytes(), expected.as_bytes());
    let length_delta = u32::try_from(left.len() ^ right.len()).unwrap_or(u32::MAX);
    let body = left.iter().zip(right).fold(0u8, |acc, (x, y)| acc | (x ^ y));
    let tail = left
        .iter()
        .skip(right.len())
        .chain(right.iter().skip(left.len()))
        .fold(0u8, |acc, byte| acc | byte);
    (u8::try_from(length_delta).unwrap_or(u8::MAX) | body | tail) == 0
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
    if !token_matches(value["token"].as_str().unwrap_or(""), token) {
        return Err(VerifyError::TokenMismatch);
    }
    // PIDs are u32 on every supported platform: a 64-bit value (tampered or
    // buggy READY output) must be rejected outright — an `as u32` cast would
    // wrap 2^32 offsets back onto the real child pid and pass ownership.
    let pid = match value["pid"].as_u64() {
        Some(pid) => u32::try_from(pid).map_err(|_| VerifyError::BadJson)?,
        None => return Err(VerifyError::BadJson),
    };
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

/// Upper bound for the ownership journal read. Journals carry identity
/// fields only (a few hundred bytes); anything larger is malformed by
/// definition, and the read must be bounded so a planted multi-GiB file at
/// the journal path cannot be buffered by the shell during verification.
pub const JOURNAL_MAX_BYTES: u64 = 64 * 1024;

/// Read a journal bounded to [`JOURNAL_MAX_BYTES`] (+1 detection byte).
/// Regular files only: a planted FIFO would block the open, and a symlink
/// is refused outright. Returns `None` for anything unreadable, over-cap
/// (buffered at most cap+1 bytes, then classified as malformed) or
/// non-UTF8 — every caller treats that as "keep / fail closed".
fn read_journal_bounded(journal: &Path) -> Option<String> {
    let meta = std::fs::symlink_metadata(journal).ok()?;
    if meta.is_symlink() || !meta.is_file() {
        return None;
    }
    let file = std::fs::File::open(journal).ok()?;
    let mut bytes = Vec::new();
    {
        use std::io::Read;
        file.take(JOURNAL_MAX_BYTES + 1).read_to_end(&mut bytes).ok()?;
    }
    if bytes.len() as u64 > JOURNAL_MAX_BYTES {
        return None;
    }
    String::from_utf8(bytes).ok()
}

/// Verify the readiness journal the helper published (identity fields only).
pub fn verify_journal(journal: &Path, ready: &ReadyPayload) -> Result<(), VerifyError> {
    let meta = std::fs::symlink_metadata(journal).map_err(|_| VerifyError::JournalMissing)?;
    if meta.is_symlink() || !meta.is_file() {
        // A planted FIFO would block the open; a symlink is refused outright.
        return Err(VerifyError::JournalMissing);
    }
    let file = std::fs::File::open(journal).map_err(|_| VerifyError::JournalMissing)?;
    let mut bytes = Vec::new();
    {
        use std::io::Read;
        file.take(JOURNAL_MAX_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| VerifyError::JournalMissing)?;
    }
    if bytes.len() as u64 > JOURNAL_MAX_BYTES {
        return Err(VerifyError::JournalTooLarge);
    }
    let text = String::from_utf8(bytes).map_err(|_| VerifyError::JournalMismatch("non-utf8"))?;
    let value: serde_json::Value =
        serde_json::from_str(&text).map_err(|_| VerifyError::JournalMismatch("unparsable"))?;
    if value["version"].as_u64() != Some(PROTOCOL_VERSION) {
        return Err(VerifyError::JournalMismatch("version"));
    }
    if !token_matches(
        value["token"].as_str().unwrap_or(""),
        ready.token.as_str(),
    ) {
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
    #[cfg(target_os = "linux")]
    {
        let announced = value["pid_starttime"].as_u64();
        let actual = crate::ownership::pid_starttime(ready.pid);
        // On Linux the journal anchors PID identity to /proc start time, the
        // equivalent of the Windows creation-time check in owned_processes.
        // The anchor is REQUIRED (red team 2026-09-09: the old
        // `is_some() &&` guard failed open on omission — the helper always
        // writes the field, so an omitted/unreadable anchor is not a journal
        // this handshake produced). Comparing the Options rejects omission,
        // mismatch, and a journal shipped for a now-dead pid alike; the one
        // residual corner is a /proc read failure for a LIVE pid (both
        // Nones match), which the GO write and health gate still gate.
        if announced != actual {
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
        let existing = match read_journal_bounded(&journal) {
            Some(text) => text,
            None => {
                eprintln!(
                    "mangaflow-desktop: ownership journal {} is unreadable, oversized or malformed; leaving it untouched instead of marking stopped",
                    journal.display()
                );
                return Ok(());
            }
        };
        let mut value: serde_json::Value = match serde_json::from_str::<serde_json::Value>(&existing) {
            Ok(value) if value.is_object() => value,
            // Anything else — unparsable bytes, or a parsable non-object
            // root (42, [1,2,3], a bare string) — is a forensic anomaly.
            // Overwriting it with a fresh stub would hide the anomaly AND
            // hand the stale-runtime sweep a terminal state to delete (an
            // object root even parses as one), destroying the record
            // exactly when it matters. Leave the bytes untouched: the sweep
            // keeps malformed journals.
            _ => {
                eprintln!(
                    "mangaflow-desktop: ownership journal {} is malformed; leaving it untouched instead of marking stopped",
                    journal.display()
                );
                return Ok(());
            }
        };
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
        // Bounded read: a planted oversized journal at a scannable name is
        // treated exactly like an unreadable one (keep the directory).
        let Some(text) = read_journal_bounded(&journal) else {
            continue;
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
    unix_now_from(SystemTime::now())
}

/// Clamps a pre-epoch clock to the epoch floor: the logging path must stay
/// panic-free, and dos_date_time clamps again at the DOS layer.
fn unix_now_from(now: SystemTime) -> u64 {
    now.duration_since(UNIX_EPOCH)
        .map(|since| since.as_secs())
        .unwrap_or(0)
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
        // Use the test process's own (live) pid with its real /proc
        // starttime for the journal phase: the Unix anchor is required, so
        // a fixture pid whose liveness varies across machines would make
        // the positive journal assertion flaky.
        let live_pid = std::process::id();
        #[cfg(target_os = "linux")]
        let live_starttime = crate::ownership::pid_starttime(live_pid);
        #[cfg(not(target_os = "linux"))]
        let live_starttime: Option<u64> = None;
        let line = format!(
            "{READY_PREFIX}{{\"token\":\"{TOKEN}\",\"pid\":{live_pid},\"api_origin\":\"http://127.0.0.1:39001\",\"web_origin\":\"http://127.0.0.1:39002\"}}"
        );
        let payload = verify_ready_line(&line, TOKEN, live_pid).unwrap();
        assert_eq!(payload.web_origin.as_deref(), Some("http://127.0.0.1:39002"));

        let hostile = format!(
            "{READY_PREFIX}{{\"token\":\"{TOKEN}\",\"pid\":{live_pid},\"api_origin\":\"http://127.0.0.1:39001\",\"web_origin\":\"http://10.0.0.9:39002\"}}"
        );
        assert!(matches!(
            verify_ready_line(&hostile, TOKEN, live_pid),
            Err(VerifyError::OriginNotLoopback)
        ));

        // Journal must carry exactly the announced web origin: missing when
        // announced, present when absent, and a mismatched value all fail.
        let dir = std::env::temp_dir().join(format!("mfd-webo-{}-{}", std::process::id(), new_token()));
        std::fs::create_dir_all(&dir).unwrap();
        let journal = dir.join(JOURNAL_NAME);
        let payload = verify_ready_line(&line, TOKEN, live_pid).unwrap();
        let mut good = serde_json::json!({
            "version": PROTOCOL_VERSION, "token": TOKEN, "state": "ready",
            "pid": live_pid, "api_origin": "http://127.0.0.1:39001",
            "web_origin": "http://127.0.0.1:39002",
        });
        #[cfg(target_os = "linux")]
        if let Some(starttime) = live_starttime {
            good["pid_starttime"] = serde_json::json!(starttime);
        }
        std::fs::write(&journal, good.to_string()).unwrap();
        assert!(verify_journal(&journal, &payload).is_ok());
        let mut mismatched = serde_json::json!({
            "version": PROTOCOL_VERSION, "token": TOKEN, "state": "ready",
            "pid": live_pid, "api_origin": "http://127.0.0.1:39001",
        });
        #[cfg(target_os = "linux")]
        if let Some(starttime) = live_starttime {
            mismatched["pid_starttime"] = serde_json::json!(starttime);
        }
        std::fs::write(&journal, mismatched.to_string()).unwrap();
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

    /// The gate is prefix-bound to http://127.0.0.1:<digits>: the localhost
    /// NAME, IPv6 loopback, a sibling domain ending in 127.0.0.1, and an
    /// empty port are all NOT the loopback origin the ADR means.
    #[test]
    fn rejects_loopback_lookalike_origins() {
        for origin in [
            "http://localhost:8000",
            "http://[::1]:8000",
            "http://127.0.0.1.evil.example:8000",
            "http://127.0.0.1:",
            "http://0.0.0.0:8000",
        ] {
            let line = format!(
                "{READY_PREFIX}{{\"token\":\"{TOKEN}\",\"pid\":4242,\"api_origin\":\"{origin}\"}}"
            );
            assert!(
                matches!(
                    verify_ready_line(&line, TOKEN, 4242),
                    Err(VerifyError::OriginNotLoopback)
                ),
                "{origin} must not pass the loopback gate"
            );
        }
    }

    /// PIDs are u32: a 64-bit READY pid (tampered output) that wraps onto
    /// the expected pid via `as u32` must be rejected outright, not pass
    /// the ownership predicate.
    #[test]
    fn rejects_pids_beyond_the_u32_range_even_when_they_wrap_onto_the_expected_pid() {
        let line = format!(
            "{READY_PREFIX}{{\"token\":\"{TOKEN}\",\"pid\":{},\"api_origin\":\"http://127.0.0.1:8000\"}}",
            4242 + (1u64 << 32)
        );
        assert!(matches!(
            verify_ready_line(&line, TOKEN, 4242),
            Err(VerifyError::BadJson)
        ));
    }

    /// The sweep is wired into session start (RunLog::create), not just
    /// reachable as a free function: a stale terminal-state runtime
    /// directory from a previous session must be gone after the new
    /// session's log exists. Production wiring uses the full 24 h grace,
    /// so the fixture ages the stale journal's mtime past it.
    #[test]
    fn session_start_sweeps_a_stale_terminal_runtime_directory() {
        let user_data = std::env::temp_dir().join(format!(
            "mangaflow-desktop-sweep-wiring-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&user_data);
        let stale = user_data
            .join("runtime")
            .join(format!("{RUNTIME_DIR_PREFIX}{}", "a".repeat(32)));
        std::fs::create_dir_all(&stale).unwrap();
        std::fs::write(
            stale.join(JOURNAL_NAME),
            format!("{{\"version\":{PROTOCOL_VERSION},\"token\":\"{}\",\"state\":\"stopped\"}}", "b".repeat(32)),
        )
        .unwrap();
        // Age the terminal journal beyond the production grace window.
        let aged = std::time::SystemTime::now()
            - std::time::Duration::from_secs(RUNTIME_SWEEP_GRACE_SECONDS * 2);
        let file = std::fs::OpenOptions::new()
            .write(true)
            .open(stale.join(JOURNAL_NAME))
            .unwrap();
        file.set_times(
            std::fs::FileTimes::new().set_modified(aged),
        )
        .unwrap();
        drop(file);

        let fresh_token = new_token();
        crate::logs::RunLog::create(&user_data, &fresh_token).unwrap();
        assert!(
            !stale.exists(),
            "session start must sweep the stale terminal runtime directory"
        );

        let _ = std::fs::remove_dir_all(&user_data);
    }

    /// The loopback gate matches ports as pure digits: a leading '+'
    /// (accepted by `u16::from_str`) must fail the gate, not pass it on a
    /// parsing technicality. Red on the old `parse::<u16>()` check.
    #[test]
    fn rejects_a_plus_prefixed_port_in_the_origin() {
        let line = format!(
            "{READY_PREFIX}{{\"token\":\"{TOKEN}\",\"pid\":4242,\"api_origin\":\"http://127.0.0.1:+80\"}}"
        );
        assert!(matches!(
            verify_ready_line(&line, TOKEN, 4242),
            Err(VerifyError::OriginNotLoopback)
        ));
    }

    /// Token comparison stays correct for the cases timing-hardening must
    /// not break: equal strings match, and any differing byte/length is a
    /// mismatch (the fold covers bytes past the shorter side too).
    #[test]
    fn token_matches_survives_length_and_byte_differences() {
        assert!(token_matches(TOKEN, TOKEN));
        assert!(!token_matches("", TOKEN) && !token_matches(TOKEN, ""));
        assert!(!token_matches(&TOKEN[..31], TOKEN));
        assert!(!token_matches(
            "0123456789abcdef0123456789abcdeG",
            TOKEN
        ));
    }

    /// The /proc starttime anchor is REQUIRED on Unix (red team 2026-09-09
    /// — the old `is_some() &&` guard failed open on omission): a journal
    /// carrying the anchor must match the live process; a mismatching or
    /// absent anchor fails closed. The Windows leg has no /proc equivalent
    /// (Job membership anchors there), and pid_starttime reads /proc, so
    /// the whole matrix is unix-gated.
    #[cfg(target_os = "linux")]
    #[test]
    fn journal_starttime_anchor_matches_or_fails_closed() {
        let dir = std::env::temp_dir().join(format!(
            "mangaflow-desktop-starttime-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let layout = RuntimeLayout::create(&dir).unwrap();
        let token = layout.token.clone();
        let ready = ReadyPayload {
            token: token.clone(),
            pid: std::process::id(),
            api_origin: "http://127.0.0.1:8080".into(),
            port: 8080,
            web_origin: None,
        };
        let live = crate::ownership::pid_starttime(std::process::id());
        let mut journal: serde_json::Value = serde_json::json!({
            "version": PROTOCOL_VERSION,
            "token": token,
            "state": "ready",
            "pid": std::process::id(),
            "api_origin": "http://127.0.0.1:8080",
        });
        if let Some(starttime) = live {
            journal["pid_starttime"] = serde_json::json!(starttime);
        }
        std::fs::write(layout.journal_path(), journal.to_string()).unwrap();
        assert!(verify_journal(&layout.journal_path(), &ready).is_ok());

        journal["pid_starttime"] = serde_json::json!(live.unwrap_or(0) + 1);
        std::fs::write(layout.journal_path(), journal.to_string()).unwrap();
        assert!(matches!(
            verify_journal(&layout.journal_path(), &ready),
            Err(VerifyError::StartTimeMismatch)
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Every tampered identity field fails closed with the offending field
    /// named; the positive control proves the fixture itself is valid.
    #[test]
    fn journal_tamper_matrix_fails_closed_on_every_identity_field() {
        let dir = std::env::temp_dir().join(format!(
            "mangaflow-desktop-tamper-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let layout = RuntimeLayout::create(&dir).unwrap();
        let token = layout.token.clone();
        let ready = ReadyPayload {
            token: token.clone(),
            pid: std::process::id(),
            api_origin: "http://127.0.0.1:8080".into(),
            port: 8080,
            web_origin: None,
        };
        let mut base = serde_json::json!({
            "version": PROTOCOL_VERSION,
            "token": token,
            "state": "ready",
            "pid": std::process::id(),
            "api_origin": "http://127.0.0.1:8080",
        });
        // The Unix anchor belongs in the base fixture: the helper always
        // writes it, and verify_journal treats a missing/mismatched
        // pid_starttime as a failure (red team 2026-09-09).
        #[cfg(target_os = "linux")]
        if let Some(starttime) = crate::ownership::pid_starttime(std::process::id()) {
            base["pid_starttime"] = serde_json::json!(starttime);
        }
        let mut tampered: Vec<(&str, serde_json::Value)> = vec![
            ("version", serde_json::json!(PROTOCOL_VERSION + 1)),
            ("token", serde_json::json!("f".repeat(32))),
            ("state", serde_json::json!("stopped")),
            ("pid", serde_json::json!(std::process::id() + 1)),
            ("api_origin", serde_json::json!("http://127.0.0.1:9999")),
        ];
        #[cfg(target_os = "linux")]
        {
            // The anchor must be present AND correct: omission is no longer
            // a fail-open skip. Linux-gated (the /proc anchor does not exist
            // on other unix targets, where None==None would legitimately pass).
            tampered.push(("pid_starttime", serde_json::Value::Null));
            tampered.push(("pid_starttime", serde_json::json!(999)));
        }
        for (field, value) in tampered {
            let mut journal = base.clone();
            journal[field] = value;
            std::fs::write(layout.journal_path(), journal.to_string()).unwrap();
            let result = verify_journal(&layout.journal_path(), &ready);
            let failed_closed = match result {
                Err(VerifyError::JournalMismatch(name)) => name == field,
                Err(VerifyError::StartTimeMismatch) => field == "pid_starttime",
                _ => false,
            };
            assert!(failed_closed, "tampering {field} must fail closed");
        }
        // Positive control: the untampered journal passes.
        std::fs::write(layout.journal_path(), base.to_string()).unwrap();
        assert!(verify_journal(&layout.journal_path(), &ready).is_ok());
        // Omitting the anchor entirely fails closed too (Linux-only: see
        // the tamper-gate note above).
        #[cfg(target_os = "linux")]
        {
            let mut without_anchor = base.clone();
            without_anchor.as_object_mut().unwrap().remove("pid_starttime");
            std::fs::write(layout.journal_path(), without_anchor.to_string()).unwrap();
            assert!(matches!(
                verify_journal(&layout.journal_path(), &ready),
                Err(VerifyError::StartTimeMismatch)
            ));
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// An unparsable journal is a forensic anomaly: mark_stopped must leave
    /// the bytes untouched instead of replacing them with a deletable stub
    /// (the stale-runtime sweep deletes terminal-state directories, so a
    /// stub here would destroy the record exactly when it matters).
    #[test]
    fn mark_stopped_preserves_an_unparsable_journal() {
        let dir = std::env::temp_dir().join(format!(
            "mangaflow-desktop-corrupt-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let layout = RuntimeLayout::create(&dir).unwrap();
        std::fs::write(layout.journal_path(), "{\"state\": \"rea").unwrap();

        layout.mark_stopped(Some(0)).unwrap();

        assert_eq!(
            std::fs::read_to_string(layout.journal_path()).unwrap(),
            "{\"state\": \"rea",
            "the corrupt bytes must survive verbatim"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// An oversized journal (identity fields are a few hundred bytes) must
    /// fail with JournalTooLarge instead of being buffered into the shell —
    /// the read is bounded at the cap with one detection byte to spare.
    #[test]
    fn journal_reads_are_bounded_and_oversize_fails_closed() {
        let dir = std::env::temp_dir().join(format!(
            "mangaflow-desktop-jsize-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let layout = RuntimeLayout::create(&dir).unwrap();
        let oversized = "x".repeat(JOURNAL_MAX_BYTES as usize + 1);
        std::fs::write(layout.journal_path(), oversized).unwrap();
        let ready = ReadyPayload {
            token: layout.token.clone(),
            pid: 1,
            api_origin: "http://127.0.0.1:8080".into(),
            port: 8080,
            web_origin: None,
        };
        assert!(matches!(
            verify_journal(&layout.journal_path(), &ready),
            Err(VerifyError::JournalTooLarge)
        ));
        // Boundary complement: a journal at exactly the cap is readable and
        // fails later on content, not on size.
        let at_cap = "x".repeat(JOURNAL_MAX_BYTES as usize);
        std::fs::write(layout.journal_path(), at_cap).unwrap();
        assert!(matches!(
            verify_journal(&layout.journal_path(), &ready),
            Err(VerifyError::JournalMismatch("unparsable"))
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The error enum is user-facing: Display carries the failing step,
    /// and the value routes through `Box<dyn std::error::Error>` like any
    /// other error path.
    #[test]
    fn verify_errors_display_and_route_through_the_error_trait() {
        let error: Box<dyn std::error::Error> = Box::new(VerifyError::OriginNotLoopback);
        assert!(
            error.to_string().contains("回环"),
            "unexpected message: {error}"
        );
        let error: Box<dyn std::error::Error> =
            Box::new(VerifyError::JournalMismatch("token"));
        assert!(error.to_string().contains("token"), "{error}");
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

    /// Error-path pin: a DIRECTORY at the journal path fails the read
    /// (EISDIR) and surfaces as JournalMissing — not a panic and not an
    /// accidental parse.
    #[test]
    fn journal_directory_path_surfaces_as_journal_missing() {
        let dir = std::env::temp_dir().join(format!(
            "mangaflow-desktop-jdir-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        let journal = dir.join("owner.json");
        std::fs::create_dir_all(&journal).unwrap();
        let ready = ReadyPayload {
            token: "0".repeat(32),
            pid: 1,
            api_origin: "http://127.0.0.1:8080".into(),
            port: 8080,
            web_origin: None,
        };
        assert!(matches!(
            verify_journal(&journal, &ready),
            Err(VerifyError::JournalMissing)
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// unix_now clamps a pre-epoch clock to 0 instead of panicking — the
    /// logging path must stay panic-free. The seam takes an explicit
    /// SystemTime so the pre-epoch case is exercised directly (red on the
    /// old unwrap: it panicked instead of returning 0).
    #[test]
    fn unix_now_clamps_a_pre_epoch_clock() {
        let pre_epoch = UNIX_EPOCH - std::time::Duration::from_secs(1);
        assert_eq!(unix_now_from(pre_epoch), 0);
        let at_epoch = UNIX_EPOCH;
        assert_eq!(unix_now_from(at_epoch), 0);
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
    /// removed through (Unix: real symlink; Windows: junction via
    /// `mklink /J`, which needs no privilege — otherwise the Windows leg
    /// of this test would be a vacuous pass).
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
        std::fs::create_dir_all(user_data.join("runtime")).unwrap();
        let planted = user_data
            .join("runtime")
            .join(format!("{RUNTIME_DIR_PREFIX}{}", "7".repeat(32)));
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(&outside, &planted).unwrap();
            sweep_runtime_dirs_with(&user_data, 0).unwrap();
            // The link entry is not a dir from read_dir's file_type: skipped,
            // and the target directory survives untouched.
            assert!(outside.join(JOURNAL_NAME).exists());
            let _ = std::fs::remove_file(&planted);
        }
        #[cfg(windows)]
        {
            // A junction is the privilege-free Windows equivalent of a
            // planted link; whichever guard fires (entry file type or the
            // canonical containment — the junction resolves outside the
            // runtime root), the target must survive.
            let output = std::process::Command::new("cmd")
                .args(["/C", "mklink", "/J"])
                .arg(&planted)
                .arg(&outside)
                .output()
                .expect("run mklink /J");
            assert!(
                output.status.success(),
                "mklink /J failed: {}",
                String::from_utf8_lossy(&output.stderr)
            );
            sweep_runtime_dirs_with(&user_data, 0).unwrap();
            assert!(
                outside.join(JOURNAL_NAME).exists(),
                "junction target must survive the sweep"
            );
            // remove_dir on a junction removes the link, not the target.
            let _ = std::fs::remove_dir(&planted);
            assert!(outside.join(JOURNAL_NAME).exists());
        }
        let _ = std::fs::remove_dir_all(&user_data);
        let _ = std::fs::remove_dir_all(&outside);
    }
}
