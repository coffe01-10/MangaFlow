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
//! housekeeping: a failed sweep never blocks a session start, and a failed
//! in-session rotation is reported to the caller and retried on the next
//! record (which first tries to recover the log handle) instead of panicking
//! the shell. #150 hardens that retry loop two ways: the base is renamed to
//! a staging name BEFORE any generation is shifted — a locked base fails
//! with zero history loss — and after
//! [`ROTATION_MAX_CONSECUTIVE_FAILURES`] consecutive failures a circuit
//! breaker stops further rotation attempts for the process. The sweep
//! assumes no concurrent shell shares the user-data root — the
//! single-instance mutex is NOT RUN on real hardware (D4) — so there is no
//! liveness check that would exclude a live session's files.

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
/// Rotate a log file once it reaches this size. 12 MiB is this crate's own
/// choice — the ADR requires rotation but names no numeric band — and is
/// strictly below the 64 MiB export cap.
pub const ROTATION_THRESHOLD_BYTES: u64 = 12 * 1024 * 1024;
/// Number of rotated generations (`.1` = newest … `.<N>` = oldest) kept per
/// log base; anything beyond is deleted.
pub const ROTATION_KEEP_GENERATIONS: usize = 5;
/// #150 circuit breaker: after this many consecutive in-session rotation
/// failures, rotation attempts stop for the rest of the process and the
/// active file keeps accepting appends (growing past the threshold if it
/// must) instead of retrying destructively on every record while a lock
/// persists. 3 is tight enough to bound damage at a single-digit number of
/// failed attempts yet tolerant of one transient failure plus its retry.
/// The count resets only on a successful rotation; a fresh process starts
/// at zero, so the next session retries normally.
pub const ROTATION_MAX_CONSECUTIVE_FAILURES: u32 = 3;

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

/// Only the shell's and helper's own per-run base names rotate:
/// `shell-<32 hex>.log` and `helper-<32 hex>.stderr.log`. Anything else in
/// the logs directory — foreign, hand-placed, or legacy files — is left
/// untouched.
fn is_rotatable_base_name(name: &str) -> bool {
    let shell_token = name
        .strip_prefix("shell-")
        .and_then(|rest| rest.strip_suffix(".log"));
    let helper_token = name
        .strip_prefix("helper-")
        .and_then(|rest| rest.strip_suffix(".stderr.log"));
    shell_token.or(helper_token).is_some_and(is_valid_token)
}

/// Generation `generation` of a log base file, derived from the base name —
/// never from user input — so it cannot leave the logs directory. `None`
/// only for a pathological base without a file name; rotation skips it.
fn generation_path(base: &Path, generation: usize) -> Option<PathBuf> {
    let mut name = base.file_name()?.to_os_string();
    name.push(format!(".{generation}"));
    Some(base.with_file_name(name))
}

/// Remove `path` if it exists; a NotFound result is success. Anything else
/// (locked, permission, directory) propagates so callers can bail out before
/// touching further state.
fn remove_file_if_exists(path: &Path) -> std::io::Result<()> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

/// Staging name used while a rotation shifts generations around a base.
/// It matches no rotatable base pattern (`is_rotatable_base_name` requires
/// the exact `.log` / `.stderr.log` suffixes), so a leftover staging file is
/// never swept up as history by a later session; the next rotation attempt
/// removes it before staging again.
fn rotation_staging_path(base: &Path) -> Option<PathBuf> {
    let mut name = base.file_name()?.to_os_string();
    name.push(".rotating");
    Some(base.with_file_name(name))
}

/// Drop the oldest generation, then shift `.i` → `.(i+1)` from the newest
/// end down (each destination was just vacated, so plain renames also work
/// on Windows, which has no overwrite-on-rename). Symlinks are never
/// followed or moved: a symlinked generation is unlinked — its target
/// survives.
fn shift_generations_up(base: &Path, keep: usize) -> std::io::Result<()> {
    if let Some(oldest) = generation_path(base, keep) {
        remove_file_if_exists(&oldest)?;
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
    Ok(())
}

/// Rotate one base file if it is an oversized regular file inside
/// `logs_canonical`. #150 order-of-operations: the base is renamed to a
/// staging sibling FIRST — when the base is held open without
/// FILE_SHARE_DELETE (Windows) or the directory is unwritable, the rotation
/// fails here with **zero generations touched**, instead of shifting history
/// on every retry while a lock persists. Only after the base is safely
/// staged are the oldest generation dropped and the rest shifted, and any
/// failure in that phase rolls the base back to its original path before
/// the error is returned. Returns whether a rotation happened.
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
    let Some(staging) = rotation_staging_path(base) else {
        return Ok(false);
    };
    let Some(newest) = generation_path(base, 1) else {
        return Ok(false);
    };
    // A leftover staging file means an earlier rollback also failed; clear
    // it before re-staging. If it cannot be removed, stop here — no
    // generation has been touched yet.
    remove_file_if_exists(&staging)?;
    // The critical gate: a base that cannot be renamed (locked without
    // FILE_SHARE_DELETE) fails HERE, before any generation is shifted or
    // deleted — the retry loop the in-session rotation runs on every record
    // can no longer grind history away one generation per attempt (#150).
    fs::rename(base, &staging)?;
    let shifted = shift_generations_up(base, keep);
    match shifted {
        Ok(()) => match fs::rename(&staging, &newest) {
            Ok(()) => Ok(true),
            // Exotic (`.1` reoccupied mid-rotation): keep the base content
            // by rolling it back rather than losing it into staging.
            Err(error) => {
                let _ = fs::rename(&staging, base);
                Err(error)
            }
        },
        // Roll the base content back to its original path. If even the
        // rollback fails, the next attempt clears the staging leftover
        // above; the error reported is the one that aborted the shift.
        Err(error) => {
            let _ = fs::rename(&staging, base);
            Err(error)
        }
    }
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
        // Best-effort remains the contract (a sweep failure must never block
        // a session start), but the failure is no longer silently discarded:
        // it goes to stderr, the one channel a broken logging system may
        // still use without recursing into itself (#150).
        if let Err(error) = rotate_file(&entry.path(), logs_canonical, threshold, keep) {
            eprintln!("mangaflow-desktop: session-start rotation failed for {name}: {error}");
        }
    }
    Ok(())
}

/// Session-start housekeeping: rotate logs left oversized by previous
/// sessions (`shell-<32 hex>.log` / `helper-<32 hex>.stderr.log` alike) and
/// prune old generations. Called from [`RunLog::create`] while nothing holds
/// those files open, and public so callers/tests can sweep explicitly.
/// Best-effort by design — a rotation failure must never block starting a
/// session. It assumes no concurrent shell shares the user-data root
/// (single-instance mutex NOT RUN on real hardware, D4); there is no
/// liveness check that would exclude a live session's files yet.
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
    /// Consecutive failed rotation attempts; once it reaches
    /// [`ROTATION_MAX_CONSECUTIVE_FAILURES`] the circuit opens and rotation
    /// is no longer attempted (#150).
    rotation_failures: u32,
}

impl RunLogFile {
    fn rotate_if_large(&mut self) -> std::io::Result<()> {
        // A previous rotation's reopen may have failed and left no handle.
        // Retry the open first, so logging resumes as soon as the base path
        // is usable again instead of staying dead forever.
        if self.file.is_none() {
            self.file = Some(open_append_regular(&self.base, &self.logs_canonical)?);
        }
        let Some(file) = self.file.as_ref() else {
            return Err(std::io::Error::new(
                std::io::ErrorKind::Other,
                "run log file is unavailable",
            ));
        };
        if file.metadata()?.len() < self.max_file_bytes {
            return Ok(());
        }
        // Close before renaming: Windows cannot rename a file that is open
        // without FILE_SHARE_DELETE.
        self.file = None;
        // Circuit breaker (#150): while it is open, skip the rotation
        // attempt entirely — the base is reopened below and keeps accepting
        // writes, at the cost of growing past the threshold, which is the
        // lesser evil against grinding generations away on every record.
        let rotated = if self.rotation_failures >= ROTATION_MAX_CONSECUTIVE_FAILURES {
            Ok(false)
        } else {
            rotate_file(
                &self.base,
                &self.logs_canonical,
                self.max_file_bytes,
                ROTATION_KEEP_GENERATIONS,
            )
        };
        // Reopen the base path either way: fresh after a successful rotation,
        // still the old (oversized or unrotatable) file when rotation was
        // skipped or failed mid-way — the open path keeps accepting writes.
        match open_append_regular(&self.base, &self.logs_canonical) {
            Ok(reopened) => {
                self.file = Some(reopened);
                match rotated {
                    Ok(true) => {
                        self.rotation_failures = 0;
                        Ok(())
                    }
                    Ok(false) => Ok(()),
                    Err(error) => {
                        self.rotation_failures += 1;
                        if self.rotation_failures == ROTATION_MAX_CONSECUTIVE_FAILURES {
                            eprintln!(
                                "mangaflow-desktop: log rotation disabled after \
                                 {ROTATION_MAX_CONSECUTIVE_FAILURES} consecutive failures: \
                                 {error}"
                            );
                        }
                        Err(error)
                    }
                }
            }
            Err(reopen_error) => {
                // Never write through whatever now sits at the base path
                // (e.g. a symlink planted while the handle was closed) and
                // never panic the shell: report the error — the next record
                // retries the recovery above once the path is fixed.
                Err(reopen_error)
            }
        }
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
        // helper-stderr regime. Fail-soft: a sweep failure (unreadable
        // directory, stray fs error) must never block the session start.
        let _ = rotate_logs(user_data);
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
                rotation_failures: 0,
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
        // The failure also goes to stderr — never back into this logger —
        // so a persistently failing rotation is observable (#150).
        let rotation = active.rotate_if_large();
        if let Err(error) = &rotation {
            eprintln!("mangaflow-desktop: shell log rotation failed: {error}");
        }
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
    /// #149: the destination already exists and the caller has no
    /// user-confirmed overwrite (only the native save dialog grants one).
    DestinationExists,
    /// #150: a symlink/junction is planted at the `.pending` sibling the
    /// exporter writes through before the final rename.
    PendingIsSymlink,
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
            ExportError::DestinationExists => {
                write!(f, "导出目标已存在，未经用户在对话框中确认覆盖")
            }
            ExportError::PendingIsSymlink => {
                write!(f, "导出目标的 .pending 临时文件是符号链接，拒绝写入")
            }
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

/// Whether the entry at `path` is a symlink (junctions share the same
/// reparse-point metadata shape on Windows). Never follows the link —
/// `symlink_metadata` is read exactly once and only its file type is
/// inspected. Shared by the destination and the `.pending` sibling checks
/// so both refuse links the same way (#149, #150).
fn is_symlink_at(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok_and(|meta| meta.file_type().is_symlink())
}

/// Validate the user-chosen destination: absolute, no `.`/`..` components,
/// existing (non-symlink) parent, and — after canonicalizing the parent —
/// never inside the user-data root. With `allow_existing == false` (the
/// default export surface, #149) an already-existing regular destination is
/// refused: the only sanctioned overwrite is the native save dialog's
/// explicit user confirmation, represented by the caller using
/// [`export_logs_zip_overwrite`]. Returns the canonical destination path.
fn validate_destination(
    user_data: &Path,
    destination: &Path,
    allow_existing: bool,
) -> Result<PathBuf, ExportError> {
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
    if is_symlink_at(destination) {
        return Err(ExportError::DestinationIsSymlink);
    }
    if destination.is_dir() {
        return Err(ExportError::DestinationIsDirectory);
    }
    if !allow_existing && destination.is_file() {
        return Err(ExportError::DestinationExists);
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
        // Size via a fresh metadata query, NOT the enumeration entry: on
        // NTFS the directory entry's size field lags while a writer holds
        // the file open (probed: entry_meta=0 vs fs_meta=11 for a
        // just-written open log), which made every export of the ACTIVE
        // run log report "changed_during_export" on Windows. `fs::metadata`
        // sees the current size on both platforms, and the post-read
        // re-check below still catches genuine mid-export changes.
        let size = fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
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
/// of the boundary are enforced — see the module docs). #149: an
/// already-existing destination is REFUSED here — a caller that obtained the
/// user's explicit overwrite confirmation through the native save dialog
/// must use [`export_logs_zip_overwrite`] instead; nothing else may replace
/// an existing file.
pub fn export_logs_zip(user_data: &Path, destination: &Path) -> Result<ExportReport, ExportError> {
    export_logs_with(user_data, destination, false)
}

/// Confirmed-overwrite variant of [`export_logs_zip`] (#149). The one
/// legitimate caller is the shell's export command, whose destination comes
/// from a native save dialog that already prompted the user about replacing
/// the existing file. Replacing the destination remains a remove-then-rename
/// through the `.pending` sibling, never an in-place truncation.
pub fn export_logs_zip_overwrite(
    user_data: &Path,
    destination: &Path,
) -> Result<ExportReport, ExportError> {
    export_logs_with(user_data, destination, true)
}

fn export_logs_with(
    user_data: &Path,
    destination: &Path,
    overwrite_confirmed: bool,
) -> Result<ExportReport, ExportError> {
    let destination_canonical = validate_destination(user_data, destination, overwrite_confirmed)?;
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
    // leaves a truncated archive at the user-chosen path. #150: a planted
    // symlink/junction AT the pending path is refused with the same
    // detection the destination itself uses — `fs::write` would happily
    // write through the link into its target.
    let file_name = destination_canonical
        .file_name()
        .ok_or(ExportError::DestinationNoFileName)?
        .to_string_lossy()
        .into_owned();
    let pending = destination_canonical.with_file_name(format!("{file_name}.pending"));
    if is_symlink_at(&pending) {
        return Err(ExportError::PendingIsSymlink);
    }
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
            validate_destination(&user_data, &inside, false),
            Err(ExportError::DestinationInsideUserData)
        ));
        // A `..` path that lexically escapes but resolves outside is still
        // rejected up front: the shell never normalizes user input silently.
        assert!(matches!(
            validate_destination(&user_data, &user_data.join("x/../../escape.zip"), false),
            Err(ExportError::DestinationHasDotComponents)
        ));
        assert!(matches!(
            validate_destination(&user_data, Path::new("relative.zip"), false),
            Err(ExportError::DestinationNotAbsolute)
        ));
        assert!(matches!(
            validate_destination(&user_data, &user_data.join("no-such-parent/e.zip"), false),
            Err(ExportError::DestinationParentMissing)
        ));
        // A symlink destination (even pointing outside) is refused. (Unix
        // only: planting a link needs SeCreateSymbolicLinkPrivilege on
        // Windows, so that platform exercises this policy via the shared
        // `is_symlink_at` predicate instead.)
        #[cfg(unix)]
        {
            let outside =
                std::env::temp_dir().join(format!("mfd-dest-{}.zip", crate::protocol::new_token()));
            std::os::unix::fs::symlink(&outside, user_data.join("link.zip")).unwrap();
            assert!(matches!(
                validate_destination(&user_data, &user_data.join("link.zip"), false),
                Err(ExportError::DestinationIsSymlink)
            ));
            let _ = fs::remove_file(user_data.join("link.zip"));
            let _ = fs::remove_file(&outside);
        }
        let _ = fs::remove_dir_all(&user_data);
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
        let _logs_canonical = logs.canonicalize().unwrap();

        // A symlink planted at a base path is neither followed nor moved:
        // rotation skips it and its target is untouched.
        let outside = std::env::temp_dir()
            .join(format!("mfd-rotate-out-{}.log", crate::protocol::new_token()));
        fs::write(&outside, "outside".repeat(8)).unwrap();
        #[cfg(unix)]
        {
            let linked = logs.join(format!("shell-{}.log", "a".repeat(32)));
            std::os::unix::fs::symlink(&outside, &linked).unwrap();
            assert!(!rotate_file(&linked, &_logs_canonical, 8, 5).unwrap());
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
            assert!(rotate_file(&base, &_logs_canonical, 8, 5).unwrap());
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
        // Foreign names that pattern-match loosely but carry no valid token
        // are never rotated.
        let foreign_shell = logs.join("shell-notes.log");
        let foreign_helper = logs.join("helper-nothex.stderr.log");
        fs::write(&big_shell, "s".repeat(64)).unwrap();
        fs::write(&big_helper, "h".repeat(64)).unwrap();
        fs::write(&small_helper, "tiny").unwrap();
        fs::write(&generation, "old generation").unwrap();
        fs::write(&unrelated, "u".repeat(64)).unwrap();
        fs::write(&foreign_shell, "n".repeat(64)).unwrap();
        fs::write(&foreign_helper, "m".repeat(64)).unwrap();

        sweep_logs_dir(&logs, &logs_canonical, 32, 5).unwrap();

        assert!(!big_shell.exists());
        assert!(generation_path(&big_shell, 1).unwrap().exists());
        assert!(!big_helper.exists());
        assert!(generation_path(&big_helper, 1).unwrap().exists());
        assert_eq!(fs::read_to_string(&small_helper).unwrap(), "tiny");
        assert_eq!(fs::read_to_string(&generation).unwrap(), "old generation");
        assert_eq!(fs::read_to_string(&unrelated).unwrap(), "u".repeat(64));
        assert_eq!(fs::read_to_string(&foreign_shell).unwrap(), "n".repeat(64));
        assert_eq!(fs::read_to_string(&foreign_helper).unwrap(), "m".repeat(64));
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

    #[test]
    #[cfg(unix)]
    fn run_log_recovers_after_a_failed_rotation_reopen() {
        let user_data = temp_user_data("reopen");
        let token = "ef".repeat(16);
        let run_log = RunLog::create(&user_data, &token).unwrap();
        let base = shell_log_path(&user_data, &token);
        run_log.record("before", &serde_json::json!({})).unwrap();

        // Force the in-session rotation on the next record, then plant a
        // symlink at the base path. The held handle keeps pointing at the
        // original inode, so the size check passes, the handle is closed,
        // and the reopen now faces the planted link.
        fs::OpenOptions::new()
            .write(true)
            .open(&base)
            .unwrap()
            .set_len(ROTATION_THRESHOLD_BYTES)
            .unwrap();
        let link_target = std::env::temp_dir()
            .join(format!("mfd-reopen-evil-{}.log", crate::protocol::new_token()));
        fs::write(&link_target, "evil").unwrap();
        fs::remove_file(&base).unwrap();
        std::os::unix::fs::symlink(&link_target, &base).unwrap();

        // The failed reopen must surface as an error — never a panic — and
        // nothing may be written through the link.
        let error = run_log
            .record("during", &serde_json::json!({}))
            .expect_err("reopen through a planted symlink must fail");
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
        assert!(fs::symlink_metadata(&base).unwrap().is_symlink());
        assert_eq!(fs::read(&link_target).unwrap(), b"evil");

        // Fix the path: the very next record retries the open, recovers the
        // handle, and logging resumes on a fresh base file.
        fs::remove_file(&base).unwrap();
        run_log.record("after", &serde_json::json!({})).unwrap();
        let active = fs::read_to_string(&base).unwrap();
        assert!(active.contains("\"after\""), "{active}");
        assert!(!active.contains("\"during\""));

        let _ = fs::remove_dir_all(&user_data);
        let _ = fs::remove_file(&link_target);
    }

    /// #149 regression: a caller without the user's overwrite confirmation
    /// must never replace an existing file — the default export refuses,
    /// leaves the file byte-identical, and leaves no `.pending` sibling.
    #[test]
    fn export_refuses_an_existing_destination_without_confirmation() {
        let user_data = temp_user_data("exists");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        fs::write(logs.join(format!("shell-{}.log", "5".repeat(32))), "line\n").unwrap();

        let destination =
            std::env::temp_dir().join(format!("mfd-existing-{}.txt", crate::protocol::new_token()));
        fs::write(&destination, "important user document").unwrap();
        let pending = destination.with_file_name(format!(
            "{}.pending",
            destination.file_name().unwrap().to_string_lossy()
        ));

        let error = export_logs_zip(&user_data, &destination).unwrap_err();
        assert!(matches!(error, ExportError::DestinationExists), "{error}");
        assert_eq!(
            fs::read_to_string(&destination).unwrap(),
            "important user document",
            "the existing file must be untouched"
        );
        assert!(!pending.exists(), "no pending sibling may be left behind");

        // The confirmed-overwrite entry point — what the native save
        // dialog's overwrite prompt funnels into — is the only way the
        // file gets replaced.
        let report = export_logs_zip_overwrite(&user_data, &destination).unwrap();
        assert_eq!(report.files.len(), 1);
        assert_eq!(&fs::read(&destination).unwrap()[0..2], b"PK");
        assert!(!pending.exists());

        let _ = fs::remove_dir_all(&user_data);
        let _ = fs::remove_file(&destination);
    }

    /// #150 regression: a symlink planted at the `.pending` sibling is
    /// refused with the same detection the destination itself uses, and the
    /// link target is never written through. Unix uses a real symlink;
    /// Windows needs SeCreateSymbolicLinkPrivilege to plant one, so the
    /// positive case is attempted and skipped when the privilege is
    /// missing (the shared `is_symlink_at` predicate both checks run is
    /// still exercised via the negative case).
    #[test]
    fn export_refuses_a_symlink_planted_at_the_pending_sibling() {
        let user_data = temp_user_data("pendlink");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        fs::write(logs.join(format!("shell-{}.log", "6".repeat(32))), "line\n").unwrap();

        let destination =
            std::env::temp_dir().join(format!("mfd-pending-{}.zip", crate::protocol::new_token()));
        let pending = destination.with_file_name(format!(
            "{}.pending",
            destination.file_name().unwrap().to_string_lossy()
        ));
        let victim = std::env::temp_dir().join(format!(
            "mfd-pending-victim-{}.txt",
            crate::protocol::new_token()
        ));
        fs::write(&victim, "do not touch").unwrap();

        #[cfg(unix)]
        let planted = std::os::unix::fs::symlink(&victim, &pending).is_ok();
        #[cfg(windows)]
        let planted = std::os::windows::fs::symlink_file(&victim, &pending).is_ok();
        if !planted {
            eprintln!("symlink creation not permitted; running the negative case only");
        } else {
            let error = export_logs_zip(&user_data, &destination).unwrap_err();
            assert!(matches!(error, ExportError::PendingIsSymlink), "{error}");
            assert!(!destination.exists());
            assert!(is_symlink_at(&pending));
            assert_eq!(fs::read_to_string(&victim).unwrap(), "do not touch");
            let _ = fs::remove_file(&pending);
        }

        // Negative case (no privileges needed): a regular file is not a link.
        assert!(!is_symlink_at(&victim));

        let _ = fs::remove_dir_all(&user_data);
        let _ = fs::remove_file(&victim);
    }

    /// Windows lock for the rotation-failure tests: holds the base open
    /// WITHOUT FILE_SHARE_DELETE (but with READ|WRITE sharing so the
    /// logger's append-reopen keeps succeeding) — exactly the external
    /// handle shape from #150 that made renames fail while writes flowed.
    #[cfg(windows)]
    struct RenameLock(windows::Win32::Foundation::HANDLE);

    #[cfg(windows)]
    impl Drop for RenameLock {
        fn drop(&mut self) {
            unsafe {
                let _ = windows::Win32::Foundation::CloseHandle(self.0);
            }
        }
    }

    #[cfg(windows)]
    fn lock_base_against_rename(path: &Path) -> RenameLock {
        use std::os::windows::ffi::OsStrExt;
        use windows::Win32::Storage::FileSystem::{
            CreateFileW, FILE_ATTRIBUTE_NORMAL, FILE_CREATION_DISPOSITION,
            FILE_FLAGS_AND_ATTRIBUTES, FILE_SHARE_MODE, FILE_SHARE_READ, FILE_SHARE_WRITE,
            OPEN_EXISTING,
        };
        let wide = path
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect::<Vec<u16>>();
        let handle = unsafe {
            CreateFileW(
                windows::core::PCWSTR(wide.as_ptr()),
                0x8000_0000u32, // GENERIC_READ
                FILE_SHARE_MODE(FILE_SHARE_READ.0 | FILE_SHARE_WRITE.0),
                None,
                FILE_CREATION_DISPOSITION(OPEN_EXISTING.0),
                FILE_FLAGS_AND_ATTRIBUTES(FILE_ATTRIBUTE_NORMAL.0),
                None,
            )
            .expect("open the base without FILE_SHARE_DELETE for the lock test")
        };
        RenameLock(handle)
    }

    /// Unix equivalent: strips the logs directory's write permission, which
    /// fails the base rename while appends to the open file keep working —
    /// the same failure shape as a Windows no-DELETE-share handle.
    #[cfg(unix)]
    struct DirWriteLock<'a>(&'a Path);

    #[cfg(unix)]
    impl<'a> DirWriteLock<'a> {
        fn lock(dir: &'a Path) -> Self {
            let mut permissions = fs::metadata(dir).unwrap().permissions();
            std::os::unix::fs::PermissionsExt::set_mode(&mut permissions, 0o555);
            fs::set_permissions(dir, permissions).unwrap();
            DirWriteLock(dir)
        }
    }

    #[cfg(unix)]
    impl Drop for DirWriteLock<'_> {
        fn drop(&mut self) {
            let mut permissions = fs::metadata(self.0).unwrap().permissions();
            std::os::unix::fs::PermissionsExt::set_mode(&mut permissions, 0o755);
            let _ = fs::set_permissions(self.0, permissions);
        }
    }

    #[cfg(unix)]
    fn skip_when_root() -> bool {
        // Directory permissions cannot make rename fail for root.
        let euid = unsafe { libc::geteuid() };
        euid == 0
    }

    /// #150 regression: while the base cannot be renamed (Windows: held
    /// open without FILE_SHARE_DELETE; Unix: logs directory not writable),
    /// a failed rotation must not shift or delete a single generation.
    /// The old order shifted history first, so every retry ate one more
    /// generation until the archive was empty and the base grew unbounded.
    #[test]
    fn rotation_failure_on_an_unrenameable_base_loses_no_generations() {
        let user_data = temp_user_data("locked");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        let logs_canonical = logs.canonicalize().unwrap();
        let base = logs.join(format!("shell-{}.log", "7".repeat(32)));

        // Full history plus an oversized base.
        let mut expected_generations = Vec::new();
        for generation in 1..=ROTATION_KEEP_GENERATIONS {
            let content = format!("generation-{generation}");
            fs::write(generation_path(&base, generation).unwrap(), &content).unwrap();
            expected_generations.push(content);
        }
        fs::write(&base, "oversized base").unwrap();

        #[cfg(windows)]
        let lock = lock_base_against_rename(&base);
        #[cfg(unix)]
        let lock = {
            if skip_when_root() {
                eprintln!("running as root: directory permissions cannot fail a rename");
                return;
            }
            DirWriteLock::lock(&logs)
        };

        let result = rotate_file(&base, &logs_canonical, 8, ROTATION_KEEP_GENERATIONS);
        assert!(
            result.is_err(),
            "renaming the base must fail under the lock"
        );
        drop(lock);

        // Zero history loss: every generation keeps its exact content and
        // the base is untouched — not moved, not left in staging.
        assert_eq!(fs::read_to_string(&base).unwrap(), "oversized base");
        for (generation, content) in (1..=ROTATION_KEEP_GENERATIONS).zip(&expected_generations) {
            assert_eq!(
                fs::read_to_string(generation_path(&base, generation).unwrap()).unwrap(),
                *content,
                "generation {generation} must survive the failed rotation"
            );
        }
        assert!(
            !rotation_staging_path(&base).unwrap().exists(),
            "no staging leftover may remain after the failed attempt"
        );
        let _ = fs::remove_dir_all(&user_data);
    }

    /// #150 regression: `ROTATION_MAX_CONSECUTIVE_FAILURES` consecutive
    /// rotation failures open the circuit breaker — afterwards rotation is
    /// no longer attempted even after the lock is gone, while milestone
    /// lines keep flowing into the (oversized) base instead of history
    /// being ground away record by record.
    #[test]
    fn run_log_rotation_circuit_breaker_opens_after_three_failures() {
        let user_data = temp_user_data("breaker");
        let token = "8".repeat(32);
        #[cfg(unix)]
        let logs = logs_dir(&user_data);
        let run_log = RunLog::create(&user_data, &token).unwrap();
        let base = shell_log_path(&user_data, &token);
        run_log.record("warmup", &serde_json::json!({})).unwrap();
        fs::OpenOptions::new()
            .write(true)
            .open(&base)
            .unwrap()
            .set_len(ROTATION_THRESHOLD_BYTES)
            .unwrap();

        #[cfg(windows)]
        let lock = lock_base_against_rename(&base);
        #[cfg(unix)]
        let lock = {
            if skip_when_root() {
                eprintln!("running as root: directory permissions cannot fail a rename");
                return;
            }
            DirWriteLock::lock(&logs)
        };

        // Three failing rotations: every record still writes its line (the
        // failure is reported, never fatal) and surfaces the rotation error.
        for attempt in 0..ROTATION_MAX_CONSECUTIVE_FAILURES {
            let result =
                run_log.record("locked_attempt", &serde_json::json!({ "attempt": attempt }));
            assert!(
                result.is_err(),
                "rotation under the lock must surface its error"
            );
        }
        drop(lock);

        // The breaker is open: even though the base is renameable again and
        // still oversized, no rotation is attempted — no generation appears.
        run_log
            .record("after_lock", &serde_json::json!({}))
            .unwrap();
        assert!(
            !generation_path(&base, 1).unwrap().exists(),
            "the open breaker must stop rotation attempts"
        );
        let active = fs::read_to_string(&base).unwrap();
        assert!(active.contains("\"after_lock\""), "{active}");
        assert!(active.contains("\"locked_attempt\""), "{active}");

        let _ = fs::remove_dir_all(&user_data);
    }
}
