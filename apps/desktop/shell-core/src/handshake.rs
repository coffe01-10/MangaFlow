//! Shell-side orchestration of the frozen ADR startup protocol:
//! spawn helper → read READY line → verify token/PID/journal → send GO →
//! poll loopback health → only then is the shell allowed to create the
//! WebView and inject the API origin.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::Duration;

use crate::logs::{helper_log_path, logs_dir, open_append_regular, RunLog};
use crate::ownership::{OwnedTree, OwnershipError};
use crate::protocol::{
    verify_journal, verify_ready_line_where, ReadyPayload, RuntimeLayout, VerifyError, GO_PREFIX,
    HEALTH_PATH,
};

/// Upper bound for one stdout protocol line and one health response body.
/// The writer on both channels is the shell's own helper, but "a third-party
/// library echoes a huge blob to stdout" is a realistic non-adversarial
/// trigger; the cap turns that into a failed verification or a truncated
/// (status-line-only-relevant) response instead of an unbounded String.
pub(crate) const MAX_STREAM_MESSAGE_BYTES: u64 = 64 * 1024;

#[derive(Debug, Clone)]
pub struct HelperConfig {
    /// Python interpreter that runs the helper script.
    pub python: PathBuf,
    /// Path to `sidecar/mangaflow_desktop_helper.py`.
    pub helper_script: PathBuf,
    /// Extra helper arguments after the mode, e.g. ["app", "--api-root", …].
    pub helper_args: Vec<String>,
    /// Overall deadline for the READY line. The ADR cold-start budget is
    /// ≤ 15s; the default here is deliberately looser (20s) so a slow but
    /// healthy start fails the budget in the measured numbers, not by
    /// aborting the handshake.
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

/// Force the helper's Python stdio/stderr encoding to UTF-8 regardless of
/// the host's ANSI codepage (#150): on zh-CN Windows the default for
/// redirected streams is GBK, which would mix encodings inside the
/// otherwise-UTF-8 log tree the exporter ships. Kept as its own function so
/// the encoding contract is unit-testable without spawning anything.
pub(crate) fn force_helper_utf8(command: &mut Command) {
    command.env("PYTHONUTF8", "1");
    command.env("PYTHONIOENCODING", "utf-8");
}

/// Record a shell milestone, surfacing a failure on stderr instead of
/// discarding it (#150). stderr — not the logger itself — is the only
/// channel a failing logging system may use without recursing into itself.
fn try_record(log: &RunLog, event: &str, fields: &serde_json::Value) {
    if let Err(error) = log.record(event, fields) {
        eprintln!("mangaflow-desktop: run log '{event}' milestone failed: {error}");
    }
}

pub struct SpawnedHelper {
    pub tree: OwnedTree,
    pub ready: ReadyPayload,
    pub layout: RuntimeLayout,
    /// Per-run shell milestone log (identity fields only).
    pub log: RunLog,
}

/// Run the full handshake. On success the helper is verified, serving, and
/// owned; the caller may create the WebView and inject `ready.api_origin`.
/// On failure the child (if any) is torn down with the same fail-closed
/// stop its `OwnedTree` drop performs, and — from the moment the run log
/// exists — every failure path records the run's terminal state (RunLog
/// "stopped" milestone + ownership-journal `mark_stopped`), so an aborted
/// handshake never leaves `owner.json` claiming state "ready" for a run the
/// shell just killed.
pub fn spawn_helper(config: &HelperConfig, user_data: &Path) -> Result<SpawnedHelper, SpawnError> {
    let layout = RuntimeLayout::create(user_data)?;
    let run_log = RunLog::create(user_data, &layout.token)?;
    // The helper's stderr — which carries uvicorn/API/Worker output in the
    // desktop form — lands in the unified logs directory instead of being
    // inherited from a console a GUI shell does not have (ADR §4.5).
    // open_append_regular refuses non-regular entries planted at the path
    // and any path that does not resolve inside the canonical logs root.
    // In-session rotation is deliberately NOT attempted here: the helper
    // process owns this handle for its lifetime, so helper stderr rotates
    // across sessions (the RunLog::create sweep above, see logs.rs).
    // Resolve the canonical logs root BEFORE opening the helper's stderr
    // file: it is the root `open_append_regular` compares the resolved
    // helper-log path against. This is still a pre-spawn step (`OwnedTree`
    // does not exist yet), so a failure here must do the same terminal
    // bookkeeping as the open itself failing below — record "stopped" with
    // no exit code and stop nothing — honoring the doc contract that every
    // failure path from the moment the run log exists records the run's
    // terminal state. The bare `?` this replaces returned without any
    // record even though `RunLog::create` had already succeeded.
    let canonical_logs_root = match logs_dir(user_data).canonicalize() {
        Ok(root) => root,
        Err(error) => {
            record_stopped(&run_log, &layout, None);
            return Err(SpawnError::Io(error));
        }
    };
    let helper_stderr = match open_append_regular(
        &helper_log_path(user_data, &layout.token),
        &canonical_logs_root,
    ) {
        Ok(stderr) => stderr,
        Err(error) => {
            record_stopped(&run_log, &layout, None);
            return Err(SpawnError::Io(error));
        }
    };
    let mut command = Command::new(&config.python);
    command
        .arg(&config.helper_script)
        .args(&config.helper_args)
        .env("MANGAFLOW_DESKTOP_TOKEN", &layout.token)
        .env("MANGAFLOW_DESKTOP_JOURNAL", layout.journal_path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(helper_stderr));
    force_helper_utf8(&mut command);
    try_record(
        &run_log,
        "spawn",
        &serde_json::json!({ "token": layout.token }),
    );

    let mut tree = match OwnedTree::spawn(command) {
        Ok(tree) => tree,
        Err(error) => {
            // No child ever came up (a Windows spawn failure has already
            // killed the still-suspended child); only the terminal records
            // of a run that never started are missing.
            record_stopped(&run_log, &layout, None);
            return Err(error.into());
        }
    };
    let stdout = tree.child.stdout.take().expect("piped stdout");

    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        // Only the FIRST line is protocol; the thread then parks reading to
        // EOF so the pipe's read end stays open for the child's whole
        // lifetime. If the thread exited after the READY line (the old
        // behavior), its drop of the only read end would break the pipe: any
        // later helper stdout write — a third-party library printing during
        // import, a stray print — would hit EPIPE and could kill an otherwise
        // healthy helper. Post-handshake output is drained and discarded:
        // the desktop helper logs to stderr, stdout carries the protocol.
        // Every line is read through a byte cap: a newline-free garbage
        // stream (a third-party library echoing a huge blob) must cost
        // bounded memory, not an unbounded String. A protocol line is far
        // below the cap, so a truncation can only ever fail verification.
        let first = {
            let mut line = String::new();
            let read = reader
                .by_ref()
                .take(MAX_STREAM_MESSAGE_BYTES)
                .read_line(&mut line);
            match read {
                Ok(0) => Err(std::io::Error::new(
                    std::io::ErrorKind::UnexpectedEof,
                    "helper closed stdout before publishing readiness",
                )),
                Ok(_) => Ok(line),
                Err(error) => Err(error),
            }
        };
        let _ = sender.send(first);
        loop {
            let mut junk = String::new();
            match reader
                .by_ref()
                .take(MAX_STREAM_MESSAGE_BYTES)
                .read_line(&mut junk)
            {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
        }
    });

    // From here on the child exists: every failure path tears it down and
    // records the terminal state via `abort_spawn` BEFORE the original
    // error is returned. The verification calls and their order are
    // unchanged — only the error routing around them is new.
    let line = match receiver.recv_timeout(config.ready_timeout) {
        Ok(Ok(line)) => line,
        Ok(Err(error)) => {
            return Err(abort_spawn(tree, &layout, &run_log, SpawnError::Io(error)))
        }
        Err(_) => return Err(abort_spawn(tree, &layout, &run_log, SpawnError::ReadyTimeout)),
    };
    // PID identity: the announcer must be a process this shell owns. On
    // Windows a launcher-style venv python (CPython 3.12) re-execs through a
    // child, so accept the direct child PID or any PID inside the root Job;
    // on Unix the direct child only (exec preserves the PID).
    let direct_pid = tree.pid();
    let ready = match verify_ready_line_where(&line, &layout.token, |pid| {
        pid == direct_pid || tree.contains_pid(pid)
    }) {
        Ok(ready) => ready,
        Err(error) => return Err(abort_spawn(tree, &layout, &run_log, SpawnError::Verify(error))),
    };
    match verify_journal(&layout.journal_path(), &ready) {
        Ok(()) => {}
        Err(error) => return Err(abort_spawn(tree, &layout, &run_log, SpawnError::Verify(error))),
    }
    try_record(
        &run_log,
        "ready_verified",
        &serde_json::json!({ "pid": ready.pid, "port": ready.port }),
    );

    let stdin = tree.child.stdin.as_mut().expect("piped stdin");
    let go = writeln!(stdin, "{GO_PREFIX}{}", layout.token).and_then(|()| stdin.flush());
    if let Err(error) = go {
        return Err(abort_spawn(tree, &layout, &run_log, SpawnError::Io(error)));
    }
    try_record(&run_log, "go_sent", &serde_json::json!({}));

    if wait_for_health(&ready.api_origin, config.health_timeout).is_err() {
        // The helper already published state "ready" in its journal; without
        // this path the shell would kill it and leave owner.json claiming
        // "ready" for a dead run.
        return Err(abort_spawn(
            tree,
            &layout,
            &run_log,
            SpawnError::HealthTimeout,
        ));
    }
    try_record(&run_log, "healthy", &serde_json::json!({}));
    Ok(SpawnedHelper {
        tree,
        ready,
        layout,
        log: run_log,
    })
}

/// Terminal bookkeeping shared by every handshake failure path from the
/// moment the run log exists: the RunLog "stopped" milestone and the
/// ownership journal's `mark_stopped` — the same records the desktop
/// shell's `stop_helper` writes on shutdown. A failure surfaces on stderr
/// (#150), never recursively through the logger.
fn record_stopped(run_log: &RunLog, layout: &RuntimeLayout, exit_code: Option<i32>) {
    try_record(
        run_log,
        "stopped",
        &serde_json::json!({ "exit_code": exit_code }),
    );
    if let Err(error) = layout.mark_stopped(exit_code) {
        eprintln!("mangaflow-desktop: marking the ownership journal stopped failed: {error}");
    }
}

/// Tear a half-spawned run down and record its terminal state before the
/// original handshake error is returned. The stop itself is IDENTICAL to
/// what `OwnedTree`'s drop performed on the old `?` propagation — the same
/// cooperative-then-escalating stop with drop's 3s grace — so the kill
/// semantics are unchanged; only the records are new. (After an explicit
/// stop the drop sees an already-reaped child and adds no second stop; the
/// job handle still closes, killing anything that escaped.)
fn abort_spawn(
    mut tree: OwnedTree,
    layout: &RuntimeLayout,
    run_log: &RunLog,
    error: SpawnError,
) -> SpawnError {
    let exit_code = tree.stop(Duration::from_secs(3)).ok().flatten();
    record_stopped(run_log, layout, exit_code);
    error
}

#[derive(Debug)]
pub enum SpawnError {
    Io(std::io::Error),
    ReadyTimeout,
    HealthTimeout,
    Verify(VerifyError),
    Ownership(OwnershipError),
}

impl std::fmt::Display for SpawnError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SpawnError::Io(error) => write!(f, "spawn helper I/O 失败：{error}"),
            SpawnError::ReadyTimeout => write!(f, "helper 未在预算内宣布 READY"),
            SpawnError::HealthTimeout => write!(f, "GO 后健康探活未在预算内通过"),
            SpawnError::Verify(error) => write!(f, "READY 校验失败：{error}"),
            SpawnError::Ownership(error) => write!(f, "进程树所有权建立失败：{error}"),
        }
    }
}

impl std::error::Error for SpawnError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            SpawnError::Io(error) => Some(error),
            SpawnError::Verify(error) => Some(error),
            SpawnError::Ownership(error) => Some(error),
            _ => None,
        }
    }
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
/// without pulling an HTTP client dependency into the shell core. The
/// response is read through a byte cap: only the status line is parsed, so a
/// peer that streams without end costs bounded memory, not an unbounded
/// String (read_to_string would otherwise buffer the whole body).
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
    (&mut BufReader::new(stream))
        .take(MAX_STREAM_MESSAGE_BYTES)
        .read_to_string(&mut response)?;
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

#[cfg(test)]
mod tests {
    use super::*;

    /// Red team 2026-09-08: the health response read is byte-capped. Only
    /// the status line is parsed, so a peer streaming a huge body must yield
    /// a truncated bounded response (with the status still parsed), never an
    /// unbounded String.
    #[test]
    fn get_status_caps_the_response_read() {
        use std::io::Write as _;
        use std::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").expect("bind loopback");
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut sock, _) = listener.accept().expect("accept");
            let _ = sock.write_all(b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\n");
            let chunk = [b'A'; 8192];
            for _ in 0..64 {
                if sock.write_all(&chunk).is_err() {
                    break;
                }
            }
            let _ = sock.flush();
            // The socket drops here: HTTP/1.0 + Connection: close means EOF
            // ends the read even though the server never consumed a request
            // body boundary.
        });

        let (status, body) =
            get_status(&format!("http://127.0.0.1:{port}"), HEALTH_PATH, Duration::from_secs(5))
                .expect("health read succeeds");
        assert_eq!(status, 200);
        assert!(
            (body.len() as u64) <= MAX_STREAM_MESSAGE_BYTES,
            "response must be capped at {MAX_STREAM_MESSAGE_BYTES}, got {}",
            body.len()
        );
        let _ = server.join();
    }

    /// #150 regression: the helper command must force UTF-8 stdio encoding
    /// (`PYTHONUTF8` + `PYTHONIOENCODING`) so its stderr never mixes GBK
    /// bytes into the UTF-8 log tree on an ANSI-codepage Windows host.
    #[test]
    fn helper_command_forces_utf8_stdio_environment() {
        let mut command = Command::new("irrelevant-binary");
        force_helper_utf8(&mut command);
        let envs: std::collections::BTreeMap<&std::ffi::OsStr, &std::ffi::OsStr> = command
            .get_envs()
            .filter_map(|(key, value)| value.map(|value| (key, value)))
            .collect();
        assert_eq!(
            envs.get(std::ffi::OsStr::new("PYTHONUTF8")),
            Some(&std::ffi::OsStr::new("1")),
            "{envs:?}"
        );
        assert_eq!(
            envs.get(std::ffi::OsStr::new("PYTHONIOENCODING")),
            Some(&std::ffi::OsStr::new("utf-8")),
            "{envs:?}"
        );
    }
}
