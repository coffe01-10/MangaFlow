//! Shell-side orchestration of the frozen ADR startup protocol:
//! spawn helper → read READY line → verify token/PID/journal → send GO →
//! poll loopback health → only then is the shell allowed to create the
//! WebView and inject the API origin.

use std::io::{BufRead, BufReader, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::Duration;

use crate::ownership::{OwnedTree, OwnershipError};
use crate::protocol::{
    verify_journal, verify_ready_line, ReadyPayload, RuntimeLayout, VerifyError, GO_PREFIX,
    HEALTH_PATH,
};

#[derive(Debug, Clone)]
pub struct HelperConfig {
    /// Python interpreter that runs the helper script.
    pub python: PathBuf,
    /// Path to `sidecar/mangaflow_poc_helper.py`.
    pub helper_script: PathBuf,
    /// Extra helper arguments after the mode, e.g. ["app", "--api-root", …].
    pub helper_args: Vec<String>,
    /// Overall deadline for the READY line (ADR cold-start budget ≤ 15s).
    pub ready_timeout: Duration,
    /// Deadline for the first successful loopback health response after GO.
    pub health_timeout: Duration,
}

impl HelperConfig {
    pub fn stub(python: &Path, helper_script: &Path) -> HelperConfig {
        HelperConfig {
            python: python.to_path_buf(),
            helper_script: helper_script.to_path_buf(),
            helper_args: vec!["stub".into()],
            ready_timeout: Duration::from_secs(20),
            health_timeout: Duration::from_secs(10),
        }
    }
}

pub struct SpawnedHelper {
    pub tree: OwnedTree,
    pub ready: ReadyPayload,
    pub layout: RuntimeLayout,
}

/// Run the full handshake. On success the helper is verified, serving, and
/// owned; the caller may create the WebView and inject `ready.api_origin`.
pub fn spawn_helper(config: &HelperConfig, user_data: &Path) -> Result<SpawnedHelper, SpawnError> {
    let layout = RuntimeLayout::create(user_data)?;
    let mut command = Command::new(&config.python);
    command
        .arg(&config.helper_script)
        .args(&config.helper_args)
        .env("MANGAFLOW_POC_TOKEN", &layout.token)
        .env("MANGAFLOW_POC_JOURNAL", layout.journal_path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());

    let mut tree = OwnedTree::spawn(command)?;
    let stdout = tree.child.stdout.take().expect("piped stdout");

    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let mut lines = BufReader::new(stdout).lines();
        match lines.next() {
            Some(Ok(line)) => {
                let _ = sender.send(Ok(line));
            }
            Some(Err(error)) => {
                let _ = sender.send(Err(error));
            }
            None => {
                let _ = sender.send(Err(std::io::Error::new(
                    std::io::ErrorKind::UnexpectedEof,
                    "helper closed stdout before publishing readiness",
                )));
            }
        }
    });

    let line = receiver
        .recv_timeout(config.ready_timeout)
        .map_err(|_| SpawnError::ReadyTimeout)?
        .map_err(SpawnError::Io)?;
    let ready = verify_ready_line(&line, &layout.token, tree.pid()).map_err(SpawnError::Verify)?;
    verify_journal(&layout.journal_path(), &ready).map_err(SpawnError::Verify)?;

    let stdin = tree.child.stdin.as_mut().expect("piped stdin");
    writeln!(stdin, "{GO_PREFIX}{}", layout.token).map_err(SpawnError::Io)?;
    stdin.flush().map_err(SpawnError::Io)?;

    wait_for_health(&ready.api_origin, config.health_timeout)
        .map_err(|_| SpawnError::HealthTimeout)?;
    Ok(SpawnedHelper {
        tree,
        ready,
        layout,
    })
}

#[derive(Debug)]
pub enum SpawnError {
    Io(std::io::Error),
    ReadyTimeout,
    HealthTimeout,
    Verify(VerifyError),
    Ownership(OwnershipError),
}

impl From<OwnershipError> for SpawnError {
    fn from(error: OwnershipError) -> Self {
        SpawnError::Ownership(error)
    }
}

impl From<std::io::Error> for SpawnError {
    fn from(error: std::io::Error) -> Self {
        SpawnError::Io(error)
    }
}

/// Minimal HTTP/1.0 GET over std TCP — enough for the loopback health gate
/// without pulling an HTTP client dependency into the PoC.
pub fn get_status(origin: &str, path: &str, timeout: Duration) -> std::io::Result<(u16, String)> {
    let authority = origin.trim_start_matches("http://");
    let mut stream = TcpStream::connect(authority)?;
    stream.set_read_timeout(Some(timeout))?;
    stream.set_write_timeout(Some(timeout))?;
    write!(
        stream,
        "GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
    )?;
    let mut response = String::new();
    std::io::Read::read_to_string(&mut BufReader::new(stream), &mut response)?;
    let status: u16 = response
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse().ok())
        .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::InvalidData, "bad status line"))?;
    Ok((status, response))
}

fn wait_for_health(origin: &str, timeout: Duration) -> std::io::Result<()> {
    let deadline = std::time::Instant::now() + timeout;
    loop {
        if let Ok((200, _)) = get_status(origin, HEALTH_PATH, Duration::from_secs(2)) {
            return Ok(());
        }
        if std::time::Instant::now() >= deadline {
            return Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                "loopback health check did not become ready",
            ));
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}
