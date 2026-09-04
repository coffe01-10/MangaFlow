//! Unified desktop log layout, rotation, and export (V02-54B/C, ADR §4.5).
//!
//! All desktop-run logs live under `<user_data>/logs/`: the shell writes a
//! per-run JSON-lines milestone log (`shell-<token>.log`), and the helper's
//! stderr — which carries the API/Worker uvicorn output in the desktop form —
//! is redirected by the shell into `helper-<token>.stderr.log` instead of
//! being inherited from a console that does not exist for a GUI process.
//!
//! `export_logs_zip` archives that directory to a user-chosen destination.
//! Both directions of the user-data boundary are enforced: archive members
//! may only be regular files whose canonical paths stay inside the canonical
//! logs directory (symlinks are skipped, never followed), and the export
//! destination may never resolve to any path inside the user-data root, so
//! an export can neither read beyond the logs nor write into user data.
//!
//! V02-54C adds size-based rotation: when a `shell-*.log` or
//! `helper-*.stderr.log` file reaches [`ROTATION_THRESHOLD_BYTES`] it is
//! renamed to a numbered generation (`.1` … `.<ROTATION_KEEP_GENERATIONS>`),
//! and the oldest generation is deleted once the cap is exceeded. The two
//! log kinds rotate in deliberately different regimes, and this is the
//! stated trade-off (not an oversight):
//!
//! * The shell owns every write to its RunLog, so `shell-<token>.log`
//!   rotates **in-session**: `RunLog::record` checks the active file before
//!   each line, closes it, shifts the generations, and reopens the same base
//!   path — the open path keeps accepting writes after rotation.
//! * The helper stderr is held open by the helper process itself (Windows
//!   cannot rename a file another process holds open without
//!   FILE_SHARE_DELETE, and a Unix rename would detach the helper's future
//!   writes from the fresh base), so `helper-<token>.stderr.log` rotates
//!   **across sessions**: every session start sweeps the logs directory
//!   while the previous sessions' files are closed.
//!
//! Rotation never follows symlinks (a link planted at a log path is skipped
//! untouched, a symlinked generation is unlinked — its target survives), and
//! it never renames or deletes anything outside the canonical logs root:
//! candidates are matched against the fixed base patterns inside that
//! directory, generation names derive from the matched file names, and a
//! canonical-containment check runs before any rename. Rotation is
//! housekeeping: a failed rotation never blocks a session start and never
//! costs the milestone line that triggered it.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Component, Path, PathBuf};

use serde::Serialize;

use crate::protocol::unix_now;
use crate::ziparch::{dos_date_time, ZipWriter};

pub const LOGS_DIR_NAME: &str = "logs";
/// Per-file read cap for archive members; larger logs are reported as skipped
/// instead of failing the whole export. Rotation (V02-54C) keeps routine logs
/// well below this cap; it remains the guard for a helper log that outgrew
/// the rotation threshold within a single session.
pub const EXPORT_MAX_FILE_BYTES: u64 = 64 * 1024 * 1024;
/// Rotate a log file once it reaches this size (12 MiB — inside the ADR's
/// 8–16 MiB suggestion band and strictly below the 64 MiB export cap).
pub const ROTATION_THRESHOLD_BYTES: u64 = 12 * 1024 * 1024;
/// Number of rotated generations (`.1` = newest … `.<N>` = oldest) kept per
/// log base; anything beyond is deleted.
pub const ROTATION_KEEP_GENERATIONS: usize = 5;

fn is_valid_token(token: &str) -> bool {
    token.len() == 32 && token.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

pub fn logs_dir(user_data: &Path) -> PathBuf {
    user_data.join(LOGS_DIR_NAME)
}

/// Helper (API/Worker) stderr log for one owned run. The token is shell- or
/// helper-generated and must be 32 hex chars — it becomes part of a file name.
pub fn helper_log_path(user_data: &Path, token: &str) -> PathBuf {
    logs_dir(user_data).join(format!("helper-{token}.stderr.log"))
}

/// Shell milestone log for one owned run.
pub fn shell_log_path(user_data: &Path, token: &str) -> PathBuf {
    logs_dir(user_data).join(format!("shell-{token}.log"))
}

/// Create/append a log file under the canonical logs root, refusing to
/// write through a non-regular entry (e.g. a symlink) planted at the path —
/// `OpenOptions` would follow it. Three layers: the final component must not
/// be a non-regular entry, the parent must canonically resolve inside the
/// logs root *before* anything is created through it, and after the open the
/// handle is re-verified as a regular file whose path still canonicalizes
/// inside the root — on refusal nothing is ever written through it. The
/// check-then-open pattern leaves a theoretical swap window; the logs
/// directory lives in per-user data (ACL tightening NOT RUN, D6), the same
/// accepted residual risk the exporter covers with its post-canonicalize
/// re-check.
pub(crate) fn open_append_regular(
    path: &Path,
    logs_canonical: &Path,
) -> std::io::Result<std::fs::File> {
    if let Ok(meta) = fs::symlink_metadata(path) {
        if meta.is_symlink() || !meta.is_file() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "log path exists but is not a regular file",
            ));
        }
    }
    let parent_inside = path
        .parent()
        .and_then(|parent| parent.canonicalize().ok())
        .is_some_and(|canonical| canonical.starts_with(logs_canonical));
    if !parent_inside {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "log parent resolves outside the canonical logs root",
        ));
    }
    let file = OpenOptions::new().create(true).append(true).open(path)?;
    let opened = file.metadata().map(|meta| meta.is_file()).unwrap_or(false)
        && path
            .canonicalize()
            .map(|canonical| canonical.starts_with(logs_canonical))
            .unwrap_or(false);
    if !opened {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "log path refused after open: not a regular file inside the logs root",
        ));
    }
    Ok(file)
}

fn is_rotatable_base_name(name: &str) -> bool {
    let shell = name.len() > "shell-".len() + ".log".len()
        && name.starts_with("shell-")
        && name.ends_with(".log");
    let helper = name.len() > "helper-".len() + ".stderr.log".len()
        && name.starts_with("helper-")
        && name.ends_with(".stderr.log");
    shell || helper
}

/// Generation `generation` of a log base file, derived from the base name —
/// never from user input — so it cannot leave the logs directory. `None`
/// only for a pathological base without a file name; rotation skips it.
fn generation_path(base: &Path, generation: usize) -> Option<PathBuf> {
    let mut name = base.file_name()?.to_os_string();
    name.push(format!(".{generation}"));
    Some(base.with_file_name(name))
}

/// Rotate one base file if it is an oversized regular file inside
/// `logs_canonical`: drop the oldest generation, shift `.i` → `.(i+1)` from
/// the newest end down (each destination was just vacated, so plain renames
/// also work on Windows, which has no overwrite-on-rename), then move the
/// base into `.1`. Returns whether a rotation happened. Symlinks are never
/// followed or moved: a link at the base path skips rotation, a symlinked
/// generation is unlinked instead of renamed.
fn rotate_file(
    base: &Path,
    logs_canonical: &Path,
    threshold: u64,
    keep: usize,
) -> std::io::Result<bool> {
    let keep = keep.max(1);
    let meta = match fs::symlink_metadata(base) {
        Ok(meta) => meta,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error),
    };
    if meta.is_symlink() || !meta.is_file() || meta.len() < threshold {
        return Ok(false);
    }
    // Belt and braces: the file must canonically live inside the logs root,
    // the same containment the exporter demands before reading a member.
    match base.canonicalize() {
        Ok(canonical) if canonical.starts_with(logs_canonical) => {}
        _ => return Ok(false),
    }
    // Deleting the oldest generation is best-effort: if it cannot be removed,
    // the first shift below fails and the caller falls back to appending.
    if let Some(oldest) = generation_path(base, keep) {
        let _ = fs::remove_file(oldest);
    }
    for generation in (1..keep).rev() {
        let Some(source) = generation_path(base, generation) else {
            continue;
        };
        let Ok(source_meta) = fs::symlink_metadata(&source) else {
            continue;
        };
        if source_meta.is_symlink() {
            fs::remove_file(&source)?;
        } else if source_meta.is_file() {
            let Some(destination) = generation_path(base, generation + 1) else {
                continue;
            };
            fs::rename(&source, &destination)?;
        }
    }
    let Some(newest) = generation_path(base, 1) else {
        return Ok(false);
    };
    fs::rename(base, newest)?;
    Ok(true)
}

/// Sweep one logs directory with explicit limits (see [`rotate_logs`]).
fn sweep_logs_dir(
    logs: &Path,
    logs_canonical: &Path,
    threshold: u64,
    keep: usize,
) -> std::io::Result<()> {
    for entry in fs::read_dir(logs)? {
        let Ok(entry) = entry else { continue };
        let name = entry.file_name().to_string_lossy().into_owned();
        // Only plain names matched against the fixed base patterns are
        // touched, and generation names derive from them — rename/delete
        // targets cannot leave the logs root.
        if name.contains('/') || name.contains('\\') || name == ".." || name == "." {
            continue;
        }
        if !is_rotatable_base_name(&name) {
            continue;
        }
        let _ = rotate_file(&entry.path(), logs_canonical, threshold, keep);
    }
    Ok(())
}

/// Session-start housekeeping: rotate logs left oversized by previous
/// sessions (`shell-*.log` / `helper-*.stderr.log` alike) and prune old
/// generations. Called from [`RunLog::create`] while nothing holds those
/// files open, and public so callers/tests can sweep explicitly. Best-effort
/// by design — a rotation failure must never block starting a session.
pub fn rotate_logs(user_data: &Path) -> std::io::Result<()> {
    let logs = logs_dir(user_data);
    let Ok(logs_canonical) = logs.canonicalize() else {
        return Ok(()); // no logs directory yet — nothing to rotate
    };
    sweep_logs_dir(
        &logs,
        &logs_canonical,
        ROTATION_THRESHOLD_BYTES,
        ROTATION_KEEP_GENERATIONS,
    )
}

/// Append-only JSON-lines writer for shell-run milestones. Identity fields
/// only (token/pid/port/origin/state) — never commands, env, or secrets.
pub struct RunLog {
    inner: std::sync::Mutex<RunLogFile>,
}

struct RunLogFile {
    /// `None` only transiently, while a rotation has the file closed.
    file: Option<std::fs::File>,
    /// Base path of the active shell log; rotation reopens this exact path.
    base: PathBuf,
    /// Canonical logs root used for the containment check before renames.
    logs_canonical: PathBuf,
    /// Rotate once the active file reaches this size.
    max_file_bytes: u64,
}

impl RunLogFile {
    fn rotate_if_large(&mut self) -> std::io::Result<()> {
        let size = match self.file.as_ref() {
            Some(file) => file.metadata()?.len(),
            // A previous rotation's reopen failed: report it instead of
            // panicking — every subsequent record reports the same failure.
            None => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::Other,
                    "run log file is unavailable",
                ))
            }
        };
        if size < self.max_file_bytes {
            return Ok(());
        }
        // Close before renaming: Windows cannot rename a file that is open
        // without FILE_SHARE_DELETE.
        self.file = None;
        let rotated = rotate_file(
            &self.base,
            &self.logs_canonical,
            self.max_file_bytes,
            ROTATION_KEEP_GENERATIONS,
        );
        // Reopen the base path either way: fresh after a successful rotation,
        // still the old (oversized or unrotatable) file when rotation was
        // skipped or failed mid-way — the open path keeps accepting writes.
        // If the reopen itself fails (planted non-regular entry, fs error)
        // the file stays `None` and records fail with an error instead of
        // panicking the shell.
        self.file = Some(open_append_regular(
            &self.base,
            &self.logs_canonical,
        )?);
        rotated.map(|_| ())
    }
}

impl RunLog {
    pub fn create(user_data: &Path, token: &str) -> std::io::Result<RunLog> {
        if !is_valid_token(token) {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "run log token must be 32 hex chars",
            ));
        }
        // Session-start sweep first (no-op when the logs dir does not exist):
        // previous sessions' shell and helper logs are rotated while they are
        // guaranteed closed — see the module docs for the cross-session
        // helper-stderr regime.
        rotate_logs(user_data)?;
        fs::create_dir_all(logs_dir(user_data))?;
        let base = shell_log_path(user_data, token);
        let logs_canonical = logs_dir(user_data).canonicalize()?;
        let file = open_append_regular(&base, &logs_canonical)?;
        Ok(RunLog {
            inner: std::sync::Mutex::new(RunLogFile {
                file: Some(file),
                base,
                logs_canonical,
                max_file_bytes: ROTATION_THRESHOLD_BYTES,
            }),
        })
    }

    pub fn record(&self, event: &str, fields: &serde_json::Value) -> std::io::Result<()> {
        let line = serde_json::json!({ "ts": unix_now(), "event": event, "fields": fields });
        // Fallible serialization — a milestone must never panic the shell,
        // even if serde_json were to refuse the payload.
        let mut payload = serde_json::to_vec(&line).map_err(|error| {
            std::io::Error::new(std::io::ErrorKind::InvalidData, error.to_string())
        })?;
        payload.push(b'\n');
        // Poison recovery: a panic in another thread while it held the lock
        // must not take down the logging path — the state is recovered via
        // into_inner instead of propagating the poison as a panic.
        let mut active = match self.inner.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        // Rotation is housekeeping: a failed rotation must never cost the
        // milestone line, so its error is only reported after the write.
        let rotation = active.rotate_if_large();
        let written = match active.file.as_mut() {
            Some(file) => file.write_all(&payload),
            None => Err(std::io::Error::new(
                std::io::ErrorKind::Other,
                "run log file is unavailable",
            )),
        };
        rotation.and(written)
    }
}

#[derive(Debug)]
pub enum ExportError {
    DestinationNotAbsolute,
    DestinationNoFileName,
    DestinationParentMissing,
    DestinationHasDotComponents,
    DestinationIsSymlink,
    DestinationIsDirectory,
    DestinationInsideUserData,
    Io(std::io::Error),
}

impl std::fmt::Display for ExportError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ExportError::DestinationNotAbsolute => write!(f, "导出目标必须是绝对路径"),
            ExportError::DestinationNoFileName => write!(f, "导出目标缺少文件名"),
            ExportError::DestinationParentMissing => write!(f, "导出目标的上级目录不存在"),
            ExportError::DestinationHasDotComponents => write!(f, "导出目标不能包含 . / .. 路径成分"),
            ExportError::DestinationIsSymlink => write!(f, "导出目标不能是符号链接"),
            ExportError::DestinationIsDirectory => write!(f, "导出目标已是目录"),
            ExportError::DestinationInsideUserData => {
                write!(f, "导出目标不能位于用户数据根之内")
            }
            ExportError::Io(error) => write!(f, "导出失败: {error}"),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct SkippedEntry {
    pub name: String,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ExportReport {
    pub destination: PathBuf,
    pub files: Vec<String>,
    pub skipped: Vec<SkippedEntry>,
    pub total_bytes: u64,
}

/// Validate the user-chosen destination: absolute, no `.`/`..` components,
/// existing (non-symlink) parent, and — after canonicalizing the parent —
/// never inside the user-data root. Returns the canonical destination path.
fn validate_destination(user_data: &Path, destination: &Path) -> Result<PathBuf, ExportError> {
    if !destination.is_absolute() {
        return Err(ExportError::DestinationNotAbsolute);
    }
    if destination
        .components()
        .any(|c| matches!(c, Component::ParentDir | Component::CurDir))
    {
        return Err(ExportError::DestinationHasDotComponents);
    }
    let file_name = destination
        .file_name()
        .ok_or(ExportError::DestinationNoFileName)?;
    let parent = destination
        .parent()
        .ok_or(ExportError::DestinationNoFileName)?;
    if destination.is_symlink() {
        return Err(ExportError::DestinationIsSymlink);
    }
    if destination.is_dir() {
        return Err(ExportError::DestinationIsDirectory);
    }
    let parent_canonical = parent
        .canonicalize()
        .map_err(|error| match error.kind() {
            std::io::ErrorKind::NotFound => ExportError::DestinationParentMissing,
            _ => ExportError::Io(error),
        })?;
    let user_data_canonical =
        user_data.canonicalize().map_err(ExportError::Io)?;
    let destination_canonical = parent_canonical.join(file_name);
    if destination_canonical.starts_with(&user_data_canonical) {
        return Err(ExportError::DestinationInsideUserData);
    }
    Ok(destination_canonical)
}

/// Recursively collect regular files under `root` (never following symlinks),
/// verifying every member stays canonically inside the root. Returns
/// `(relative_member_name, path)` pairs plus skip reasons.
fn collect_members(
    dir: &Path,
    root_canonical: &Path,
    relative: &str,
    members: &mut Vec<(String, PathBuf, u64)>,
    skipped: &mut Vec<SkippedEntry>,
) -> std::io::Result<()> {
    for entry in fs::read_dir(dir)? {
        let entry = match entry {
            Ok(entry) => entry,
            Err(error) => {
                skipped.push(SkippedEntry {
                    name: relative.to_string(),
                    reason: format!("readdir: {error}"),
                });
                continue;
            }
        };
        let name = entry.file_name().to_string_lossy().into_owned();
        let member = if relative.is_empty() {
            name.clone()
        } else {
            format!("{relative}/{name}")
        };
        let file_type = match entry.file_type() {
            Ok(t) => t,
            Err(error) => {
                skipped.push(SkippedEntry { name: member, reason: format!("stat: {error}") });
                continue;
            }
        };
        let path = entry.path();
        if file_type.is_symlink() {
            skipped.push(SkippedEntry { name: member, reason: "symlink".into() });
            continue;
        }
        if file_type.is_dir() {
            collect_members(&path, root_canonical, &member, members, skipped)?;
            continue;
        }
        if !file_type.is_file() {
            skipped.push(SkippedEntry { name: member, reason: "not_a_regular_file".into() });
            continue;
        }
        // Belt and braces: the canonical path must still live under the logs
        // root (a race-swapped or hard-linked escape is skipped, not read).
        match path.canonicalize() {
            Ok(canonical) if canonical.starts_with(root_canonical) => {}
            _ => {
                skipped.push(SkippedEntry { name: member, reason: "escaped_logs_root".into() });
                continue;
            }
        }
        if member.split('/').any(|part| part == ".." || part.is_empty()) {
            skipped.push(SkippedEntry { name: member, reason: "unsafe_member_name".into() });
            continue;
        }
        let size = entry.metadata().map(|m| m.len()).unwrap_or(0);
        if size > EXPORT_MAX_FILE_BYTES {
            skipped.push(SkippedEntry { name: member, reason: "too_large".into() });
            continue;
        }
        members.push((member, path, size));
    }
    Ok(())
}

/// Archive `<user_data>/logs/` into a store-only ZIP at `destination`.
/// The destination must resolve outside the user-data root (both directions
/// of the boundary are enforced — see the module docs).
pub fn export_logs_zip(
    user_data: &Path,
    destination: &Path,
) -> Result<ExportReport, ExportError> {
    let destination_canonical = validate_destination(user_data, destination)?;
    let logs = logs_dir(user_data);
    let logs_canonical = logs.canonicalize().map_err(ExportError::Io)?;

    let mut members: Vec<(String, PathBuf, u64)> = Vec::new();
    let mut skipped: Vec<SkippedEntry> = Vec::new();
    collect_members(
        &logs,
        &logs_canonical,
        "",
        &mut members,
        &mut skipped,
    )
    .map_err(ExportError::Io)?;
    members.sort_by(|a, b| a.0.cmp(&b.0));

    let mut total_bytes = 0u64;
    let mut included: Vec<String> = Vec::new();
    let (dos_date, dos_time) = dos_date_time(unix_now());
    let mut zip = ZipWriter::new();

    let manifest = serde_json::json!({
        "version": 1,
        "generated_at": unix_now(),
        "source": "user-data:logs",
        "included": members.iter().map(|(name, _, size)| serde_json::json!({
            "name": name, "size": size,
        })).collect::<Vec<_>>(),
        "skipped": skipped,
    });
    zip.add_file(
        "manifest.json",
        serde_json::to_string(&manifest).unwrap().as_bytes(),
        dos_date,
        dos_time,
    );

    for (member, path, size) in &members {
        let data = fs::read(path).map_err(ExportError::Io)?;
        if data.len() as u64 != *size || data.len() as u64 > EXPORT_MAX_FILE_BYTES {
            skipped.push(SkippedEntry {
                name: member.clone(),
                reason: "changed_during_export".into(),
            });
            continue;
        }
        total_bytes += data.len() as u64;
        included.push(member.clone());
        zip.add_file(member, &data, dos_date, dos_time);
    }

    // Write through a pending sibling and rename, so a failed write never
    // leaves a truncated archive at the user-chosen path.
    let file_name = destination_canonical
        .file_name()
        .ok_or(ExportError::DestinationNoFileName)?
        .to_string_lossy()
        .into_owned();
    let pending = destination_canonical.with_file_name(format!("{file_name}.pending"));
    fs::write(&pending, zip.finish()).map_err(ExportError::Io)?;
    if destination_canonical.is_file() {
        fs::remove_file(&destination_canonical).map_err(ExportError::Io)?;
    }
    fs::rename(&pending, &destination_canonical).map_err(ExportError::Io)?;

    Ok(ExportReport {
        destination: destination_canonical,
        files: included,
        skipped,
        total_bytes,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_user_data(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "mangaflow-desktop-logs-{tag}-{}-{}",
            std::process::id(),
            crate::protocol::new_token()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn destination_validation_rejects_traversal_and_user_data_targets() {
        let user_data = temp_user_data("dest");
        let inside = user_data.join("export.zip");
        assert!(matches!(
            validate_destination(&user_data, &inside),
            Err(ExportError::DestinationInsideUserData)
        ));
        // A `..` path that lexically escapes but resolves outside is still
        // rejected up front: the shell never normalizes user input silently.
        assert!(matches!(
            validate_destination(&user_data, &user_data.join("x/../../escape.zip")),
            Err(ExportError::DestinationHasDotComponents)
        ));
        assert!(matches!(
            validate_destination(&user_data, Path::new("relative.zip")),
            Err(ExportError::DestinationNotAbsolute)
        ));
        assert!(matches!(
            validate_destination(&user_data, &user_data.join("no-such-parent/e.zip")),
            Err(ExportError::DestinationParentMissing)
        ));
        // A symlink destination (even pointing outside) is refused.
        let outside = std::env::temp_dir().join(format!("mfd-dest-{}.zip", crate::protocol::new_token()));
        std::os::unix::fs::symlink(&outside, user_data.join("link.zip")).unwrap();
        assert!(matches!(
            validate_destination(&user_data, &user_data.join("link.zip")),
            Err(ExportError::DestinationIsSymlink)
        ));
        let _ = fs::remove_file(user_data.join("link.zip"));
        let _ = fs::remove_dir_all(&user_data);
        let _ = fs::remove_file(&outside);
    }

    #[test]
    fn run_log_rejects_non_hex_tokens() {
        let user_data = temp_user_data("token");
        assert!(RunLog::create(&user_data, "../evil").is_err());
        assert!(RunLog::create(&user_data, "ZZZZ").is_err());
        assert!(RunLog::create(&user_data, "ab".repeat(16).as_str()).is_ok());
        let _ = fs::remove_dir_all(&user_data);
    }

    #[test]
    fn rotation_shifts_generations_and_prunes_oldest() {
        let user_data = temp_user_data("rotate");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        let logs_canonical = logs.canonicalize().unwrap();
        let base = logs.join(format!("shell-{}.log", "1".repeat(32)));

        // Seven rotations with keep=3: generations hold only the newest
        // three rounds, the oldest is deleted each time, and the base is
        // vacated (the sweep regime leaves re-creation to the next writer).
        for round in 0..7 {
            let content = format!("round-{round}-").repeat(4);
            fs::write(&base, &content).unwrap();
            assert!(rotate_file(&base, &logs_canonical, 10, 3).unwrap());
            assert!(!base.exists());
        }
        assert_eq!(
            fs::read_to_string(generation_path(&base, 1).unwrap()).unwrap(),
            "round-6-".repeat(4)
        );
        assert_eq!(
            fs::read_to_string(generation_path(&base, 2).unwrap()).unwrap(),
            "round-5-".repeat(4)
        );
        assert_eq!(
            fs::read_to_string(generation_path(&base, 3).unwrap()).unwrap(),
            "round-4-".repeat(4)
        );
        assert!(!generation_path(&base, 4).unwrap().exists());
        let _ = fs::remove_dir_all(&user_data);
    }

    #[test]
    fn rotation_never_follows_symlinks_or_leaves_the_logs_root() {
        let user_data = temp_user_data("symlink");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        let logs_canonical = logs.canonicalize().unwrap();

        // A symlink planted at a base path is neither followed nor moved:
        // rotation skips it and its target is untouched.
        let outside = std::env::temp_dir()
            .join(format!("mfd-rotate-out-{}.log", crate::protocol::new_token()));
        fs::write(&outside, "outside".repeat(8)).unwrap();
        #[cfg(unix)]
        {
            let linked = logs.join(format!("shell-{}.log", "a".repeat(32)));
            std::os::unix::fs::symlink(&outside, &linked).unwrap();
            assert!(!rotate_file(&linked, &logs_canonical, 8, 5).unwrap());
            assert!(fs::symlink_metadata(&linked).unwrap().is_symlink());
            assert_eq!(fs::read(&outside).unwrap(), b"outside".repeat(8));
            let _ = fs::remove_file(&linked);
        }

        // A base that does not canonically live inside the logs root is left
        // alone (simulated here with a foreign containment root).
        let base = logs.join(format!("shell-{}.log", "b".repeat(32)));
        fs::write(&base, "x".repeat(64)).unwrap();
        let other_root = std::env::temp_dir()
            .join(format!("mfd-rotate-other-{}", crate::protocol::new_token()));
        fs::create_dir_all(&other_root).unwrap();
        assert!(!rotate_file(&base, &other_root.canonicalize().unwrap(), 8, 5).unwrap());
        assert_eq!(fs::read_to_string(&base).unwrap(), "x".repeat(64));
        let _ = fs::remove_dir_all(&other_root);

        // A symlinked generation is unlinked instead of renamed; its target
        // file survives outside the logs root.
        #[cfg(unix)]
        {
            let base = logs.join(format!("shell-{}.log", "c".repeat(32)));
            fs::write(&base, "y".repeat(64)).unwrap();
            let gen_target = std::env::temp_dir()
                .join(format!("mfd-rotate-gen-{}.log", crate::protocol::new_token()));
            fs::write(&gen_target, "gen-target").unwrap();
            let generation = generation_path(&base, 1).unwrap();
            std::os::unix::fs::symlink(&gen_target, &generation).unwrap();
            assert!(rotate_file(&base, &logs_canonical, 8, 5).unwrap());
            assert_eq!(fs::read(&gen_target).unwrap(), b"gen-target");
            assert!(!fs::symlink_metadata(&generation).unwrap().is_symlink());
            assert_eq!(fs::read_to_string(&generation).unwrap(), "y".repeat(64));
            let _ = fs::remove_file(&gen_target);
        }

        let _ = fs::remove_dir_all(&user_data);
        let _ = fs::remove_file(&outside);
    }

    #[test]
    fn session_sweep_rotates_only_oversized_base_files() {
        let user_data = temp_user_data("sweep");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        let logs_canonical = logs.canonicalize().unwrap();

        let big_shell = logs.join(format!("shell-{}.log", "1".repeat(32)));
        let big_helper = logs.join(format!("helper-{}.stderr.log", "2".repeat(32)));
        let small_helper = logs.join(format!("helper-{}.stderr.log", "3".repeat(32)));
        // A generation file must not be treated as a rotatable base.
        let generation = logs.join(format!("shell-{}.log.1", "4".repeat(32)));
        let unrelated = logs.join("unrelated.log");
        fs::write(&big_shell, "s".repeat(64)).unwrap();
        fs::write(&big_helper, "h".repeat(64)).unwrap();
        fs::write(&small_helper, "tiny").unwrap();
        fs::write(&generation, "old generation").unwrap();
        fs::write(&unrelated, "u".repeat(64)).unwrap();

        sweep_logs_dir(&logs, &logs_canonical, 32, 5).unwrap();

        assert!(!big_shell.exists());
        assert!(generation_path(&big_shell, 1).unwrap().exists());
        assert!(!big_helper.exists());
        assert!(generation_path(&big_helper, 1).unwrap().exists());
        assert_eq!(fs::read_to_string(&small_helper).unwrap(), "tiny");
        assert_eq!(fs::read_to_string(&generation).unwrap(), "old generation");
        assert_eq!(fs::read_to_string(&unrelated).unwrap(), "u".repeat(64));
        let _ = fs::remove_dir_all(&user_data);
    }

    #[test]
    #[cfg(unix)]
    fn run_log_create_refuses_to_write_through_a_symlink() {
        let user_data = temp_user_data("opensymlink");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        let outside = std::env::temp_dir()
            .join(format!("mfd-open-{}.log", crate::protocol::new_token()));
        fs::write(&outside, "secret").unwrap();
        let token = "ab".repeat(16);
        std::os::unix::fs::symlink(&outside, shell_log_path(&user_data, &token)).unwrap();
        assert!(RunLog::create(&user_data, &token).is_err());
        assert_eq!(fs::read_to_string(&outside).unwrap(), "secret");
        let _ = fs::remove_dir_all(&user_data);
        let _ = fs::remove_file(&outside);
    }

    #[test]
    fn open_append_regular_refuses_paths_outside_the_logs_root() {
        let user_data = temp_user_data("containment");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        let logs_canonical = logs.canonicalize().unwrap();

        // A path outside the logs root is refused before anything is created
        // through it, even though it is a perfectly regular target.
        let outside = std::env::temp_dir()
            .join(format!("mfd-open-outside-{}.log", crate::protocol::new_token()));
        let error = open_append_regular(&outside, &logs_canonical).unwrap_err();
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
        assert!(!outside.exists(), "nothing may be created outside the root");

        // A path that only lexically sits inside the logs root but resolves
        // elsewhere through a symlinked directory is refused as well.
        #[cfg(unix)]
        {
            let outside_dir = std::env::temp_dir()
                .join(format!("mfd-open-dir-{}", crate::protocol::new_token()));
            fs::create_dir_all(&outside_dir).unwrap();
            let planted = logs.join("planted-dir");
            std::os::unix::fs::symlink(&outside_dir, &planted).unwrap();
            let error =
                open_append_regular(&planted.join("x.log"), &logs_canonical).unwrap_err();
            assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
            assert_eq!(
                fs::read_dir(&outside_dir).unwrap().count(),
                0,
                "nothing may be written through the planted link"
            );
            let _ = fs::remove_dir_all(&outside_dir);
        }

        // The happy path still appends inside the logs root.
        let inside = logs.join(format!("shell-{}.log", "d".repeat(32)));
        open_append_regular(&inside, &logs_canonical).unwrap();
        let _ = fs::remove_dir_all(&user_data);
    }

    #[test]
    fn run_log_record_survives_mutex_poisoning() {
        let user_data = temp_user_data("poison");
        let token = "cd".repeat(16);
        let run_log = RunLog::create(&user_data, &token).unwrap();

        // Deliberately poison the mutex by panicking while the lock is held.
        let default_hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(|_| {}));
        let poisoned = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _guard = run_log.inner.lock().expect("hold the lock to poison it");
            panic!("deliberate: poison the run log mutex");
        }));
        std::panic::set_hook(default_hook);
        assert!(poisoned.is_err());

        // The next milestone is still written: the poison is recovered via
        // into_inner instead of panicking the caller.
        run_log
            .record("after_poison", &serde_json::json!({}))
            .unwrap();
        let log = fs::read_to_string(shell_log_path(&user_data, &token)).unwrap();
        assert!(log.contains("\"after_poison\""), "{log}");
        let _ = fs::remove_dir_all(&user_data);
    }
}
