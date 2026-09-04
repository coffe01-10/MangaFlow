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
    let payload = line
        .strip_prefix(READY_PREFIX)
        .ok_or(VerifyError::BadLine)?;
    let value: serde_json::Value =
        serde_json::from_str(payload).map_err(|_| VerifyError::BadJson)?;
    if value["token"].as_str() != Some(token) {
        return Err(VerifyError::TokenMismatch);
    }
    let pid = value["pid"].as_u64().ok_or(VerifyError::BadJson)? as u32;
    if pid != expected_pid {
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
    Ok(ReadyPayload {
        token: token.to_string(),
        pid,
        api_origin: origin,
        port,
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
    /// the canonical-path checks of `scripts/owned_processes.py`.
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
        Ok(RuntimeLayout {
            user_data: user_data.to_path_buf(),
            token,
        })
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
        std::fs::write(&journal, serde_json::to_string(&value).unwrap())?;
        Ok(())
    }
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
}
