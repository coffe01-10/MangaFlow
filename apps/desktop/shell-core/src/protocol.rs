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
        Self::create_with_token(user_data, &new_token())
    }

    /// Test seam: `create` with a caller-chosen token, so the canonical-name
    /// mismatch guard is reachable (a planted symlink at the exact runtime
    /// name cannot be built against a random 128-bit token).
    fn create_with_token(user_data: &Path, token: &str) -> std::io::Result<RuntimeLayout> {
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
        // Containment, not just the leaf name: a symlink planted at the
        // runtime name pointing at a same-leaf directory OUTSIDE the
        // user-data root passes the leaf check above but relocates the
        // session's journal (and everything the helper writes under it)
        // to an attacker-chosen subtree. The session tree must live inside
        // the user-data root, resolved on both sides (#458).
        let user_data_canonical = user_data.canonicalize()?;
        if !resolved.starts_with(&user_data_canonical) {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "runtime path escapes the user-data root",
            ));
        }
        let layout = RuntimeLayout {
            user_data: user_data.to_path_buf(),
            token: token.to_string(),
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
        apply_stop(&mut value, exit_code, unix_now());
        write_journal_atomic(&journal, &value)?;

        // #602 post-write verify (single bounded retry — never a loop): the
        // helper also writes this journal (every state transition). A late
        // helper `ready` landing between the rename above and this read
        // would revive a dead session's journal to a state the stale-runtime
        // sweep never reclaims. Re-read ONCE; when the state is not
        // "stopped", merge the stop onto the CURRENT record (keeping its
        // fields) and write again through the same atomic helper. An
        // unparsable/unreadable re-read keeps the bytes untouched, exactly
        // like the pre-write guard above.
        //
        // Residual window, admitted: this verify and the helper's
        // pre-replace terminal-state check are complementary best-effort
        // checks, NOT an atomic CAS. The shell's stop can land between the
        // helper's read and its own replace (reviving), and this post-write
        // verify can in turn be overtaken by a later helper write landing
        // after its read — the combined window is small but not zero.
        if let Some(text) = read_journal_bounded(&journal) {
            if let Ok(current) = serde_json::from_str::<serde_json::Value>(&text) {
                if let Some(merged) = merge_stop_onto_current(&current, exit_code, unix_now()) {
                    return write_journal_atomic(&journal, &merged);
                }
            }
        }
        Ok(())
    }
}

/// The stop transformation applied to a journal record: terminal state,
/// timestamp, and (when this stop knows one) the exit code. Shared by the
/// initial write and the #602 post-write re-apply so the two can never drift.
fn apply_stop(record: &mut serde_json::Value, exit_code: Option<i32>, stopped_at: u64) {
    record["state"] = "stopped".into();
    record["stopped_at"] = stopped_at.into();
    if let Some(code) = exit_code {
        record["exit_code"] = code.into();
    }
}

/// #602 pure decision seam for `mark_stopped`'s post-write verify: decide
/// whether the just-written stop was clobbered by a concurrent helper write,
/// and if so, build the record to re-publish.
///
/// - `None` — no re-apply: the current record already says `"stopped"`, or it
///   is a non-object (forensic anomaly — overwriting would destroy it, same
///   rationale as the pre-write guard).
/// - `Some(merged)` — the current record (fields preserved) with the stop
///   transformation applied: `state="stopped"`, `stopped_at`, and `exit_code`
///   when this call intended one (a `None` exit code leaves any existing
///   field untouched, mirroring the first write).
fn merge_stop_onto_current(
    current: &serde_json::Value,
    exit_code: Option<i32>,
    stopped_at: u64,
) -> Option<serde_json::Value> {
    if !current.is_object() || current["state"].as_str() == Some("stopped") {
        return None;
    }
    let mut merged = current.clone();
    apply_stop(&mut merged, exit_code, stopped_at);
    Some(merged)
}

/// Atomic journal write (pending file + rename, same shape as the helper and
/// `owned_processes.py`). Identity fields only — never commands, env, secrets.
/// The staging name is shell-owned (``owner.json.shell.pending``, #602): the
/// helper stages its writes to ``owner.json.helper.pending`` — a shared
/// pending name let the two writers rendezvous (publishing the other's
/// payload or failing with the other's missing file).
fn write_journal_atomic(journal: &Path, record: &serde_json::Value) -> std::io::Result<()> {
    let pending = journal.with_file_name(format!(
        "{}.shell.pending",
        journal.file_name().unwrap_or_default().to_string_lossy()
    ));
    // Same-user link planting (#561): a symlink at either name would make
    // the pending write follow it and redirect the ~200-byte ownership
    // record to an attacker-chosen file. Refuse both before any write —
    // the helper's _write_journal enforces the same parity.
    for path in [journal, &pending] {
        if std::fs::symlink_metadata(path)
            .is_ok_and(|meta| meta.is_symlink())
        {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                format!("process journal must not be a link: {}", path.display()),
            ));
        }
    }
    // Serialization of a serde_json::Value cannot fail today, but the
    // journal write path is on the teardown hotline (every stop path calls
    // mark_stopped) — keep it panic-free by contract, not by review.
    let payload = serde_json::to_vec(record).map_err(|error| {
        std::io::Error::new(std::io::ErrorKind::InvalidData, error.to_string())
    })?;
    std::fs::write(&pending, payload)?;
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
/// leg now holds its own single-instance mutex (keyed by its canonical
/// user-data path, App.xaml.cs), but this sweep cannot observe that lock: it
/// never learns which user-data directory the WPF leg runs on, so a
/// non-terminal directory may still belong to a live session. Foreign names
/// (not `mangaflow-desktop-<32 hex>`), symlinks/junctions planted at a
/// candidate name, unparsable journals, and anything that does not
/// canonically resolve inside the runtime root are all left untouched.
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
    // #610: a planted root link would redirect the sweep — canonicalize
    // follows it and the candidate containment at the bottom would compare
    // the target against itself. The candidate level already refuses links;
    // the root gets the same refusal.
    if runtime
        .symlink_metadata()
        .is_ok_and(|meta| meta.is_symlink())
    {
        eprintln!(
            "mangaflow-desktop: runtime directory {} is a symlink; skipping the stale-runtime sweep (#610)",
            runtime.display()
        );
        return Ok(());
    }
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
        #[allow(unused_variables)] // only the linux cfg blocks below read it
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
        #[cfg_attr(not(target_os = "linux"), allow(unused_mut))] // mutated by the linux anchor block only
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
        #[cfg_attr(not(target_os = "linux"), allow(unused_mut))] // mutated by the linux anchor block only
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
        #[cfg_attr(not(target_os = "linux"), allow(unused_mut))] // mutated by the linux anchor block only
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
        #[cfg_attr(not(target_os = "linux"), allow(unused_mut))] // extended by the linux anchor cases only
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

    /// #602 seam test for the post-write verify: when a concurrent helper
    /// write (e.g. a late `ready`) clobbers the stop, the merge decision must
    /// re-apply the stop ONTO THE CURRENT RECORD — state=stopped with this
    /// call's exit_code and stopped_at, every other field carried over from
    /// the helper's record — and the decision must be pure (the input record
    /// is never mutated).
    #[test]
    fn merge_stop_reapplies_a_clobbered_stop_onto_the_current_record() {
        let current = serde_json::json!({
            "version": PROTOCOL_VERSION,
            "token": TOKEN,
            "state": "ready",
            "pid": 4242,
            "api_origin": "http://127.0.0.1:39001",
            "web_origin": "http://127.0.0.1:39002",
        });
        let merged = merge_stop_onto_current(&current, Some(7), 1_700_000_012)
            .expect("a clobbered (non-stopped) current record must re-apply the stop");
        assert_eq!(merged["state"], "stopped");
        assert_eq!(merged["exit_code"], 7);
        assert_eq!(merged["stopped_at"], 1_700_000_012);
        // Every other field comes from the CURRENT (re-read) record, not
        // from the shell's earlier write: identity and origins survive the
        // re-apply.
        assert_eq!(merged["pid"], 4242);
        assert_eq!(merged["token"], TOKEN);
        assert_eq!(merged["api_origin"], "http://127.0.0.1:39001");
        assert_eq!(merged["web_origin"], "http://127.0.0.1:39002");
        // Purity: the decision must not mutate its input.
        assert_eq!(current["state"], "ready");
        assert!(current.get("stopped_at").is_none());
    }

    /// The no-op half of the seam: a journal whose state already says
    /// "stopped" (the common case — nothing raced the rename) needs no
    /// second write.
    #[test]
    fn merge_stop_is_a_noop_when_the_stop_survived() {
        let stopped = serde_json::json!({
            "version": PROTOCOL_VERSION,
            "token": TOKEN,
            "state": "stopped",
            "stopped_at": 1,
            "exit_code": 0,
        });
        assert!(merge_stop_onto_current(&stopped, Some(0), 2).is_none());
    }

    /// A re-read `failed` record also gets the stop re-applied (the design
    /// re-applies on any non-stopped state), but its forensic fields ride
    /// along; an exit_code of None must not inject an exit_code field —
    /// mirroring the first write's conditional.
    #[test]
    fn merge_stop_reapplies_onto_a_failed_record_and_keeps_its_fields() {
        let failed = serde_json::json!({
            "version": PROTOCOL_VERSION,
            "token": TOKEN,
            "state": "failed",
            "error": "alembic:OperationalError",
        });
        let merged = merge_stop_onto_current(&failed, None, 5)
            .expect("a clobbered failed record must re-apply the stop");
        assert_eq!(merged["state"], "stopped");
        assert_eq!(merged["stopped_at"], 5);
        assert_eq!(merged["error"], "alembic:OperationalError");
        assert!(
            merged.get("exit_code").is_none(),
            "a None exit code must not fabricate an exit_code field"
        );
    }

    /// Non-object re-reads (42, [1,2,3]) are forensic anomalies, exactly like
    /// the pre-write guard: the merge must refuse instead of fabricating a
    /// deletable object from them.
    #[test]
    fn merge_stop_refuses_non_object_current_records() {
        for current in [serde_json::json!(42), serde_json::json!([1, 2, 3])] {
            assert!(
                merge_stop_onto_current(&current, Some(0), 1).is_none(),
                "a non-object current record must not be re-written: {current}"
            );
        }
    }

    /// #602 staging-name pin: a successful mark_stopped must leave no pending
    /// sibling behind — neither the shell-owned `owner.json.shell.pending`
    /// nor the legacy shared `owner.json.pending` the two writers used to
    /// rendezvous on.
    #[test]
    fn mark_stopped_leaves_no_shell_pending_sibling_behind() {
        let dir = std::env::temp_dir().join(format!(
            "mangaflow-desktop-nopending-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let layout = RuntimeLayout::create(&dir).unwrap();

        layout.mark_stopped(Some(0)).unwrap();

        let journal = layout.journal_path();
        let value: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&journal).unwrap()).unwrap();
        assert_eq!(value["state"], "stopped");
        let directory = journal.parent().expect("the journal lives in its runtime dir");
        assert!(
            !directory.join(format!("{JOURNAL_NAME}.shell.pending")).exists(),
            "the shell pending sibling must not survive a successful stop write"
        );
        assert!(
            !directory.join(format!("{JOURNAL_NAME}.pending")).exists(),
            "the legacy shared pending name must stay unused (#602)"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// #561 (helper parity): a symlink at the journal or the .pending
    /// sibling must be refused before any write — the pending write
    /// follows links, so a planted link would redirect the ownership
    /// record to an attacker-chosen file. Both refusals surface as
    /// InvalidInput with the link named; the outside target stays
    /// untouched. Unix-only: symlink creation.
    #[test]
    #[cfg(unix)]
    fn write_journal_atomic_refuses_links_at_both_names() {
        use crate::protocol::new_token;
        use std::fs;
        use std::os::unix::fs::symlink as platform_symlink;
        let user_data = std::env::temp_dir().join(format!(
            "mangaflow-desktop-jlink-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = fs::remove_dir_all(&user_data);
        let runtime = user_data.join("runtime").join(format!(
            "{RUNTIME_DIR_PREFIX}{}",
            "a".repeat(32)
        ));
        fs::create_dir_all(&runtime).unwrap();
        let journal = runtime.join(JOURNAL_NAME);
        let pending = journal.with_file_name(format!("{JOURNAL_NAME}.shell.pending"));
        let outside = user_data.join("outside.json");
        fs::write(&outside, b"{}").unwrap();

        // (1) Link at the journal: refused, outside untouched.
        platform_symlink(&outside, &journal).unwrap();
        let error = write_journal_atomic(&journal, &serde_json::json!({"state": "ready"}))
            .err()
            .expect("a symlinked journal must be refused");
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
        assert!(error.to_string().contains("must not be a link"));
        assert_eq!(fs::read(&outside).unwrap(), b"{}");

        // (2) Link at the pending sibling: refused, journal not created,
        // outside untouched (write_text would have followed the link).
        fs::remove_file(&journal).unwrap();
        platform_symlink(&outside, &pending).unwrap();
        let error = write_journal_atomic(&journal, &serde_json::json!({"state": "ready"}))
            .err()
            .expect("a symlinked pending sibling must be refused");
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
        assert_eq!(fs::read(&outside).unwrap(), b"{}");
        assert!(!journal.exists());

        let _ = fs::remove_dir_all(&user_data);
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

    /// The token generator's output contract: 32 lowercase hex chars, the
    /// exact shape the stale-runtime sweep's name predicate (and every
    /// log/journal name in the crate) recognizes, and unique across draws.
    /// A formatting or determinism regression here would make new runtime
    /// directories permanently invisible to the sweep.
    #[test]
    fn new_token_output_satisfies_the_token_format_contract_and_is_unique() {
        use std::collections::HashSet;
        let mut seen = HashSet::new();
        for _ in 0..128 {
            let token = new_token();
            assert_eq!(token.len(), 32, "{token}");
            assert!(
                token.bytes().all(|byte| byte.is_ascii_digit()
                    || (b'a'..=b'f').contains(&byte)),
                "token must be lowercase hex: {token}"
            );
            assert!(
                is_runtime_dir_name(&format!("{RUNTIME_DIR_PREFIX}{token}")),
                "the sweep must recognize the generated runtime directory name"
            );
            assert!(seen.insert(token.clone()), "token collision: {token}");
        }
        assert_eq!(seen.len(), 128);
    }

    /// Clock-skew fail-closed leg: a terminal journal whose mtime is in the
    /// FUTURE must keep the directory (duration_since errs → continue).
    /// A journal that is a FIFO must keep the candidate WITHOUT blocking:
    /// read_journal_bounded checks regular-file via metadata BEFORE any
    /// open, so the sweep can never hang on a planted pipe. Unix-only:
    /// mkfifo is a libc call. The sweep runs on a spawned thread with a
    /// bounded join — if the metadata-before-open ordering ever regressed,
    /// this test would HANG forever, so the regression must surface as a
    /// failure instead.
    #[test]
    #[cfg(unix)]
    fn sweep_keeps_a_candidate_whose_journal_is_a_fifo() {
        use std::ffi::CString;
        use std::time::Duration;

        let user_data = std::env::temp_dir().join(format!(
            "mangaflow-desktop-sweep-fifo-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&user_data);
        let runtime = user_data.join("runtime");
        let candidate = runtime.join(format!("{RUNTIME_DIR_PREFIX}{}", "b".repeat(32)));
        std::fs::create_dir_all(&candidate).unwrap();
        let journal = candidate.join(JOURNAL_NAME);
        let cpath =
            CString::new(journal.as_os_str().as_encoded_bytes()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(cpath.as_ptr(), 0o644) }, 0);

        // The wait is bounded by a channel: a regression to open-before-
        // metadata would hang the sweep forever on the writer-less FIFO —
        // a hang must surface as a test failure, not wedge the suite.
        let (tx, rx) = std::sync::mpsc::channel();
        let worker = std::thread::spawn({
            let user_data = user_data.clone();
            move || {
                let result = std::panic::catch_unwind(
                    std::panic::AssertUnwindSafe(|| {
                        sweep_runtime_dirs_with(&user_data, 0)
                    }),
                );
                let _ = tx.send(());
                result
            }
        });
        let sweep_started = rx
            .recv_timeout(Duration::from_secs(60))
            .expect("the sweep hung on the FIFO journal — metadata-before-open regression");
        let sweep_result = worker
            .join()
            .unwrap_or_else(|payload| panic!("the sweep worker panicked: {payload:?}"));
        assert!(
            sweep_result.is_ok(),
            "the sweep must not error on a FIFO journal: {sweep_result:?}"
        );
        let _ = sweep_started;

        assert!(
            candidate.exists() && journal.exists(),
            "the FIFO-journal candidate must be kept without blocking"
        );
        let _ = std::fs::remove_dir_all(&user_data);
    }

    #[test]
    fn sweep_keeps_a_candidate_with_a_future_mtime_journal() {
        let user_data = std::env::temp_dir().join(format!(
            "mangaflow-desktop-sweep-future-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&user_data);
        let runtime = user_data.join("runtime");
        let candidate = runtime.join(format!("{RUNTIME_DIR_PREFIX}{}", "e".repeat(32)));
        std::fs::create_dir_all(&candidate).unwrap();
        std::fs::write(
            candidate.join(JOURNAL_NAME),
            format!("{{\"version\":1,\"token\":\"{}\",\"state\":\"stopped\"}}", "f".repeat(32)),
        )
        .unwrap();
        let future = std::time::SystemTime::now() + std::time::Duration::from_secs(3600);
        let handle = std::fs::OpenOptions::new()
            .write(true)
            .open(candidate.join(JOURNAL_NAME))
            .unwrap();
        handle
            .set_times(std::fs::FileTimes::new().set_modified(future))
            .unwrap();
        drop(handle);

        // Negative control: an AGED terminal twin must be removed by the
        // same sweep, proving the future-mtime keep is the anchor at work.
        let aged = runtime.join(format!("{RUNTIME_DIR_PREFIX}{}", "9".repeat(32)));
        std::fs::create_dir_all(&aged).unwrap();
        std::fs::write(
            aged.join(JOURNAL_NAME),
            format!("{{\"version\":1,\"token\":\"{}\",\"state\":\"stopped\"}}", "9".repeat(32)),
        )
        .unwrap();

        sweep_runtime_dirs_with(&user_data, 0).unwrap();

        assert!(
            candidate.exists(),
            "a future-mtime terminal journal must keep the candidate (clock-skew fail-closed)"
        );
        assert!(
            !aged.exists(),
            "the aged control must be swept (proves the sweep ran)"
        );
        let _ = std::fs::remove_dir_all(&user_data);
    }

    /// The sweep reads a candidate's journal through read_journal_bounded,
    /// which refuses symlinks: a planted symlink at owner.json must keep
    /// the candidate directory AND the link target's bytes intact — the
    /// deletion decision may never be driven by content outside the
    /// runtime root.
    /// A journal that is a DIRECTORY (not a regular file) must keep the
    /// candidate exactly like a symlink: read_journal_bounded refuses
    /// non-regular files, and the deletion decision may never depend on
    /// their content.
    #[test]
    fn sweep_keeps_a_candidate_whose_journal_is_a_directory() {
        let user_data = std::env::temp_dir().join(format!(
            "mangaflow-desktop-sweep-jdir-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&user_data);
        let runtime = user_data.join("runtime");
        let candidate = runtime.join(format!("{RUNTIME_DIR_PREFIX}{}", "b".repeat(32)));
        std::fs::create_dir_all(candidate.join(JOURNAL_NAME)).unwrap();

        sweep_runtime_dirs_with(&user_data, 0).unwrap();

        assert!(
            candidate.exists(),
            "a directory-as-journal candidate must be kept"
        );
        let _ = std::fs::remove_dir_all(&user_data);
    }

    /// mark_stopped on a MISSING journal must not fabricate a stopped
    /// record: absent ownership records are kept absent (the sweep then
    /// ignores the directory as a foreign/empty name).
    #[test]
    fn mark_stopped_without_a_journal_fabricates_nothing() {
        let dir = std::env::temp_dir().join(format!(
            "mangaflow-desktop-missing-j-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        let layout = RuntimeLayout::create(&dir).unwrap();
        std::fs::remove_file(layout.journal_path()).unwrap();

        layout.mark_stopped(Some(0)).unwrap();

        assert!(
            !layout.journal_path().exists(),
            "mark_stopped must not fabricate a stopped record for a missing journal"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn sweep_keeps_a_candidate_whose_journal_is_a_symlink() {
        let user_data = std::env::temp_dir().join(format!(
            "mangaflow-desktop-sweep-symlink-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&user_data);
        let runtime = user_data.join("runtime");
        let candidate = runtime.join(format!("{RUNTIME_DIR_PREFIX}{}", "c".repeat(32)));
        std::fs::create_dir_all(&candidate).unwrap();
        let outside = user_data.join("outside.json");
        std::fs::write(
            &outside,
            format!("{{\"version\":1,\"token\":\"{}\",\"state\":\"stopped\"}}", "d".repeat(32)),
        )
        .unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(&outside, candidate.join(JOURNAL_NAME)).unwrap();
        #[cfg(windows)]
        {
            // Windows symlink_file needs privileges; if creation is refused,
            // skip the case instead of panicking at fixture setup.
            if std::os::windows::fs::symlink_file(&outside, candidate.join(JOURNAL_NAME)).is_err() {
                eprintln!("symlink creation not permitted; skipping the sweep-symlink case");
                let _ = std::fs::remove_dir_all(&user_data);
                return;
            }
        }

        sweep_runtime_dirs_with(&user_data, 0).unwrap();

        assert!(candidate.exists(), "the candidate directory must be kept");
        assert_eq!(
            std::fs::read_to_string(outside).unwrap(),
            format!("{{\"version\":1,\"token\":\"{}\",\"state\":\"stopped\"}}", "d".repeat(32)),
            "the link target's bytes must be untouched"
        );
        let _ = std::fs::remove_dir_all(&user_data);
    }

    /// Error-path pin: a symlink at the journal path fails closed (the
    /// bounded reader refuses links, surfaced as JournalMissing) and the
    /// link target's bytes stay untouched. Unix-only: planting the link
    /// needs the platform symlink API.
    #[test]
    #[cfg(unix)]
    fn journal_symlink_fails_closed_and_spares_the_target() {
        let dir = std::env::temp_dir().join(format!(
            "mangaflow-desktop-jsymlink-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let journal = dir.join(JOURNAL_NAME);
        let ready = ReadyPayload {
            token: "0".repeat(32),
            pid: 1,
            api_origin: "http://127.0.0.1:8080".into(),
            port: 8080,
            web_origin: None,
        };
        let outside = dir.join("outside.json");
        std::fs::write(&outside, "{\"stolen\": true}").unwrap();
        std::os::unix::fs::symlink(&outside, &journal).unwrap();
        assert!(matches!(
            verify_journal(&journal, &ready),
            Err(VerifyError::JournalMissing)
        ));
        assert_eq!(
            std::fs::read_to_string(&outside).unwrap(),
            "{\"stolen\": true}",
            "the symlink target's bytes must be untouched"
        );
        std::fs::remove_file(&journal).unwrap();
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Error-path pin (cross-platform, red team 2026-09-09 review: the
    /// non-UTF8 classification needs no privileges and no symlinks, so it
    /// must not hide behind the symlink test's unix gate): a journal whose
    /// bytes are not valid UTF-8 fails closed as JournalMismatch("non-utf8")
    /// instead of being read lossily or panicking.
    #[test]
    fn journal_non_utf8_fails_closed() {
        let dir = std::env::temp_dir().join(format!(
            "mangaflow-desktop-jnonutf8-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let journal = dir.join(JOURNAL_NAME);
        let ready = ReadyPayload {
            token: "0".repeat(32),
            pid: 1,
            api_origin: "http://127.0.0.1:8080".into(),
            port: 8080,
            web_origin: None,
        };
        std::fs::write(&journal, b"\xff\xfe not utf8").unwrap();
        assert!(matches!(
            verify_journal(&journal, &ready),
            Err(VerifyError::JournalMismatch("non-utf8"))
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Table-driven boundaries for the sweep's runtime-directory name
    /// predicate: exactly prefix + 32 lowercase-hex chars passes; case,
    /// length, charset and prefix drift each fail. The stale-runtime sweep
    /// deletes directories ONLY when this predicate passes, so a false
    /// positive here would widen deletion to foreign directories.
    #[test]
    fn runtime_dir_name_predicate_boundaries() {
        let valid = "0123456789abcdef0123456789abcdef";
        assert!(is_runtime_dir_name(&format!("{RUNTIME_DIR_PREFIX}{valid}")));
        for invalid in [
            // Case drift (the documented alphabet is lowercase).
            format!("{RUNTIME_DIR_PREFIX}{}", "A".repeat(32)),
            format!("{RUNTIME_DIR_PREFIX}{}", valid.to_ascii_uppercase()),
            // Length boundaries on both sides of 32.
            format!("{RUNTIME_DIR_PREFIX}{}", "a".repeat(31)),
            format!("{RUNTIME_DIR_PREFIX}{}", "a".repeat(33)),
            // Charset drift.
            format!("{RUNTIME_DIR_PREFIX}{}", "g".repeat(32)),
            // Prefix drift (missing the trailing separator of the real
            // prefix) and a foreign prefix entirely.
            format!("mangaflow-desktop{valid}"),
            format!("mangaflow-desktopx-{valid}"),
            format!("other-{valid}"),
            valid.to_string(),
        ] {
            assert!(
                !is_runtime_dir_name(&invalid),
                "invalid runtime dir name must be rejected: {invalid}"
            );
        }
    }

    /// The canonical-name guard: a symlink planted at the EXACT runtime
    /// directory name redirects create_dir_all elsewhere, and canonicalize
    /// resolves to a differently-named directory — the layout must refuse
    /// with InvalidInput rather than journal ownership under a name the
    /// shell does not own. Reachable only through the token seam: a random
    /// 128-bit token cannot be targeted in advance (on case-insensitive
    /// filesystems the same guard catches casing drift of a real dir).
    #[cfg(unix)]
    #[test]
    fn runtime_layout_refuses_a_planted_symlink_at_its_own_name() {
        use std::os::unix::fs::symlink;

        let user_data = std::env::temp_dir().join(format!(
            "mangaflow-layout-guard-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&user_data);
        let elsewhere = std::env::temp_dir().join(format!(
            "mangaflow-layout-elsewhere-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&elsewhere);
        let decoy = elsewhere.join("renamed-target");
        std::fs::create_dir_all(&decoy).unwrap();

        let token = new_token();
        let planted = user_data.join("runtime").join(format!("{RUNTIME_DIR_PREFIX}{token}"));
        std::fs::create_dir_all(planted.parent().unwrap()).unwrap();
        symlink(&decoy, &planted).unwrap();

        let error = RuntimeLayout::create_with_token(&user_data, &token)
            .err()
            .expect("a planted symlink at the runtime name must refuse");
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
        assert!(error.to_string().contains("ownership mismatch"), "{error}");
        // The refusal must not have journaled ownership into the decoy.
        assert!(
            !decoy.join(JOURNAL_NAME).exists(),
            "the decoy must stay journal-free: {error}"
        );

        let _ = std::fs::remove_dir_all(&user_data);
        let _ = std::fs::remove_dir_all(&elsewhere);
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

            // #311: a junction whose target is INSIDE the runtime root
            // defeats the canonical containment check by construction — the
            // entry file-type guard is the only defense left, so pin it
            // explicitly. If the guard regressed, sweeping the planted entry
            // would follow the link and delete the in-root target's journal.
            let inside = user_data.join("runtime").join("inside-target");
            std::fs::create_dir_all(&inside).unwrap();
            std::fs::write(
                inside.join(JOURNAL_NAME),
                serde_json::json!({"version": 1, "token": "8".repeat(32), "state": "stopped"}).to_string(),
            )
            .unwrap();
            let planted_inside = user_data
                .join("runtime")
                .join(format!("{RUNTIME_DIR_PREFIX}{}", "8".repeat(32)));
            let output = std::process::Command::new("cmd")
                .args(["/C", "mklink", "/J"])
                .arg(&planted_inside)
                .arg(&inside)
                .output()
                .expect("run mklink /J for the in-root junction");
            assert!(
                output.status.success(),
                "mklink /J (in-root) failed: {}",
                String::from_utf8_lossy(&output.stderr)
            );
            sweep_runtime_dirs_with(&user_data, 0).unwrap();
            assert!(
                inside.join(JOURNAL_NAME).exists(),
                "in-root junction target must survive the sweep (file-type guard)"
            );
            let _ = std::fs::remove_dir(&planted_inside);
            assert!(inside.join(JOURNAL_NAME).exists());
        }
        let _ = std::fs::remove_dir_all(&user_data);
        let _ = std::fs::remove_dir_all(&outside);
    }
}

    /// The containment guard (#458): a symlink planted at the exact runtime
    /// name whose TARGET uses the same leaf (an attacker-chosen subtree with
    /// a pre-built `mangaflow-desktop-<token>` directory) passes the leaf
    /// check — the session tree must additionally live INSIDE the resolved
    /// user-data root, or the journal (and everything the helper writes
    /// under the session dir) is relocated to an attacker-chosen subtree.
    #[cfg(unix)]
    #[test]
    fn runtime_layout_refuses_a_same_leaf_target_outside_the_user_data_root() {
        use std::os::unix::fs::symlink;

        let user_data = std::env::temp_dir().join(format!(
            "mangaflow-layout-contain-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&user_data);
        let outside_root = std::env::temp_dir().join(format!(
            "mangaflow-layout-outside-{}-{}",
            std::process::id(),
            new_token()
        ));
        let _ = std::fs::remove_dir_all(&outside_root);

        let token = new_token();
        // The attacker pre-builds a same-leaf directory outside the root.
        let outside = outside_root.join(format!("{RUNTIME_DIR_PREFIX}{token}"));
        std::fs::create_dir_all(&outside).unwrap();
        // ... and plants a symlink at the runtime name pointing at it.
        let planted = user_data.join("runtime").join(format!("{RUNTIME_DIR_PREFIX}{token}"));
        std::fs::create_dir_all(planted.parent().unwrap()).unwrap();
        symlink(&outside, &planted).unwrap();

        let error = RuntimeLayout::create_with_token(&user_data, &token)
            .err()
            .expect("a same-leaf target outside the user-data root must refuse");
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
        assert!(error.to_string().contains("user-data root"), "{error}");
        // The refusal must not have journaled ownership into the outside tree.
        assert!(
            !outside.join(JOURNAL_NAME).exists(),
            "the outside tree must stay journal-free: {error}"
        );

        let _ = std::fs::remove_dir_all(&user_data);
        let _ = std::fs::remove_dir_all(&outside_root);
    }
