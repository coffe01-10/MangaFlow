//! Delivery contract tests (OS-independent, run in the Linux sandbox).
//!
//! The V02-54 acceptance matrix leaves real MSI/NSIS install, upgrade and
//! uninstall behavior NOT RUN (no Windows here). These tests freeze the
//! config-level rules the future installer must keep: the uninstaller must
//! never carry user-data deletion hooks, the WebView surface stays loopback
//! only, and the bundle targets stay the pinned MSI + NSIS pair.
//! D1 re-verification on Windows remains required before shipping.

use serde_json::Value;
use std::path::PathBuf;

fn tauri_config() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../src-tauri/tauri.conf.json");
    serde_json::from_str(&std::fs::read_to_string(path).expect("tauri.conf.json readable"))
        .expect("tauri.conf.json parses")
}

#[test]
fn uninstall_config_must_not_carry_user_data_deletion_hooks() {
    let config = tauri_config();
    // Tauri's NSIS uninstaller removes install-dir files, registry keys and
    // shortcuts; the only config mechanism that could delete files elsewhere
    // is a custom `installerHooks` NSIS hook file (PREUNINSTALL/POSTUNINSTALL).
    // User data (`%LOCALAPPDATA%\<identifier>`: database, assets, credential
    // master key) must survive uninstall, so no hook file may be wired in.
    let windows = &config["bundle"]["windows"];
    let nsis = &windows["nsis"];
    assert!(
        nsis.is_null()
            || (nsis.get("installerHooks").is_none() && nsis.get("installer_hooks").is_none()),
        "NSIS installer hooks appeared without a lead-reviewed user-data safety argument"
    );
    // Defense in depth: no deletion-flavored directive may appear anywhere in
    // the shipped bundle config.
    let text = serde_json::to_string(&config).unwrap().to_ascii_lowercase();
    for forbidden in ["deleteappdata", "rmdir", "rm -rf", "del /", "shutil.rmtree"] {
        assert!(
            !text.contains(forbidden),
            "forbidden deletion directive in bundle config: {forbidden}"
        );
    }
}

#[test]
fn webview_network_surface_stays_loopback_only() {
    let config = tauri_config();
    let csp = config["app"]["security"]["csp"]
        .as_str()
        .expect("restricted CSP configured");
    assert!(
        csp.contains("connect-src 'self' http://127.0.0.1:*"),
        "connect-src must stay self + loopback: {csp}"
    );
    assert!(csp.contains("object-src 'none'"), "{csp}");
    assert!(csp.contains("frame-src 'none'"), "{csp}");
    // Every whitelisted http origin is the loopback wildcard, never a remote.
    for portion in csp.split("; ") {
        if let Some(origins) = portion.strip_prefix("connect-src ") {
            for origin in origins.split(' ') {
                assert!(
                    origin == "'self'" || origin == "http://127.0.0.1:*",
                    "non-loopback connect-src origin: {origin}"
                );
            }
        }
    }
}

#[test]
fn bundle_identity_and_targets_stay_pinned() {
    let config = tauri_config();
    assert_eq!(config["productName"].as_str(), Some("MangaFlow"));
    assert_eq!(
        config["identifier"].as_str(),
        Some("com.mangaflow.desktop"),
        "identifier defines the user-data directory; changing it orphans user data"
    );
    let targets = config["bundle"]["targets"].as_array().expect("targets");
    let names: Vec<&str> = targets.iter().filter_map(|v| v.as_str()).collect();
    assert!(names.contains(&"msi") && names.contains(&"nsis"), "{names:?}");
}

/// The shell exposes `window.__TAURI__` to EVERY document the WebView loads —
/// including the plan-B (W-15) web origin `http://127.0.0.1:<port>` loaded via
/// `WebviewUrl::External`. The only thing keeping that (or any other remote)
/// page from invoking the shell commands — log export, file pick, file
/// read-back — is the ACL context: in tauri 2.x an invoke from a remote
/// origin is denied unless some capability explicitly declares a `remote`
/// context (tauri 2.11.5 `webview/mod.rs`: "remote content can never reach
/// custom commands unless an explicit `remote` capability has been
/// configured"). This test freezes exactly that: shell commands stay a
/// LOCAL-context surface (the static export / shell-tools page). Granting a
/// remote context is a security decision that must replace this contract
/// deliberately, not a convenience someone reaches for while wiring up the
/// web form.
#[test]
fn no_capability_may_grant_a_remote_ipc_context() {
    let capabilities = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../src-tauri/capabilities");
    let entries = std::fs::read_dir(&capabilities)
        .unwrap_or_else(|error| panic!("capabilities dir {:?} readable: {error}", capabilities))
        .collect::<Result<Vec<_>, _>>()
        .expect("capability entries readable");
    let json_files: Vec<_> = entries
        .iter()
        .filter(|entry| entry.path().extension().is_some_and(|ext| ext == "json"))
        .collect();
    assert!(
        !json_files.is_empty(),
        "at least one capability file must exist (the shell grants core:default today)"
    );
    for entry in json_files {
        let path = entry.path();
        let text = std::fs::read_to_string(&path)
            .unwrap_or_else(|error| panic!("capability {:?} readable: {error}", path));
        let value: Value = serde_json::from_str(&text)
            .unwrap_or_else(|error| panic!("capability {:?} parses: {error}", path));
        assert!(
            value.get("remote").is_none(),
            "capability {:?} declares a `remote` IPC context: shell commands would become \
             invokable from remote documents (the plan-B web origin and anything else the \
             WebView loads). This requires a lead-reviewed security decision and must \
             consciously replace this contract — see this test's documentation.",
            path
        );
    }
}
