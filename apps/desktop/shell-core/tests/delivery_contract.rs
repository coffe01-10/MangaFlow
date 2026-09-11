//! Delivery contract tests (OS-independent, run in the Linux sandbox).
//!
//! The V02-54 acceptance matrix leaves real MSI/NSIS install, upgrade and
//! uninstall behavior NOT RUN (no Windows here). These tests freeze the
//! config-level rules the future installer must keep: the uninstaller must
//! never carry user-data deletion hooks, the WebView surface stays loopback
//! only, and the bundle targets stay the pinned MSI + NSIS pair.
//! D1 re-verification on Windows remains required before shipping.

use serde_json::Value;
use std::path::{Path, PathBuf};

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

/// Issue #300 (Stage 0 pin): script-src carries 'unsafe-inline' because the
/// static export ships Next's inline bootstrap (`self.__next_f.push`) plus
/// shell-tools.html's own inline script; a nonce rework needs per-request
/// rendering that a statically exported page cannot provide, and hash-based
/// removal would have to cover every build's flight data. The debt is
/// therefore PINNED, not accidental: this test freezes the exact directive
/// so that dropping or extending 'unsafe-inline' (or adding a source) is a
/// deliberate, lead-reviewed contract change. The equivalent header for the
/// web form is pinned separately in apps/web (next-config-csp.test.ts).
#[test]
fn script_src_unsafe_inline_is_pinned_debt_not_drift() {
    let config = tauri_config();
    let csp = config["app"]["security"]["csp"]
        .as_str()
        .expect("restricted CSP configured");
    let script_src = csp
        .split("; ")
        .find(|portion| portion.starts_with("script-src "))
        .expect("script-src directive present");
    assert_eq!(
        script_src,
        "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'",
        "script-src drifted; changing the unsafe-inline debt requires a deliberate contract change (issue #300)"
    );
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

/// Issue #300 (plan-B web form): the CSP for the plan-B desktop web form —
/// and the normal web deployment, which shares the app — ships from apps/web,
/// built per request by proxy.ts: Next extracts the nonce from the proxy-set
/// Content-Security-Policy request header and applies it to its own bootstrap
/// scripts during dynamic rendering, so script-src no longer needs
/// 'unsafe-inline'. This pins the source contract:
/// - next.config.ts headers() must not carry a Content-Security-Policy: two
///   response headers are both enforced by browsers, so a stale build-time
///   policy here would silently act as a nonce-less second policy.
/// - lib/csp.ts's script-src directive must be nonce-based and free of
///   'unsafe-inline' (re-adding it is a deliberate contract change).
/// The static-export form's tauri.conf debt is pinned separately above
/// (`script_src_unsafe_inline_is_pinned_debt_not_drift`).
#[test]
fn plan_b_web_csp_script_src_is_nonce_based() {
    let web = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../web");
    let read = |name: &str| {
        let path = web.join(name);
        std::fs::read_to_string(&path)
            .unwrap_or_else(|error| panic!("apps/web/{name} readable ({path:?}): {error}"))
    };

    let next_config = read("next.config.ts");
    assert!(
        !next_config.contains("Content-Security-Policy"),
        "next.config headers() must not set a CSP; the nonce policy ships from proxy.ts (#300)"
    );

    let csp_module = read("lib/csp.ts");
    let script_src_lines: Vec<&str> = csp_module
        .lines()
        .filter(|line| line.contains("script-src"))
        .collect();
    assert!(
        !script_src_lines.is_empty(),
        "lib/csp.ts must define the script-src directive"
    );
    for line in &script_src_lines {
        assert!(
            !line.contains("'unsafe-inline'"),
            "plan-B script-src regained 'unsafe-inline': {line}"
        );
    }
    assert!(
        script_src_lines
            .iter()
            .any(|line| line.contains("'nonce-")),
        "plan-B script-src must be nonce-based: {script_src_lines:?}"
    );

    let proxy = read("proxy.ts");
    assert!(
        proxy.contains("Content-Security-Policy"),
        "proxy.ts must set the CSP request header (Next's nonce propagation) and the response header"
    );
}

/// Enumerate every capability file the build actually loads. tauri-build's
/// default glob is `capabilities/**/*` and tauri-utils parses `.json`/`.toml`
/// at ANY depth - a top-level `read_dir` would let a nested file
/// (`capabilities/remote/remote.json`) silently bypass these contract tests.
fn capability_files() -> Vec<PathBuf> {
    fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
        let entries = std::fs::read_dir(dir)
            .unwrap_or_else(|error| panic!("capabilities dir {dir:?} readable: {error}"));
        for entry in entries {
            let entry = entry.expect("capability entry readable");
            let path = entry.path();
            if path.is_dir() {
                walk(&path, out);
            } else if path.extension().is_some_and(|ext| ext == "json" || ext == "toml") {
                out.push(path);
            }
        }
    }
    let capabilities = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../src-tauri/capabilities");
    let mut files = Vec::new();
    walk(&capabilities, &mut files);
    files.sort();
    files
}

/// The capability file's authority surface stays pinned: core permissions on
/// exactly the main + shell-tools windows (the tools window is local-context
/// by construction, #299). A new permission, window or capability file widens
/// what a loaded document may reach and must be a deliberate contract change,
/// not config drift.
#[test]
fn capability_surface_stays_the_pinned_default() {
    let files = capability_files();
    assert_eq!(
        files.len(),
        1,
        "exactly one capability file is expected (default.json): {files:?}"
    );
    assert!(
        files[0].file_name().is_some_and(|name| name == "default.json"),
        "the capability file must stay default.json"
    );
    let value: Value = serde_json::from_str(
        &std::fs::read_to_string(&files[0]).expect("capability readable"),
    )
    .expect("capability json parses");
    let expected_keys = ["identifier", "windows", "permissions"];
    for key in expected_keys {
        assert!(value.get(key).is_some(), "capability key {key} missing");
    }
    assert_eq!(
        value["identifier"], "default",
        "capability identifier drifted"
    );
    // #299: shell-tools is the local-context window the 工具 menu opens (the
    // plan-B web origin is remote and stays denied). The pair is pinned so a
    // new window is a deliberate contract change, not config drift.
    assert_eq!(
        value["windows"],
        serde_json::json!(["main", "shell-tools"]),
        "the capability must target only the main + shell-tools windows"
    );
    assert_eq!(
        value["permissions"],
        serde_json::json!(["core:default"]),
        "permissions drifted beyond core:default — a lead-reviewed security decision is required"
    );
}

/// withGlobalTauri is the shell-tools page's whole trigger surface (W-04):
/// if it is ever disabled, the desktop_export_logs / desktop_pick_* commands
/// lose their only in-shell caller. Pin it so the flag cannot be flipped as
/// cleanup without noticing.
#[test]
fn with_global_tauri_stays_enabled_for_the_shell_tools_page() {
    let config = tauri_config();
    assert_eq!(
        config["app"]["withGlobalTauri"], true,
        "withGlobalTauri must stay on: shell-tools.html invokes the shell commands"
    );
}

/// `app.windows` must stay EMPTY in config: a config-declared window would
/// let the WebView load documents BEFORE the shell's helper handshake and
/// GO gate (the shell builds its window programmatically only after the
/// ownership verification passes). A window appearing in config is a
/// startup-security regression, not a UI tweak.
#[test]
fn no_config_declared_window_may_bypass_the_handshake_gate() {
    let config = tauri_config();
    assert_eq!(
        config["app"]["windows"],
        serde_json::json!([]),
        "a config-declared window would load before the handshake-gated programmatic window"
    );
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
    let files = capability_files();
    assert!(
        !files.is_empty(),
        "at least one capability file must exist (the shell grants core:default today)"
    );
    for path in files {
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
