//! Integration tests for the desktop startup protocol and process-tree
//! ownership, run NATIVELY on both supported legs: the documented Linux
//! `cargo test` sandbox and a Windows machine. Platform-divergent runtime
//! behavior is cfg-gated inline with the reason stated at each gate:
//! - `proc_alive` reads `/proc/<pid>` on Unix and `tasklist` on Windows;
//! - `python()` (tests/common) defaults to `python` on Windows because
//!   `python3` resolves to the Microsoft Store app-execution stub there;
//! - the Windows-only assertions cover the Job Object escalation (job exit
//!   code 125) and the stdin-EOF cooperative stop channel: on Unix,
//!   `stop()` group-SIGTERMs the tree microseconds after spawn, which
//!   races any Python stand-in's interpreter bootstrap before it can
//!   install its SIGTERM handling (see
//!   `stdin_close_is_a_cooperative_stop_channel`).

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

mod common;

use mangaflow_desktop_shell_core::handshake::{spawn_helper, HelperConfig, SpawnError};
use mangaflow_desktop_shell_core::logs::shell_log_path;
use mangaflow_desktop_shell_core::ownership::{OwnedTree, OwnershipError};
use mangaflow_desktop_shell_core::protocol::{
    verify_ready_line, VerifyError, GO_PREFIX, HEALTH_PATH,
};

use common::python;

fn helper_script() -> PathBuf {
    let from_env = std::env::var("MANGAFLOW_DESKTOP_HELPER").map(PathBuf::from);
    from_env.unwrap_or_else(|_| {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../sidecar/mangaflow_desktop_helper.py")
    })
}

fn temp_user_data(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("mangaflow-desktop-test-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// Resolve the owned runtime entry's owner.json under
/// `<user_data>/runtime/` (the fixture guarantees exactly one owned
/// runtime entry per user_data root — resolving it once keeps the pid and
/// state reads on the SAME journal).
fn newest_journal_path(user_data: &Path) -> Option<std::path::PathBuf> {
    let runtime = user_data.join("runtime");
    let entry = std::fs::read_dir(&runtime)
        .ok()?
        .filter_map(|entry| entry.ok())
        .find(|entry| entry.path().join("owner.json").is_file())?
        .path();
    Some(entry.join("owner.json"))
}

fn proc_alive(pid: u32) -> bool {
    #[cfg(unix)]
    {
        Path::new(&format!("/proc/{pid}")).exists()
    }
    #[cfg(windows)]
    {
        // `tasklist` enumerates running processes only. Match the PID as an
        // exact whitespace token: the /NH (no header) rows are name, PID,
        // session, mem usage — a substring match could hit a memory column
        // like "38,040 K", a token match cannot.
        std::process::Command::new("tasklist")
            .args(["/FI", format!("PID eq {pid}").as_str(), "/NH"])
            .output()
            .map(|output| {
                String::from_utf8_lossy(&output.stdout)
                    .split_whitespace()
                    .any(|token| token == pid.to_string())
            })
            .unwrap_or(false)
    }
}

fn wait_until_gone(pid: u32, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if proc_dead(pid) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    proc_dead(pid)
}

/// Gone means absent from /proc OR a zombie: a SIGKILLed orphan re-parents
/// to the init process and lingers unreaped in containers whose init never
/// reaps, so bare existence reads as alive when the process is already dead.
#[cfg(unix)]
fn proc_dead(pid: u32) -> bool {
    // Gone means absent from /proc OR a zombie: a SIGKILLed orphan
    // re-parents to the init process and lingers unreaped in containers
    // whose init never reaps, so bare existence reads as alive when the
    // process is already dead.
    let Ok(text) = std::fs::read_to_string(format!("/proc/{pid}/stat")) else {
        return true;
    };
    match text
        .rsplit(')')
        .next()
        .and_then(|rest| rest.split_whitespace().next())
    {
        Some(state) => state == "Z",
        None => true,
    }
}

#[cfg(windows)]
fn proc_dead(pid: u32) -> bool {
    !proc_alive(pid)
}

#[test]
fn handshake_go_health_and_clean_stop_leaves_no_residue() {
    let user_data = temp_user_data("clean-stop");
    #[cfg_attr(windows, allow(unused_mut))] // the push below is Unix-only
    let mut config = HelperConfig::stub(&python(), &helper_script());
    // The helper's test descendant is a Unix-only facility (PDEATHSIG chain);
    // on Windows the equivalent coverage is the Job Object crash test below.
    #[cfg(unix)]
    config.helper_args.push("--grandchild".into());

    let mut spawned = spawn_helper(&config, &user_data).expect("handshake must complete");
    let helper_pid = spawned.tree.pid();

    // Journal is owned by the shell; before stop it still reads "ready".
    let journal = std::fs::read_to_string(spawned.layout.journal_path()).unwrap();
    let journal: serde_json::Value = serde_json::from_str(&journal).unwrap();
    assert_eq!(journal["state"], "ready");
    #[cfg(unix)]
    let grandchild_pid = journal["grandchild_pid"]
        .as_u64()
        .expect("stub --grandchild records its descendant");

    // The cooperative stop channel end-to-end: closing stdin trips the
    // helper's EOF watcher (self-SIGTERM, sys.exit(0)) — on Windows this is
    // the ONLY cooperative path, so Some(0) proves it worked within grace.
    let exit_code = spawned
        .tree
        .stop(Duration::from_secs(5))
        .expect("stop succeeds");
    assert_eq!(exit_code, Some(0));

    assert!(
        wait_until_gone(helper_pid, Duration::from_secs(5)),
        "helper must be gone"
    );
    #[cfg(unix)]
    assert!(
        wait_until_gone(grandchild_pid as u32, Duration::from_secs(5)),
        "descendant must be gone (process-group kill)"
    );

    spawned.layout.mark_stopped(exit_code).unwrap();
    let journal: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(spawned.layout.journal_path()).unwrap())
            .unwrap();
    assert_eq!(journal["state"], "stopped");
    assert!(
        journal["token"].as_str().is_some(),
        "journal keeps identity, never secrets"
    );

    let _ = std::fs::remove_dir_all(&user_data);
}

/// Windows launcher-interpreter regression: a `python.exe` that re-execs the
/// real interpreter as a child (CPython 3.12 venv style) publishes a READY
/// pid that is a grandchild of the spawned process. The handshake must
/// accept it through Job Object membership (the announcer is still inside
/// the shell's kill boundary) — exact-PID equality alone would fail closed
/// and make dev-mode desktop shells impossible on such interpreters. The
/// fake launcher mirrors the venv shape: direct child spawns the real stub
/// helper and proxies its stdio.
#[test]
#[cfg(windows)]
fn ready_pid_from_a_launcher_interpreter_chain_is_accepted_via_job_membership() {
    let user_data = temp_user_data("trampoline");
    let launcher_code = "import subprocess,sys; \
         raise SystemExit(subprocess.run([sys.executable]+sys.argv[1:]).returncode)";
    let mut config = HelperConfig::stub(&python(), &std::path::PathBuf::from("-c"));
    config.helper_args = vec![
        launcher_code.into(),
        helper_script().to_string_lossy().into_owned(),
        "stub".into(),
    ];

    let mut spawned = spawn_helper(&config, &user_data)
        .expect("handshake must accept the launcher chain pid");
    let direct_pid = spawned.tree.pid();
    assert_ne!(
        spawned.ready.pid, direct_pid,
        "the fixture is meaningless unless the announcer is not the direct child"
    );
    assert!(
        spawned.tree.contains_pid(spawned.ready.pid),
        "the announcer must be inside the shell's Job"
    );

    // The cooperative stop still ends the whole chain: stdin EOF reaches the
    // grandchild through the proxying launcher.
    let exit_code = spawned
        .tree
        .stop(Duration::from_secs(10))
        .expect("stop succeeds");
    assert_eq!(exit_code, Some(0));
    assert!(wait_until_gone(direct_pid, Duration::from_secs(5)));
    assert!(wait_until_gone(spawned.ready.pid, Duration::from_secs(5)));
    let _ = std::fs::remove_dir_all(&user_data);
}

#[test]
fn concurrent_helpers_bind_distinct_dynamic_ports() {
    let user_data = temp_user_data("concurrent");
    let config = HelperConfig::stub(&python(), &helper_script());

    let mut first = spawn_helper(&config, &user_data).expect("first helper");
    let mut second = spawn_helper(&config, &user_data).expect("second helper");
    assert_ne!(
        first.ready.port, second.ready.port,
        "no TOCTOU port collision"
    );
    assert_ne!(first.ready.token, second.ready.token);

    let (status_first, _) = mangaflow_desktop_shell_core::handshake::get_status(
        &first.ready.api_origin,
        HEALTH_PATH,
        Duration::from_secs(2),
    )
    .unwrap();
    assert_eq!(status_first, 200);

    first.tree.stop(Duration::from_secs(5)).unwrap();
    second.tree.stop(Duration::from_secs(5)).unwrap();
    let _ = std::fs::remove_dir_all(&user_data);
}

#[test]
fn wrong_go_token_is_refused_without_serving() {
    let user_data = temp_user_data("refused");
    let mut command = Command::new(python());
    command
        .arg(helper_script())
        .arg("stub")
        .env("MANGAFLOW_DESKTOP_TOKEN", "ab".repeat(16))
        .env(
            "MANGAFLOW_DESKTOP_JOURNAL",
            user_data
                .join(format!("runtime/mangaflow-desktop-{}", "ab".repeat(16)))
                .join("owner.json"),
        );
    std::fs::create_dir_all(user_data.join(format!("runtime/mangaflow-desktop-{}", "ab".repeat(16))))
        .unwrap();
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());

    let mut tree = OwnedTree::spawn(command).unwrap();
    let mut stdout = tree.child.stdout.take().unwrap();
    let mut line = String::new();
    std::io::BufRead::read_line(&mut std::io::BufReader::new(&mut stdout), &mut line).unwrap();
    let ready = verify_ready_line(&line, &"ab".repeat(16), tree.pid()).expect("ready verified");

    // Shell sends a token that does not match this startup; helper must exit
    // 75 without ever serving /api/v1/health. Wait for the voluntary exit so
    // the assertion cannot race the process-group signal.
    let stdin = tree.child.stdin.as_mut().unwrap();
    writeln!(stdin, "{GO_PREFIX}{}", "f".repeat(32)).unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    let exit_code = loop {
        match tree.child.try_wait().expect("wait succeeds") {
            Some(status) => break status.code(),
            None if Instant::now() >= deadline => panic!("helper did not exit after refusal"),
            None => std::thread::sleep(Duration::from_millis(20)),
        }
    };
    assert_eq!(exit_code, Some(75), "handshake refusal must exit 75");
    assert!(
        mangaflow_desktop_shell_core::handshake::get_status(
            &ready.api_origin,
            HEALTH_PATH,
            Duration::from_millis(800)
        )
        .is_err(),
        "no traffic may be served after a refused handshake"
    );
    let _ = std::fs::remove_dir_all(&user_data);
}

#[test]
fn shell_crash_still_kills_helper_and_descendants() {
    let user_data = temp_user_data("crash");
    let sim = Command::new(env!("CARGO_BIN_EXE_shell-sim"))
        .env("MANGAFLOW_DESKTOP_PYTHON", python())
        .env("MANGAFLOW_DESKTOP_HELPER", helper_script())
        .env("MANGAFLOW_DESKTOP_USER_DATA", &user_data)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .unwrap();
    let output = sim.wait_with_output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("MANGAFLOW_SIM_OK"),
        "simulator must complete the handshake before crashing: {stdout}"
    );
    assert!(!output.status.success(), "simulator dies via abort()");

    // The crashed simulator never stopped the helper; ownership must.
    let runtime_dir = std::fs::read_dir(user_data.join("runtime"))
        .expect("runtime dir")
        .next()
        .unwrap()
        .unwrap()
        .path();
    let journal: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(runtime_dir.join("owner.json")).unwrap())
            .unwrap();
    let helper_pid = journal["pid"].as_u64().unwrap() as u32;
    // The test descendant only exists on Unix (helper-side win32 gate).
    #[cfg(unix)]
    let grandchild_pid = journal["grandchild_pid"].as_u64().unwrap() as u32;

    assert!(
        wait_until_gone(helper_pid, Duration::from_secs(5)),
        "helper must die with the shell"
    );
    #[cfg(unix)]
    assert!(
        wait_until_gone(grandchild_pid, Duration::from_secs(5)),
        "descendants must die through the PDEATHSIG chain"
    );
    let _ = std::fs::remove_dir_all(&user_data);
}

/// Regression (process-group ownership): `stop()` must kill descendants even
/// when the direct child NEVER puts itself into its own session. Before the
/// shell claimed the process group at spawn time (`process_group(0)`), the
/// group-kill `kill(-pid)` only worked after the helper's own `setsid()` had
/// run — a stop landing during a helper's interpreter bootstrap left
/// descendants in the shell's group where the group signal missed them and
/// the per-pid fallback orphaned them. This stand-in mirrors that shape
/// deliberately: `sh` backgrounds a grandchild and parks in `wait`, calling
/// no `setsid`/`setpgid` at all, and the grandchild inherits the TEST
/// HARNESS's group (so nothing but the child-owned group signal can reach
/// it — it is not a PDEATHSIG child of the helper either, `sh` cannot set
/// one). Unix-only: on Windows the Job Object covers the whole tree by
/// construction.
#[test]
#[cfg(unix)]
fn stop_kills_descendants_of_a_child_that_never_joins_its_own_group() {
    let user_data = temp_user_data("pgroup");
    let pid_file = user_data.join("grandchild.pid");
    let script = format!(
        "sleep 3600 & echo $! > {}; wait",
        pid_file.to_string_lossy()
    );
    let mut command = Command::new("sh");
    command.arg("-c").arg(&script);
    let mut tree = OwnedTree::spawn(command).unwrap();

    // Wait for the grandchild's PID to be published, then confirm the
    // shell-owned group covers the descendant from spawn time: the
    // grandchild's pgid is the tree root's pid, not the harness's group.
    let deadline = Instant::now() + Duration::from_secs(5);
    let grandchild_pid = loop {
        if let Ok(text) = std::fs::read_to_string(&pid_file) {
            if let Ok(pid) = text.trim().parse::<u32>() {
                break pid;
            }
        }
        assert!(Instant::now() < deadline, "grandchild pid never published");
        std::thread::sleep(Duration::from_millis(20));
    };
    let pgid_of = |pid: u32| -> u32 {
        std::fs::read_to_string(format!("/proc/{pid}/stat"))
            .unwrap()
            .rsplit(')')
            .next()
            .unwrap()
            .split_whitespace()
            .nth(2)
            .unwrap()
            .parse()
            .unwrap()
    };
    assert_eq!(
        pgid_of(grandchild_pid),
        tree.pid(),
        "the grandchild must live in the process group the shell owns"
    );

    let exit = tree.stop(Duration::from_secs(5)).expect("stop succeeds");
    assert_eq!(exit, None, "a signal death reports no exit code");
    assert!(wait_until_gone(tree.pid(), Duration::from_secs(5)), "sh must die");
    assert!(
        wait_until_gone(grandchild_pid, Duration::from_secs(5)),
        "the grandchild must die with the tree (group signal), not be orphaned"
    );
    let _ = std::fs::remove_dir_all(&user_data);
}

/// A helper that exits on its own while its descendants live must not
/// orphan them: the Drop path still owns the process group after the child
/// is gone. This is the crash shape of plan B — the helper dies, its node
/// server lives on — and Unix has no KILL_ON_JOB_CLOSE analog. Red-green:
/// without the Drop group-kill the grandchild outlives the tree.
#[cfg(unix)]
#[test]
fn drop_kills_descendants_after_the_child_exited_on_its_own() {
    let user_data = temp_user_data("orphan");
    let pid_file = user_data.join("grandchild.pid");
    // The grandchild's stdout/stderr are redirected: an orphan holding the
    // test harness's pipe open would hang the whole suite after the run.
    let script = format!(
        "sleep 3600 >/dev/null 2>&1 & echo $! > {}; exit 0",
        pid_file.to_string_lossy()
    );
    let mut command = Command::new("sh");
    command.arg("-c").arg(&script);
    let tree = OwnedTree::spawn(command).unwrap();

    let deadline = Instant::now() + Duration::from_secs(5);
    let grandchild_pid = loop {
        if let Ok(text) = std::fs::read_to_string(&pid_file) {
            if let Ok(pid) = text.trim().parse::<u32>() {
                break pid;
            }
        }
        assert!(Instant::now() < deadline, "grandchild pid never published");
        std::thread::sleep(Duration::from_millis(20));
    };
    // The child exits 0 right after publishing the grandchild. Until
    // `drop` reaps it, it lingers as a zombie — so "exited" here means
    // /proc state 'Z' (or fully gone), not mere existence.
    let process_state = |pid: u32| -> char {
        let text = std::fs::read_to_string(format!("/proc/{pid}/stat"))
            .expect("this unix test requires /proc (same as the pgid checks)");
        text.rsplit(')')
            .next()
            .unwrap()
            .split_whitespace()
            .next()
            .unwrap()
            .chars()
            .next()
            .unwrap()
    };
    let deadline = Instant::now() + Duration::from_secs(5);
    while process_state(tree.pid()) != 'Z' {
        assert!(
            Instant::now() < deadline,
            "the child must have exited on its own"
        );
        std::thread::sleep(Duration::from_millis(20));
    }

    drop(tree);
    assert!(
        wait_until_gone(grandchild_pid, Duration::from_secs(5)),
        "the grandchild must die with the group even after the child exited"
    );
    let _ = std::fs::remove_dir_all(&user_data);
}

/// stop() is idempotent: a second call after the first must report the
/// cached exit instead of hanging or erroring (the stdin take is a no-op,
/// the group signals hit a dead group, and try_wait returns the cached
/// status).
#[test]
fn stop_is_idempotent_and_reports_the_cached_exit() {
    let user_data = temp_user_data("twice");
    let mut command = Command::new(python());
    command
        .arg("-c")
        .arg(
            // Same shape as `stdin_close_is_a_cooperative_stop_channel`:
            // ignore SIGTERM during bootstrap so the group SIGTERM cannot
            // win the race against the interpreter reaching stdin.read();
            // the cooperative EOF exit code is what makes the cached-exit
            // equality meaningful (a signal death reports None).
            "import signal, sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); \
             sys.stdin.read(); sys.exit(7)",
        )
        .stdin(std::process::Stdio::piped());
    let mut tree = OwnedTree::spawn(command).unwrap();

    // Give the stand-in time to install its SIGTERM handler; stopping
    // during the interpreter bootstrap would kill it by signal (exit None)
    // — the documented bootstrap race of every Unix stop() test. Unix polls
    // the kernel's signal masks (bit 14 = signal 15, SIGTERM, in
    // SigIgn/SigCgt) instead of sleeping a fixed interval, so a loaded
    // machine cannot flake; Windows has no signal to race — stdin EOF is
    // the cooperative channel — so it needs no wait at all.
    #[cfg(unix)]
    let sigterm_catch_deadline = Instant::now() + Duration::from_secs(5);
    #[cfg(unix)]
    loop {
        // SIG_IGN lands in the IGNORED mask, a Python handler in the CAUGHT
        // mask — either one proves signal.signal(SIGTERM, …) has run.
        let installed = std::fs::read_to_string(format!("/proc/{}/status", tree.pid()))
            .ok()
            .and_then(|status| {
                let bit = |prefix: &str| {
                    status
                        .lines()
                        .find(|line| line.starts_with(prefix))
                        .and_then(|line| {
                            line.split_whitespace()
                                .nth(1)
                                .and_then(|mask| u64::from_str_radix(mask, 16).ok())
                        })
                        .unwrap_or(0)
                };
                Some((bit("SigIgn:") | bit("SigCgt:")) & (1 << 14) != 0)
            });
        if installed.unwrap_or(false) {
            break;
        }
        assert!(
            Instant::now() < sigterm_catch_deadline,
            "the stand-in never installed its SIGTERM handler"
        );
        std::thread::sleep(Duration::from_millis(20));
    }

    let first = tree.stop(Duration::from_secs(5)).expect("first stop");
    let second = tree.stop(Duration::from_secs(5)).expect("second stop");
    assert_eq!(first, Some(7), "the cooperative exit code is observed");
    assert_eq!(second, first, "the second stop reports the cached exit");
    assert!(!tree.alive());
    let _ = std::fs::remove_dir_all(&user_data);
}

/// The escalation half of `stop()`: a child no cooperative channel can
/// reach must still die, promptly, once the grace window elapses. The
/// stand-in never reads stdin and — once its interpreter finishes
/// bootstrapping — ignores SIGTERM, so every cooperative channel of
/// `stop()` is unavailable.
///
/// Which platform exercises which assertion:
/// - Windows (deterministic): no signal exists to race the bootstrap — the
///   cooperative phase is exactly "close stdin and wait", and a child that
///   never reads stdin cannot exit early — so the FULL grace must elapse
///   before `TerminateJobObject` kills the job with exit code 125. The
///   grace-floor and exit-code assertions below are Windows-gated.
/// - Unix (termination contract only): `stop()` group-SIGTERMs
///   microseconds after spawn, which typically lands while the Python
///   stand-in is still bootstrapping — before its
///   `signal.signal(SIGTERM, SIG_IGN)` instruction runs — so the child may
///   die to the default-disposition SIGTERM in ~0-40 ms instead of being
///   SIGKILLed after the grace window. (An `sh -c 'trap "" TERM; exec …'`
///   wrapper would only shrink, not close, its own bootstrap race, so it
///   cannot make the timing assertions deterministic either.) Both Unix
///   outcomes are acceptable: `stop()` returns Ok, the tree is dead, and a
///   signal death reports no exit code either way — those assertions stay
///   cross-platform, as does the anti-hang bound on the elapsed time.
#[test]
fn noncooperative_helper_is_forcefully_killed() {
    let user_data = temp_user_data("timeout");
    let mut command = Command::new(python());
    command.arg("-c").arg(
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(3600)",
    );
    let mut tree = OwnedTree::spawn(command).unwrap();
    let pid = tree.pid();

    let grace = Duration::from_millis(600);
    let started = Instant::now();
    let exit = tree.stop(grace).expect("forceful stop succeeds");
    let elapsed = started.elapsed();
    // The cooperative phase gets the full grace window before escalation…
    // but only Windows observes this deterministically (docstring above: on
    // Unix the immediate SIGTERM may kill the stand-in mid-bootstrap, long
    // before the grace deadline, through no fault of stop()).
    #[cfg(windows)]
    assert!(
        elapsed >= grace,
        "stop() must honor the grace window before escalating (took {elapsed:?})"
    );
    // …and the escalation (or, on Unix, the early cooperative-SIGTERM
    // death) must terminate the tree promptly — the anti-hang regression,
    // valid on both platforms and in both Unix outcomes.
    assert!(
        elapsed < grace + Duration::from_secs(5),
        "escalation after the deadline must be immediate (took {elapsed:?})"
    );
    // Windows: the escalation is TerminateJobObject, whose job exit code is
    // 125. Unix: whether the stand-in lost the bootstrap race to SIGTERM or
    // survived it and met the post-grace SIGKILL, a signal death reports no
    // exit code.
    #[cfg(unix)]
    assert_eq!(exit, None, "a signal death reports no exit code");
    #[cfg(windows)]
    assert_eq!(
        exit,
        Some(125),
        "TerminateJobObject escalation reports the job exit code"
    );
    assert!(wait_until_gone(pid, Duration::from_secs(5)));
    let _ = std::fs::remove_dir_all(&user_data);
}

/// The cooperative half of `stop()`: closing the child's piped stdin is the
/// graceful-shutdown trigger the Windows path depends on. This stand-in
/// ignores SIGTERM so ONLY the stdin-EOF channel can stop it, and exits 42
/// on EOF — a cooperative stop reports the child's own exit code, well
/// inside the grace window, with no escalation involved. The stdin MUST be
/// piped for this to exercise anything: without `.stdin(Stdio::piped())`
/// the child inherits the harness stdin (already at EOF under `cargo test`,
/// an open console interactively), `stop()`'s stdin close is a no-op, and
/// the test passes vacuously or hangs.
///
/// Windows-only: the stdin-EOF channel is by design the WINDOWS cooperative
/// path (a Job Object cannot deliver a signal). On Unix, `stop()` also
/// group-SIGTERMs the tree microseconds after spawn — before this Python
/// stand-in's interpreter bootstrap can reach its
/// `signal.signal(SIGTERM, SIG_IGN)` — so the child dies from the
/// default-disposition SIGTERM during bootstrap (exit `None`) instead of
/// exiting 42 via stdin EOF, and the Some(42) contract cannot be tested
/// there without racing the bootstrap.
#[test]
#[cfg(windows)]
fn stdin_close_is_a_cooperative_stop_channel() {
    let user_data = temp_user_data("stdin-eof");
    let mut command = Command::new(python());
    command
        .arg("-c")
        .arg(
            "import signal, sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); \
             sys.stdin.read(); sys.exit(42)",
        )
        .stdin(Stdio::piped());
    let mut tree = OwnedTree::spawn(command).unwrap();

    let started = Instant::now();
    let grace = Duration::from_secs(5);
    let exit = tree.stop(grace).expect("cooperative stop succeeds");
    // 42 can only be the child's own `sys.exit(42)` observed via the
    // cooperative stdin-EOF channel: the escalation paths report job exit
    // code 125 (Windows) or no code at all (Unix SIGKILL).
    assert_eq!(exit, Some(42), "stopped via stdin EOF, not by a kill");
    assert!(
        started.elapsed() < grace,
        "a cooperative exit must complete within the grace window (took {:?})",
        started.elapsed()
    );
    let _ = std::fs::remove_dir_all(&user_data);
}

/// Regression for the handshake stdout contract: after the READY line the
/// shell must keep DRAINING the child's stdout (not close its read end).
/// With the old first-line-only reader this child's post-handshake writes
/// hit a broken pipe, its main thread died mid-print, and the session lost
/// a perfectly healthy helper; here the health probe after several stdout
/// writes must still answer.
#[test]
fn post_ready_stdout_chatter_does_not_break_the_session() {
    let user_data = temp_user_data("stdout-drain");
    // A minimal stand-in helper implementing the frozen protocol: READY line
    // from a journal-correct record, GO gating, then the health server in a
    // background thread while the MAIN thread keeps printing to stdout —
    // exactly the shape that EPIPEs under a closed read end.
    let stand_in = r#"
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

token = os.environ["MANGAFLOW_DESKTOP_TOKEN"]
journal_path = os.environ["MANGAFLOW_DESKTOP_JOURNAL"]
try:
    starttime = int(open("/proc/self/stat").read().rsplit(")", 1)[1].split()[19])
except Exception:
    starttime = None  # non-Linux: the anchor is optional, verification skips it

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/v1/health":
            self.send_error(404)
            return
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
origin = "http://127.0.0.1:%d" % server.server_address[1]
record = {
    "version": 1,
    "token": token,
    "state": "ready",
    "pid": os.getpid(),
    **({"pid_starttime": starttime} if starttime is not None else {}),
    "api_origin": origin,
}
with open(journal_path, "w", encoding="utf-8") as handle:
    json.dump(record, handle)
print(
    "MANGAFLOW_READY "
    + json.dumps({"token": token, "pid": os.getpid(), "api_origin": origin}),
    flush=True,
)

line = sys.stdin.readline()
if line.strip() != "MANGAFLOW_GO " + token:
    server.server_close()
    raise SystemExit(75)

threading.Thread(target=server.serve_forever, daemon=True).start()
for index in range(100):
    print(f"post-ready stdout line {index}", flush=True)
    time.sleep(0.05)
"#;
    let stand_in_path = user_data.join("stand_in_helper.py");
    std::fs::write(&stand_in_path, stand_in).unwrap();
    let config = HelperConfig {
        python: python(),
        helper_script: stand_in_path,
        helper_args: vec![],
        ready_timeout: Duration::from_secs(20),
        health_timeout: Duration::from_secs(10),
    };

    let mut spawned = spawn_helper(&config, &user_data).expect("handshake must complete");
    // Several chatter lines have been written past the READY line by now;
    // the helper must still be alive and serving.
    std::thread::sleep(Duration::from_millis(300));
    let (status, _) = mangaflow_desktop_shell_core::handshake::get_status(
        &spawned.ready.api_origin,
        HEALTH_PATH,
        Duration::from_secs(2),
    )
    .expect("helper survives its own post-ready stdout writes");
    assert_eq!(status, 200);

    // The stand-in has no stdin watcher and no signal handler, so this stop
    // exercises the plain escalation on both platforms; only its success and
    // the survival assertion above matter here.
    spawned
        .tree
        .stop(Duration::from_millis(500))
        .expect("stop succeeds after the chatter");
    let _ = std::fs::remove_dir_all(&user_data);
}

/// Regression for the handshake-failure bookkeeping: a spawn whose health
/// gate times out AFTER the helper already published state "ready" must not
/// leave `owner.json` claiming "ready" for a run the shell is killing. The
/// stand-in announces an origin that never serves (loopback port 1), so
/// `spawn_helper` fails with `HealthTimeout`; the failure path must still
/// (a) kill the tree, (b) append the RunLog "stopped" milestone, and
/// (c) mark the ownership journal "stopped".
#[test]
fn health_timeout_failure_still_records_terminal_state_and_kills() {
    let user_data = temp_user_data("health-timeout");
    // A minimal protocol-correct stand-in: journal + READY line with a
    // loopback origin nothing serves, then park on stdin like the real
    // helper's EOF watcher.
    let stand_in = r#"
import json, os, sys
token = os.environ["MANGAFLOW_DESKTOP_TOKEN"]
journal_path = os.environ["MANGAFLOW_DESKTOP_JOURNAL"]
try:
    starttime = int(open("/proc/self/stat").read().rsplit(")", 1)[1].split()[19])
except Exception:
    starttime = None  # non-Linux: the anchor is optional, verification skips it
record = {
    "version": 1,
    "token": token,
    "state": "ready",
    "pid": os.getpid(),
    **({"pid_starttime": starttime} if starttime is not None else {}),
    "api_origin": "http://127.0.0.1:1",
}
with open(journal_path, "w", encoding="utf-8") as handle:
    json.dump(record, handle)
print(
    "MANGAFLOW_READY "
    + json.dumps({"token": token, "pid": os.getpid(), "api_origin": "http://127.0.0.1:1"}),
    flush=True,
)
sys.stdin.read()
"#;
    let stand_in_path = user_data.join("stand_in_no_health.py");
    std::fs::write(&stand_in_path, stand_in).unwrap();
    let config = HelperConfig {
        python: python(),
        helper_script: stand_in_path,
        helper_args: vec![],
        ready_timeout: Duration::from_secs(20),
        health_timeout: Duration::from_secs(1),
    };

    let error = match spawn_helper(&config, &user_data) {
        Ok(_) => panic!("health gate must time out"),
        Err(error) => error,
    };
    assert!(
        matches!(error, SpawnError::HealthTimeout),
        "unexpected error: {error:?}"
    );

    // Exactly one owned run exists; its journal must no longer claim
    // "ready" for a run that is being torn down.
    let runtime_dir = std::fs::read_dir(user_data.join("runtime"))
        .expect("runtime dir")
        .next()
        .unwrap()
        .unwrap()
        .path();
    let journal: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(runtime_dir.join("owner.json")).unwrap())
            .unwrap();
    assert_eq!(journal["state"], "stopped");
    let helper_pid = journal["pid"].as_u64().unwrap() as u32;
    assert!(
        wait_until_gone(helper_pid, Duration::from_secs(5)),
        "the failed run must be dead"
    );

    // The milestone log ends with the terminal "stopped" record.
    let token = journal["token"].as_str().unwrap();
    let shell_log = std::fs::read_to_string(shell_log_path(&user_data, token)).unwrap();
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
            "stopped".to_string()
        ],
        "{shell_log}"
    );
    let _ = std::fs::remove_dir_all(&user_data);
}

/// Respawn discipline: a full handshake + stop must leave the process
/// clean enough that a SECOND helper (fresh token, fresh runtime dir)
/// handshakes and stops normally — no leaked global state, no port or
/// group leftovers gating the next session.
#[test]
fn a_second_helper_spawns_cleanly_after_a_full_first_session() {
    let user_data = temp_user_data("respawn");
    let stand_in = r#"
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

token = os.environ["MANGAFLOW_DESKTOP_TOKEN"]
journal_path = os.environ["MANGAFLOW_DESKTOP_JOURNAL"]
try:
    starttime = int(open("/proc/self/stat").read().rsplit(")", 1)[1].split()[19])
except Exception:
    starttime = None  # non-Linux: the anchor is optional, verification skips it

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
origin = f"http://127.0.0.1:{server.server_address[1]}"
with open(journal_path, "w", encoding="utf-8") as handle:
    json.dump({"version": 1, "token": token, "state": "ready",
               "pid": os.getpid(),
               **({"pid_starttime": starttime} if starttime is not None else {}),
               "api_origin": origin}, handle)
print("MANGAFLOW_READY " + json.dumps(
    {"token": token, "pid": os.getpid(), "api_origin": origin}), flush=True)
server.serve_forever()
"#;
    let stand_in_path = user_data.join("stand_in_respawn.py");
    std::fs::write(&stand_in_path, stand_in).unwrap();
    let make_config = || HelperConfig {
        python: python(),
        helper_script: stand_in_path.clone(),
        helper_args: vec![],
        ready_timeout: Duration::from_secs(10),
        health_timeout: Duration::from_secs(5),
    };

    let mut first = spawn_helper(&make_config(), &user_data).expect("first handshake");
    let first_exit = first.tree.stop(Duration::from_secs(5)).expect("first stop");

    let mut second = spawn_helper(&make_config(), &user_data).expect("second handshake");
    let second_exit = second.tree.stop(Duration::from_secs(5)).expect("second stop");

    // Both cooperative stops recorded an exit; neither session poisoned the
    // next one.
    let _ = (first_exit, second_exit);
    assert!(!first.tree.alive() && !second.tree.alive());
    let _ = std::fs::remove_dir_all(&user_data);
}

/// Error-path pin: a helper that exits IMMEDIATELY (before any READY
/// output) leaves stdout at EOF — the handshake must fail fast with a
/// verification error (the empty line is not a READY line), tear the
/// tree down, and never hang on the read.
#[test]
fn an_immediately_exiting_helper_fails_verification_and_is_torn_down() {
    let user_data = temp_user_data("instant-exit");
    let stand_in_path = user_data.join("stand_in_exit.py");
    std::fs::write(&stand_in_path, "import sys; sys.exit(3)").unwrap();
    let config = HelperConfig {
        python: python(),
        helper_script: stand_in_path.clone(),
        helper_args: vec![],
        ready_timeout: Duration::from_secs(20),
        health_timeout: Duration::from_secs(10),
    };

    let started = Instant::now();
    let error = match spawn_helper(&config, &user_data) {
        Ok(_) => panic!("an exiting helper must not complete the handshake"),
        Err(error) => error,
    };
    // The handshake maps stdout EOF to an explicit UnexpectedEof I/O error
    // (dedicated to "helper closed stdout before publishing readiness") —
    // a fast, named failure instead of a read hang or a timeout wait.
    assert!(
        matches!(error, SpawnError::Io(ref io_error) if io_error.kind() == std::io::ErrorKind::UnexpectedEof),
        "unexpected error: {error:?}"
    );
    assert!(
        started.elapsed() < Duration::from_secs(10),
        "the EOF failure must be quick, not a ready-timeout wait"
    );

    // The exiting stand-in is reaped by the teardown; nothing may linger.
    #[cfg(unix)]
    let deadline = Instant::now() + Duration::from_secs(5);
    #[cfg(unix)]
    while Instant::now() < deadline {
        let live = std::process::Command::new("pgrep")
            .args(["-f", "stand_in_exit.py"])
            .output()
            .map(|output| !output.stdout.is_empty())
            .unwrap_or(true);
        if !live {
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    #[cfg(unix)]
    assert!(
        !std::process::Command::new("pgrep")
            .args(["-f", "stand_in_exit.py"])
            .output()
            .map(|output| !output.stdout.is_empty())
            .unwrap_or(true),
        "no stand-in process may linger after the teardown"
    );
    let _ = std::fs::remove_dir_all(&user_data);
}

/// Error-path pin: a helper whose FIRST stdout line is garbage (not the
/// READY prefix) must fail verification with BadLine and tear the tree
/// down — the pre-READY channel cannot smuggle unparsed content past the
/// gate.
#[test]
fn a_garbage_ready_line_fails_verification_and_is_torn_down() {
    let user_data = temp_user_data("garbage-ready");
    let stand_in_path = user_data.join("stand_in_garbage.py");
    std::fs::write(&stand_in_path, "print(\"HELLO WORLD\", flush=True)\nimport time\ntime.sleep(60)").unwrap();
    let config = HelperConfig {
        python: python(),
        helper_script: stand_in_path.clone(),
        helper_args: vec![],
        ready_timeout: Duration::from_secs(10),
        health_timeout: Duration::from_secs(5),
    };

    let started = Instant::now();
    let error = match spawn_helper(&config, &user_data) {
        Ok(_) => panic!("a garbage READY line must not complete the handshake"),
        Err(error) => error,
    };
    assert!(
        matches!(error, SpawnError::Verify(VerifyError::BadLine)),
        "unexpected error: {error:?}"
    );
    assert!(
        started.elapsed() < config.ready_timeout,
        "the BadLine failure must be quick, not a ready-budget wait"
    );

    // The garbage-talking stand-in must be dead after the teardown.
    #[cfg(unix)]
    {
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            let live = std::process::Command::new("pgrep")
                .args(["-f", "stand_in_garbage.py"])
                .output()
                .map(|output| !output.stdout.is_empty())
                .unwrap_or(true);
            if !live {
                break;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        assert!(
            !std::process::Command::new("pgrep")
                .args(["-f", "stand_in_garbage.py"])
                .output()
                .map(|output| !output.stdout.is_empty())
                .unwrap_or(true),
            "the garbage-talking helper must be dead after the teardown"
        );
    }
    let _ = std::fs::remove_dir_all(&user_data);
}

/// Error-path pin: a helper binary that cannot be executed must fail the
/// spawn with a clean named error — the ownership layer wraps the exec
/// NotFound as OwnershipError::Spawn — not a panic, not a hang — and the
/// pre-spawn failure records "stopped" with no exit code while the owned
/// runtime directory legitimately persists with the terminal journal (the
/// doc contract for pre-spawn ownership failures).
#[test]
fn spawn_fails_cleanly_on_a_missing_helper_binary() {
    let user_data = temp_user_data("missing-binary");
    let config = HelperConfig {
        python: std::path::PathBuf::from("nonexistent-helper-binary-xyz"),
        helper_script: user_data.join("nonexistent-script.py"),
        helper_args: vec![],
        ready_timeout: Duration::from_secs(5),
        health_timeout: Duration::from_secs(5),
    };

    let error = match spawn_helper(&config, &user_data) {
        Ok(_) => panic!("a missing helper binary must not complete the handshake"),
        Err(error) => error,
    };
    // The ownership layer wraps the exec failure (NotFound) as
    // OwnershipError::Spawn — a named ownership-layer error rather than a
    // bare Io.
    assert!(
        matches!(error, SpawnError::Ownership(OwnershipError::Spawn(_))),
        "unexpected error: {error:?}"
    );

    // Terminal bookkeeping: the pre-spawn failure records "stopped" with
    // no exit code (the doc contract for pre-spawn ownership failures) —
    // the runtime directory legitimately persists with the terminal
    // journal; only the cleanup of the temp root removes it.
    let journal_value: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(newest_journal_path(&user_data).unwrap()).unwrap(),
    )
    .unwrap();
    assert_eq!(journal_value["state"], "stopped", "{journal_value}");
    assert!(
        journal_value.get("exit_code").is_none(),
        "a pre-spawn failure records no exit code: {journal_value}"
    );
    let _ = std::fs::remove_dir_all(&user_data);
}

/// ADR D9 pin: a stand-in that publishes a NON-loopback api_origin must
/// fail verification with OriginNotLoopback and be torn down — the shell
/// never GOes a helper that announced off-box reachability.
#[test]
fn a_non_loopback_origin_fails_verification_and_is_torn_down() {
    let user_data = temp_user_data("non-loopback");
    let stand_in = r#"
import json, os, sys
token = os.environ["MANGAFLOW_DESKTOP_TOKEN"]
journal_path = os.environ["MANGAFLOW_DESKTOP_JOURNAL"]
with open(journal_path, "w", encoding="utf-8") as handle:
    json.dump({"version": 1, "token": token, "state": "ready",
               "pid": os.getpid(), "api_origin": "http://10.0.0.9:8080"}, handle)
print("MANGAFLOW_READY " + json.dumps(
    {"token": token, "pid": os.getpid(), "api_origin": "http://10.0.0.9:8080"}), flush=True)
sys.stdin.read()
"#;
    let stand_in_path = user_data.join("stand_in_offbox.py");
    std::fs::write(&stand_in_path, stand_in).unwrap();
    let config = HelperConfig {
        python: python(),
        helper_script: stand_in_path.clone(),
        helper_args: vec![],
        ready_timeout: Duration::from_secs(20),
        health_timeout: Duration::from_secs(10),
    };

    let error = match spawn_helper(&config, &user_data) {
        Ok(_) => panic!("a non-loopback origin must not complete the handshake"),
        Err(error) => error,
    };
    assert!(
        matches!(error, SpawnError::Verify(VerifyError::OriginNotLoopback)),
        "unexpected error: {error:?}"
    );

    // The off-box announcer must be dead after the teardown.
    #[cfg(unix)]
    {
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            let live = std::process::Command::new("pgrep")
                .args(["-f", "stand_in_offbox.py"])
                .output()
                .map(|output| !output.stdout.is_empty())
                .unwrap_or(true);
            if !live {
                break;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        assert!(
            !std::process::Command::new("pgrep")
                .args(["-f", "stand_in_offbox.py"])
                .output()
                .map(|output| !output.stdout.is_empty())
                .unwrap_or(true),
            "the off-box announcer must be dead after the teardown"
        );
    }
    let _ = std::fs::remove_dir_all(&user_data);
}

/// F41 closure: the ReadyTimeout path had no end-to-end test — a helper
/// that NEVER publishes READY must fail with SpawnError::ReadyTimeout on
/// the ready budget (not the health window), tear the silent helper down,
/// and record a terminal journal with only the spawn milestone.
#[test]
fn a_silent_helper_fails_with_ready_timeout_and_is_torn_down() {
    let user_data = temp_user_data("never-ready");
    let stand_in_path = user_data.join("stand_in_silent.py");
    std::fs::write(&stand_in_path, "import time; time.sleep(60)").unwrap();
    let config = HelperConfig {
        python: python(),
        helper_script: stand_in_path.clone(),
        helper_args: vec![],
        ready_timeout: Duration::from_secs(2),
        health_timeout: Duration::from_secs(5),
    };

    let started = Instant::now();
    let error = match spawn_helper(&config, &user_data) {
        Ok(_) => panic!("a helper that never publishes READY must fail"),
        Err(error) => error,
    };
    assert!(matches!(error, SpawnError::ReadyTimeout), "{error:?}");
    assert!(
        started.elapsed() < Duration::from_secs(10),
        "ReadyTimeout must fire on the ready budget, not the health window"
    );

    // Teardown: the silent helper must be dead (abort_spawn stops the tree).
    #[cfg(unix)]
    let deadline = Instant::now() + Duration::from_secs(5);
    #[cfg(unix)]
    let helper_alive = |tag: &str| -> bool {
        let _ = tag;
        std::process::Command::new("pgrep")
            .args(["-f", "stand_in_silent.py"])
            .output()
            .map(|output| !output.stdout.is_empty())
            .unwrap_or(true)
    };
    #[cfg(unix)]
    while helper_alive("poll") && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(50));
    }
    #[cfg(unix)]
    assert!(
        !helper_alive("final"),
        "the silent helper must be dead after the ReadyTimeout teardown"
    );

    // Exactly one owned run exists, recorded as terminal, with the spawn
    // milestone and no ready_verified (the handshake never got that far).
    let runtime_entry = std::fs::read_dir(user_data.join("runtime"))
        .expect("runtime dir")
        .next()
        .unwrap()
        .unwrap()
        .path();
    let journal: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(runtime_entry.join("owner.json")).unwrap())
            .unwrap();
    assert_eq!(journal["state"], "stopped", "{journal}");
    let logs_dir = user_data.join("logs");
    let shell_log = std::fs::read_dir(&logs_dir)
        .unwrap()
        .find(|entry| {
            entry.as_ref().unwrap().file_name().to_string_lossy().starts_with("shell-")
        })
        .unwrap()
        .unwrap()
        .path();
    let events: Vec<String> = std::fs::read_to_string(&shell_log)
        .unwrap()
        .lines()
        .filter_map(|line| {
            serde_json::from_str::<serde_json::Value>(line)
                .ok()?
                .get("event")?
                .as_str()
                .map(str::to_string)
        })
        .collect();
    // The handshake never got past spawn: no ready_verified/go_sent/health
    // milestones — only the spawn and the teardown's own stopped marker.
    assert_eq!(events, vec!["spawn".to_string(), "stopped".to_string()], "{events:?}");
    let _ = std::fs::remove_dir_all(&user_data);
}

/// Regression (red team 2026-09-08): `stop()` called after the direct child
/// was already reaped — the native-host self-exit path reaps via `try_wait`
/// and then stops the tree — must not signal the freed pid. The fixture
/// child is a session leader that leaves an orphaned group member behind:
/// the group id stays allocated while the orphan lives, so the old
/// signal-before-check order deterministically killed it (the exact
/// "unrelated group" blast radius without needing pid reuse).
#[test]
#[cfg(unix)]
fn stop_on_an_already_reaped_child_spares_the_leftover_group() {
    let user_data = temp_user_data("reaped-stop");
    let child_code = "\
import subprocess, sys, time
orphan = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
print(orphan.pid, flush=True)
time.sleep(0.5)
";
    let mut command = Command::new(python());
    command
        .arg("-c")
        .arg(child_code)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    let mut tree = OwnedTree::spawn(command).unwrap();
    let mut stdout = tree.child.stdout.take().unwrap();
    // Diagnostic/health lines precede the orphan pid; keep reading until a
    // line parses as the pid.
    let orphan_pid: u32 = loop {
        let mut line = String::new();
        std::io::BufRead::read_line(&mut std::io::BufReader::new(&mut stdout), &mut line)
            .expect("child stdout readable");
        match line.trim().parse::<u32>() {
            Ok(pid) => break pid,
            Err(_) => eprintln!("fixture child: {}", line.trim()),
        }
    };

    // Reap the exited child BEFORE calling stop — the native-host
    // self-exit path shape.
    let reap_deadline = Instant::now() + Duration::from_secs(5);
    let reaped = loop {
        match tree.child.try_wait().expect("try_wait succeeds") {
            Some(status) => break status,
            None => {
                assert!(
                    Instant::now() < reap_deadline,
                    "the fixture child never exited"
                );
                std::thread::sleep(Duration::from_millis(20));
            }
        }
    };
    assert!(
        proc_alive(orphan_pid),
        "fixture broken: the orphan must outlive the exited child"
    );
    // Fixture integrity: the orphan's process group must be the reaped
    // child's pid — `OwnedTree::spawn` claims the group at spawn time
    // (`process_group(0)`), and group ids persist while any member lives —
    // otherwise the stray-signal scenario below cannot exist. /proc pgid is
    // the third whitespace field after the comm's closing paren.
    let stat = std::fs::read_to_string(format!("/proc/{orphan_pid}/stat"))
        .expect("orphan stat readable");
    let pgid: u32 = stat
        .rsplit(')')
        .next()
        .unwrap()
        .split_whitespace()
        .nth(2)
        .unwrap()
        .parse()
        .unwrap();
    assert_eq!(
        pgid,
        tree.pid(),
        "fixture broken: the orphan must sit in the exited child's group"
    );

    let exit = tree
        .stop(Duration::from_millis(300))
        .expect("stop on an already-reaped tree succeeds");
    assert_eq!(
        exit,
        reaped.code(),
        "stop must report the already-cached exit status"
    );
    // The stray group signal (old order) kills the orphan; give a delivered
    // signal time to surface as an exit or zombie before asserting.
    let deadline = Instant::now() + Duration::from_millis(500);
    while Instant::now() < deadline && orphan_is_live(orphan_pid) {
        std::thread::sleep(Duration::from_millis(20));
    }
    assert!(
        orphan_is_live(orphan_pid),
        "stop() signalled the leftover process group of an already-reaped child"
    );

    // Clean up the fixture orphan out-of-band; it is outside the tree by
    // construction (that is the point of the regression).
    let _ = Command::new("kill")
        .arg("-9")
        .arg(orphan_pid.to_string())
        .output();
    let _ = std::fs::remove_dir_all(&user_data);
}

/// Live means an existing /proc entry that is not a zombie: a reaped or
/// still-dying process keeps its /proc node in state Z, which a bare
/// existence check misreports as alive.
#[cfg(unix)]
fn orphan_is_live(pid: u32) -> bool {
    match std::fs::read_to_string(format!("/proc/{pid}/stat")) {
        Ok(stat) => {
            let state = stat.rsplit(')').next().unwrap().split_whitespace().next().unwrap();
            state != "Z"
        }
        Err(_) => false,
    }
}

/// Red team 2026-09-08: the READY-line read is byte-capped. A helper whose
/// stdout emits a newline-free blob (a third-party library echoing a huge
/// payload) must fail verification on the truncated first chunk instead of
/// buffering an unbounded String — and must fail FAST, not at the readiness
/// deadline.
#[test]
fn oversized_ready_line_fails_verification_quickly() {
    use mangaflow_desktop_shell_core::handshake::SpawnError;
    use mangaflow_desktop_shell_core::protocol::VerifyError;

    let user_data = temp_user_data("oversize-line");
    let stand_in = r#"
import sys, time
sys.stdout.write("MANGAFLOW_READY " + "A" * (200 * 1024) + "\n")
sys.stdout.flush()
time.sleep(120)
"#;
    let stand_in_path = user_data.join("oversize_ready.py");
    std::fs::write(&stand_in_path, stand_in).unwrap();
    let config = HelperConfig {
        python: python(),
        helper_script: stand_in_path,
        helper_args: vec![],
        ready_timeout: Duration::from_secs(20),
        health_timeout: Duration::from_secs(10),
    };

    let started = Instant::now();
    let error = spawn_helper(&config, &user_data)
        .err()
        .expect("an unparsable oversized READY line must fail the handshake");
    assert!(
        matches!(error, SpawnError::Verify(VerifyError::BadJson)),
        "unexpected error: {error:?}"
    );
    assert!(
        started.elapsed() < Duration::from_secs(10),
        "the capped read must fail fast, not at the readiness deadline (took {:?})",
        started.elapsed()
    );
    let _ = std::fs::remove_dir_all(&user_data);
}

/// Unix stand-in for the Windows-gated launcher-chain pin: on Unix,
/// membership is DIRECT-CHILD-ONLY, so a helper whose readiness is
/// announced by a real forked grandchild (its own pid in READY and in the
/// journal) must be REFUSED with Verify(PidMismatch) — the shell cannot
/// kill what it does not own, and refusing is the fail-closed answer the
/// Windows Job-membership path solves differently. The abort must still
/// stop the whole group (parent and grandchild share it) and mark the
/// journal stopped; both announcer processes are reaped by pid in a
/// bounded sweep so no failure path leaks a one-hour sleeper.
#[test]
#[cfg(unix)]
fn a_grandchild_pid_ready_is_refused_on_unix_direct_child_membership() {
    let script = temp_user_data("grandchild-pid").join("forking-helper.py");
    std::fs::write(
        &script,
        r#"
import json, os, socket, sys, time
from pathlib import Path

token = os.environ["MANGAFLOW_DESKTOP_TOKEN"]
journal = Path(os.environ["MANGAFLOW_DESKTOP_JOURNAL"])
side_info = Path(sys.argv[1]) / "announcer.txt"

pid = os.fork()
if pid == 0:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    stat = open(f"/proc/{os.getpid()}/stat").read()
    record = {
        "version": 1,
        "token": token,
        "state": "ready",
        "pid": os.getpid(),
        "api_origin": origin,
        "pid_starttime": int(stat.rsplit(")", 1)[1].split()[19]),
    }
    journal.write_text(json.dumps(record), encoding="utf-8")
    print("MANGAFLOW_READY " + json.dumps(
        {"token": token, "pid": os.getpid(), "api_origin": origin}), flush=True)
    time.sleep(3600)
side_info.write_text(f"{os.getpid()} {pid}", encoding="utf-8")
time.sleep(3600)
"#,
    )
    .unwrap();

    let user_data = temp_user_data("grandchild-pid-ud");
    // The stand-in needs the user-data path to drop its announcer list.
    // The user-data path rides helper_args (argv[1]) — no process-global
    // env mutation, which would race parallel test threads.
    let config = HelperConfig {
        python: python(),
        helper_script: script.clone(),
        helper_args: vec![user_data.clone().into_os_string().into_string().unwrap()],
        ready_timeout: Duration::from_secs(20),
        health_timeout: Duration::from_secs(5),
    };
    let error = spawn_helper(&config, &user_data).err().expect("must refuse");
    match &error {
        SpawnError::Verify(mangaflow_desktop_shell_core::protocol::VerifyError::PidMismatch) => {}
        other => panic!("expected Verify(PidMismatch), got {other:?}"),
    }

    // Terminal bookkeeping ran (the journal the grandchild wrote flips to
    // stopped), and both announcer processes are dead — bounded sweep.
    let runtime = user_data.join("runtime");
    let journal = std::fs::read_dir(&runtime)
        .unwrap()
        .filter_map(|entry| entry.ok())
        .find(|entry| entry.path().join("owner.json").is_file())
        .expect("an owned runtime entry exists")
        .path()
        .join("owner.json");
    let value: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&journal).unwrap()).unwrap();
    assert_eq!(value["state"].as_str(), Some("stopped"), "{value}");

    let announcer =
        std::fs::read_to_string(user_data.join("announcer.txt")).unwrap();
    let pids: Vec<u32> = announcer
        .split_whitespace()
        .map(|p| p.parse().unwrap())
        .collect();
    let deadline = Instant::now() + Duration::from_secs(10);
    while pids.iter().any(|p| Path::new(&format!("/proc/{p}")).exists()) {
        assert!(
            Instant::now() < deadline,
            "the group stop must reap the launcher and its grandchild"
        );
        std::thread::sleep(Duration::from_millis(50));
    }

    let _ = fs::remove_dir_all(&user_data);
    let _ = fs::remove_file(&script);
}
