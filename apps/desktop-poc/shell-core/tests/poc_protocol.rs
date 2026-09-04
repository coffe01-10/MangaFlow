//! Linux-sandbox integration tests for the PoC startup protocol and
//! process-tree ownership. Windows runtime behavior is NOT RUN here; the
//! Windows-side compile gate runs separately via
//! `cargo check --target x86_64-pc-windows-msvc`.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use mangaflow_poc_shell_core::handshake::{spawn_helper, HelperConfig};
use mangaflow_poc_shell_core::ownership::OwnedTree;
use mangaflow_poc_shell_core::protocol::{verify_ready_line, GO_PREFIX, HEALTH_PATH};

fn python() -> PathBuf {
    PathBuf::from(std::env::var("MANGAFLOW_POC_PYTHON").unwrap_or_else(|_| "python3".into()))
}

fn helper_script() -> PathBuf {
    let from_env = std::env::var("MANGAFLOW_POC_HELPER").map(PathBuf::from);
    from_env.unwrap_or_else(|_| {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../sidecar/mangaflow_poc_helper.py")
    })
}

fn temp_user_data(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("mangaflow-poc-test-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn proc_alive(pid: u32) -> bool {
    #[cfg(unix)]
    {
        Path::new(&format!("/proc/{pid}")).exists()
    }
    #[cfg(windows)]
    {
        let _ = pid;
        false
    }
}

fn wait_until_gone(pid: u32, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if !proc_alive(pid) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    proc_alive(pid) == false
}

#[test]
fn handshake_go_health_and_clean_stop_leaves_no_residue() {
    let user_data = temp_user_data("clean-stop");
    let mut config = HelperConfig::stub(&python(), &helper_script());
    config.helper_args.push("--grandchild".into());

    let mut spawned = spawn_helper(&config, &user_data).expect("handshake must complete");
    let helper_pid = spawned.tree.pid();

    // Journal is owned by the shell; before stop it still reads "ready".
    let journal = std::fs::read_to_string(spawned.layout.journal_path()).unwrap();
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&journal).unwrap()["state"],
        "ready"
    );
    let grandchild_pid = serde_json::from_str::<serde_json::Value>(&journal).unwrap()
        ["grandchild_pid"]
        .as_u64()
        .expect("stub --grandchild records its descendant");

    let exit_code = spawned
        .tree
        .stop(Duration::from_secs(5))
        .expect("stop succeeds");
    assert_eq!(exit_code, Some(0));

    assert!(
        wait_until_gone(helper_pid, Duration::from_secs(5)),
        "helper must be gone"
    );
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

    let (status_first, _) = mangaflow_poc_shell_core::handshake::get_status(
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
        .env("MANGAFLOW_POC_TOKEN", "ab".repeat(16))
        .env(
            "MANGAFLOW_POC_JOURNAL",
            user_data
                .join(format!("runtime/mangaflow-poc-{}", "ab".repeat(16)))
                .join("owner.json"),
        );
    std::fs::create_dir_all(user_data.join(format!("runtime/mangaflow-poc-{}", "ab".repeat(16))))
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
        mangaflow_poc_shell_core::handshake::get_status(
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
        .env("MANGAFLOW_POC_PYTHON", python())
        .env("MANGAFLOW_POC_HELPER", helper_script())
        .env("MANGAFLOW_POC_USER_DATA", &user_data)
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
    let grandchild_pid = journal["grandchild_pid"].as_u64().unwrap() as u32;

    assert!(
        wait_until_gone(helper_pid, Duration::from_secs(5)),
        "helper must die with the shell"
    );
    assert!(
        wait_until_gone(grandchild_pid, Duration::from_secs(5)),
        "descendants must die through the PDEATHSIG chain"
    );
    let _ = std::fs::remove_dir_all(&user_data);
}

#[test]
fn noncooperative_helper_is_forcefully_killed() {
    let user_data = temp_user_data("timeout");
    let mut command = Command::new(python());
    // The helper ignores SIGTERM, so stop() must escalate to SIGKILL after
    // the grace deadline instead of waiting forever.
    command.arg("-c").arg(
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(3600)",
    );
    let mut tree = OwnedTree::spawn(command).unwrap();
    let pid = tree.pid();

    let exit = tree
        .stop(Duration::from_millis(500))
        .expect("forceful stop succeeds");
    assert_eq!(exit, None, "SIGKILLed process reports no exit code");
    assert!(wait_until_gone(pid, Duration::from_secs(5)));
    let _ = std::fs::remove_dir_all(&user_data);
}
