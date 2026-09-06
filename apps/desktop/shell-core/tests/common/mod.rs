//! Shared helpers for the shell-core integration test binaries. Included
//! from each test crate via `mod common;` (a subdirectory module is not a
//! test crate of its own).

use std::path::PathBuf;

/// Resolve the Python interpreter for helper tests: an explicit
/// `MANGAFLOW_DESKTOP_PYTHON` always wins; otherwise the platform default.
/// "python3" on a stock Windows install resolves to the Microsoft Store
/// app-execution stub, which spawns, prints nothing, and exits 9009 — every
/// helper test would see "closed stdout before publishing readiness".
/// "python" resolves to a real interpreter wherever Python is installed.
pub fn python() -> PathBuf {
    match std::env::var("MANGAFLOW_DESKTOP_PYTHON") {
        Ok(python) => PathBuf::from(python),
        Err(_) => PathBuf::from(if cfg!(windows) { "python" } else { "python3" }),
    }
}
