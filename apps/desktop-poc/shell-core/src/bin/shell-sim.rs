//! Crash-semantics simulator: performs the full handshake like the real
//! shell, then dies abruptly (SIGABRT) WITHOUT stopping the helper. Used by
//! the residue test to prove that helper + descendants still die with the
//! shell (Unix PDEATHSIG chain / Windows KILL_ON_JOB_CLOSE).
//!
//! On Windows this binary additionally exercises the root Job Object path at
//! spawn; runtime behavior there is NOT RUN in the Linux sandbox and must be
//! re-verified on Windows (D3).

use std::time::Duration;

use mangaflow_poc_shell_core::handshake::{self, spawn_helper, HelperConfig};
use mangaflow_poc_shell_core::protocol::HEALTH_PATH;

fn env_or(name: &str, default: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| default.to_string())
}

fn main() {
    let python = env_or("MANGAFLOW_POC_PYTHON", "python3");
    let helper = env_or("MANGAFLOW_POC_HELPER", "");
    let user_data = env_or("MANGAFLOW_POC_USER_DATA", "");
    if helper.is_empty() || user_data.is_empty() {
        eprintln!("MANGAFLOW_POC_HELPER and MANGAFLOW_POC_USER_DATA are required");
        std::process::exit(2);
    }

    let mut config =
        HelperConfig::stub(std::path::Path::new(&python), std::path::Path::new(&helper));
    config.helper_args.push("--grandchild".into());
    let spawned = spawn_helper(&config, std::path::Path::new(&user_data)).unwrap_or_else(|error| {
        eprintln!("handshake failed: {error:?}");
        std::process::exit(3);
    });
    let (status, body) = handshake::get_status(
        &spawned.ready.api_origin,
        HEALTH_PATH,
        Duration::from_secs(2),
    )
    .unwrap_or_else(|error| {
        eprintln!("health failed: {error}");
        std::process::exit(4);
    });
    println!(
        "MANGAFLOW_SIM_OK {} {status} {body}",
        spawned.ready.api_origin
    );
    // Abrupt death: no Drop, no graceful stop — the tree must still die.
    std::process::abort();
}
