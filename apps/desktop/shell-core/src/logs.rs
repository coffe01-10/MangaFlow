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
/// Cap on the archived member count. The ZIP end-of-central-directory
/// record stores its entry count in a u16 field: a 65 536th member would
/// overflow that count and hand the user an archive every reader shows as
/// truncated. Members beyond this cap are skipped and reported
/// (`too_many_members`); the always-present manifest brings the worst-case
/// entry count to exactly the u16 maximum. (The writer itself now panics
/// past the field rather than saturating — see `ziparch::ZipWriter`.)
pub const EXPORT_MAX_MEMBERS: usize = 65_534;
/// Cap on the archive's total uncompressed size. ZIP offsets and sizes are
/// u32 fields, so an archive at or beyond 4 GiB would silently overflow
/// them and corrupt; 2 GiB keeps a wide safety margin below that for
/// store-only members. A member whose inclusion would push the running
/// total beyond this cap is skipped and reported (`archive_size_cap`);
/// smaller later members that still fit are archived.
pub const EXPORT_MAX_TOTAL_BYTES: u64 = 2 * 1024 * 1024 * 1024;
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
    token.len() == 32
        && token
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

pub fn logs_dir(user_data: &Path) -> PathBuf {
    user_data.join(LOGS_DIR_NAME)
}

/// Helper (API/Worker) stderr log for one owned run. The token is shell- or
/// helper-generated and must be 32 hex chars — it becomes part of a file name.
///
/// # Panics
///
/// Panics in debug builds when `token` is not 32 lowercase-hex chars: the
/// callers (the shell's spawn path and the tests' fixtures) always pass a
/// `new_token()` output, and a malformed token silently producing foreign
/// log names would corrupt the per-run log layout.
pub fn helper_log_path(user_data: &Path, token: &str) -> PathBuf {
    debug_assert!(is_valid_token(token), "token must be 32 hex: {token}");
    logs_dir(user_data).join(format!("helper-{token}.stderr.log"))
}

/// Shell milestone log for one owned run.
///
/// # Panics
///
/// Same token precondition as [`helper_log_path`].
pub fn shell_log_path(user_data: &Path, token: &str) -> PathBuf {
    debug_assert!(is_valid_token(token), "token must be 32 hex: {token}");
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

/// Staging name used while a rotation moves the OLDEST generation aside
/// before shifting (the plain `.rotating` sibling is reserved for the base
/// at that moment). Like `.rotating`, it matches no rotatable base pattern,
/// so a leftover never re-enters history; the shift itself removes it on
/// success and restores it on failure, and the next shift clears any
/// residue before staging again.
fn rotation_oldest_staging_path(base: &Path) -> Option<PathBuf> {
    let mut name = base.file_name()?.to_os_string();
    name.push(".rotating-oldest");
    Some(base.with_file_name(name))
}

/// Stage the oldest generation into a dedicated staging sibling, then shift
/// `.i` → `.(i+1)` from the newest end down (each destination was just
/// vacated, so plain renames also work on Windows, which has no
/// overwrite-on-rename). Symlinks are never followed or moved: a symlinked
/// generation is unlinked — its target survives. On success the ledger of
/// renames performed is returned TOGETHER WITH the staged oldest path: the
/// CALLER drops the staged file only after its own final step (the
/// staging→newest rename) has succeeded, and on any failure — the caller's
/// included — the renames are unwound and the staged oldest is renamed back
/// to its slot, so an interrupted rotation leaves the surviving generations
/// at their original slots with every generation of history intact.
fn shift_generations_up(
    base: &Path,
    keep: usize,
) -> std::io::Result<(Vec<(PathBuf, PathBuf)>, Option<PathBuf>)> {
    let mut staged_oldest: Option<PathBuf> = None;
    if let Some(oldest) = generation_path(base, keep) {
        if let Some(staging) = rotation_oldest_staging_path(base) {
            let leftover = fs::symlink_metadata(&staging).is_ok();
            let keep_occupied = fs::symlink_metadata(&oldest).is_ok();
            if leftover && keep_occupied {
                // A staging leftover next to an occupied `.keep` cannot be
                // proven to be residue: it may be the oldest generation a
                // failed unwind could not rename back (double failure), and
                // deleting it would destroy history. Fail the rotation and
                // report instead — the stranded copy stays inspectable and
                // recovery is a deliberate manual step.
                let error = std::io::Error::new(
                    std::io::ErrorKind::Other,
                    "stale rotation staging sibling; remove it manually after inspection",
                );
                eprintln!(
                    "mangaflow-desktop: rotation could not restore the staged oldest generation {}; failing this rotation",
                    oldest.display()
                );
                return Err(error);
            }
            if leftover && !keep_occupied {
                // Empty `.keep` slot + leftover is the provable case: the
                // leftover can only be the oldest generation a previous
                // failed rotation could not restore. Retry that restore;
                // if even it fails, skip this rotation entirely rather
                // than reshuffle generations around a stranded copy.
                if fs::rename(&staging, &oldest).is_err() {
                    eprintln!(
                        "mangaflow-desktop: rotation could not restore the staged oldest generation {}; skipping this rotation",
                        oldest.display()
                    );
                    return Ok((Vec::new(), None));
                }
            }
            match fs::rename(&oldest, &staging) {
                Ok(()) => staged_oldest = Some(staging),
                // No oldest generation: nothing to stage, nothing to drop.
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(error),
            }
        } else {
            remove_file_if_exists(&oldest)?;
        }
    }
    // Successful (source, destination) renames, in execution order.
    let mut renamed: Vec<(PathBuf, PathBuf)> = Vec::new();
    for generation in (1..keep).rev() {
        let Some(source) = generation_path(base, generation) else {
            continue;
        };
        let Ok(source_meta) = fs::symlink_metadata(&source) else {
            continue;
        };
        if source_meta.is_symlink() {
            match fs::remove_file(&source) {
                Ok(()) => {}
                Err(error) => {
                    // Unwind the renames FIRST (the reverse order frees the
                    // oldest slot last), then restore the staged oldest —
                    // restoring it early would overwrite a generation the
                    // unwind still needs to move back.
                    unwind_renamed_generations(&renamed);
                    restore_staged_oldest(&staged_oldest, base, keep);
                    return Err(error);
                }
            }
        } else if source_meta.is_file() {
            let Some(destination) = generation_path(base, generation + 1) else {
                continue;
            };
            match fs::rename(&source, &destination) {
                Ok(()) => renamed.push((source, destination)),
                Err(error) => {
                    // Unwind the renames FIRST (the reverse order frees the
                    // oldest slot last), then restore the staged oldest —
                    // restoring it early would overwrite a generation the
                    // unwind still needs to move back.
                    unwind_renamed_generations(&renamed);
                    restore_staged_oldest(&staged_oldest, base, keep);
                    return Err(error);
                }
            }
        }
    }
    // The staged oldest generation is handed to the caller: the drop happens
    // only after the caller's final rotation step has committed.
    Ok((renamed, staged_oldest))
}

/// Best-effort restore of the staged oldest generation during a failed
/// shift, mirroring [`unwind_renamed_generations`]'s reporting discipline.
fn restore_staged_oldest(staged: &Option<PathBuf>, base: &Path, keep: usize) {
    if let Some(staged) = staged {
        if let Some(oldest) = generation_path(base, keep) {
            // POSIX rename would silently replace an occupied slot — if the
            // unwind failed mid-way and left a displaced generation there,
            // the restore must not clobber it. Leave the staging copy in
            // place instead: the next attempt's self-heal branch treats it
            // as history and fails the rotation rather than deleting it.
            if fs::symlink_metadata(&oldest).is_ok() {
                eprintln!(
                    "mangaflow-desktop: rotation rollback found {} occupied; the staged copy stays at {}",
                    oldest.display(),
                    staged.display()
                );
                return;
            }
            if let Err(error) = fs::rename(staged, &oldest) {
                eprintln!(
                    "mangaflow-desktop: rotation rollback could not restore {}: {error}",
                    oldest.display()
                );
            }
        }
    }
}

/// Roll back the renames [`shift_generations_up`] recorded, last performed
/// first (each earlier destination slot was vacated by the step before it,
/// so the reverse order is exactly the one that can succeed). Best effort
/// by design: an unwind step that itself fails — the slot was reoccupied
/// mid-rollback, or a handle appeared without FILE_SHARE_DELETE — is
/// reported to stderr and skipped; the caller must still see the ORIGINAL
/// shift error, which is the one that aborted the rotation.
fn unwind_renamed_generations(renamed: &[(PathBuf, PathBuf)]) {
    for (source, destination) in renamed.iter().rev() {
        if let Err(error) = fs::rename(destination, source) {
            eprintln!(
                "mangaflow-desktop: rotation rollback could not restore {}: {error}",
                source.display()
            );
        }
    }
}

/// Rotate one base file if it is an oversized regular file inside
/// `logs_canonical`. #150 order-of-operations: the base is renamed to a
/// staging sibling FIRST — when the base is held open without
/// FILE_SHARE_DELETE (Windows) or the directory is unwritable, the rotation
/// fails here with **zero generations touched**, instead of shifting history
/// on every retry while a lock persists. Only after the base is safely
/// staged are the oldest generation dropped and the rest shifted; a failure
/// in that phase unwinds the renames already performed (see
/// [`shift_generations_up`]) and rolls the base back to its original path
/// before the error is returned, and a failure of the final staging→newest
/// rename unwinds the completed shift the same way before that rollback.
/// Returns whether a rotation happened.
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
    // generation has been touched yet. (Asymmetry, deliberate: this sibling
    // holds the OVERSIZED base content at rotation time — a file whose
    // rotation can simply be retried — while a `.rotating-oldest` leftover
    // holds the oldest HISTORY generation, which is why that one is never
    // deleted unconditionally; see shift_generations_up.)
    remove_file_if_exists(&staging)?;
    // The critical gate: a base that cannot be renamed (locked without
    // FILE_SHARE_DELETE) fails HERE, before any generation is shifted or
    // deleted — the retry loop the in-session rotation runs on every record
    // can no longer grind history away one generation per attempt (#150).
    fs::rename(base, &staging)?;
    let shifted = shift_generations_up(base, keep);
    match shifted {
        Ok((renamed, staged_oldest)) => match fs::rename(&staging, &newest) {
            Ok(()) => {
                // The rotation is fully committed: only now drop the staged
                // oldest generation. Deleting it earlier re-introduced an
                // unrecoverable loss for a failure of THIS rename — the
                // unwind below restores the renames and the staged file
                // goes back to its slot instead.
                if let Some(staged) = &staged_oldest {
                    if let Err(error) = remove_file_if_exists(staged) {
                        eprintln!(
                            "mangaflow-desktop: rotation could not delete the staged oldest generation {}: {error}",
                            staged.display()
                        );
                    }
                }
                Ok(true)
            }
            // Exotic (`.1` reoccupied mid-rotation): the shift has already
            // committed, so unwind it first — the same reverse rollback the
            // shift runs for its own partial failures; the unwind's first
            // step may fail on the reoccupied `.1` slot, which it reports
            // and skips — then keep the base content by rolling it back
            // rather than losing it into staging. The staged oldest is
            // restored too: nothing of the previous history is lost.
            Err(error) => {
                unwind_renamed_generations(&renamed);
                restore_staged_oldest(&staged_oldest, base, keep);
                let _ = fs::rename(&staging, base);
                Err(error)
            }
        },
        // Roll the base content back to its original path; the shift has
        // already unwound its own partial renames (including restoring the
        // staged oldest). If even the rollback fails, the next attempt
        // clears the staging leftover above; the error reported is the one
        // that aborted the shift.
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
        // The same moment sweeps stale terminal-state runtime session
        // directories (#264): the fresh session's own directory was created by
        // RuntimeLayout::create just before this and is skipped by the
        // terminal-state + grace-window predicate either way.
        if let Err(error) = rotate_logs(user_data) {
            // Fail-soft stays, but not silent (#150/#264): a whole-sweep
            // failure — e.g. a stray file parked at the logs path — must hit
            // stderr like the per-file failures below it, or the one state
            // that breaks every future rotation is the least visible.
            eprintln!(
                "mangaflow-desktop: session-start log rotation failed: {error}"
            );
        }
        // The sweep's contract promises a stderr report for its failures —
        // discarding the Result wholesale left that promise unimplemented
        // (#264): a silently failing sweep is invisible exactly when stale
        // runtime directories start to matter for forensics.
        if let Err(error) = crate::protocol::sweep_runtime_dirs(user_data) {
            eprintln!(
                "mangaflow-desktop: stale runtime-directory sweep failed: {error}"
            );
        }
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
    /// Enforced at validation time AND again atomically at placement: a
    /// file that appears at the destination while the archive is being
    /// collected and zipped is never clobbered by the no-overwrite path.
    DestinationExists,
    /// #150: the `.pending` sibling the exporter stages the archive in is
    /// occupied by a symlink/junction — planted links are refused up front,
    /// and anything that claims the path between the clear and the
    /// `create_new` staging write fails the same way. The archive is never
    /// written through a link.
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
            ExportError::DestinationHasDotComponents => {
                write!(f, "导出目标不能包含 . / .. 路径成分")
            }
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
/// an existing parent, and — after canonicalizing the parent chain — never
/// inside the user-data root. The parent chain itself may contain symlinks
/// or junctions: canonicalization resolves every reparse point to its real
/// target, and the containment check runs on that resolved path, so a
/// destination reached through a link that ultimately lands inside user
/// data is refused exactly like a direct one. With `allow_existing ==
/// false` (the default export surface, #149) an already-existing regular
/// destination is refused: the only sanctioned overwrite is the native save
/// dialog's explicit user confirmation, represented by the caller using
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
    let parent_canonical = parent.canonicalize().map_err(|error| match error.kind() {
        std::io::ErrorKind::NotFound => ExportError::DestinationParentMissing,
        _ => ExportError::Io(error),
    })?;
    let user_data_canonical = user_data.canonicalize().map_err(ExportError::Io)?;
    let destination_canonical = parent_canonical.join(file_name);
    if destination_canonical.starts_with(&user_data_canonical) {
        return Err(ExportError::DestinationInsideUserData);
    }
    Ok(destination_canonical)
}

/// Recursion depth cap for `collect_members`: the logs directory is
/// shell-written, but a same-user planted tree must degrade to a reported
/// skip instead of a stack overflow (red team 2026-09-09, issue #310).
const MAX_EXPORT_DEPTH: usize = 32;

/// Recursively collect regular files under `root` (never following symlinks),
/// verifying every member stays canonically inside the root. Returns
/// `(relative_member_name, path)` pairs plus skip reasons.
fn collect_members(
    dir: &Path,
    root_canonical: &Path,
    relative: &str,
    depth: usize,
    members: &mut Vec<(String, PathBuf, u64)>,
    skipped: &mut Vec<SkippedEntry>,
) -> std::io::Result<()> {
    if depth > MAX_EXPORT_DEPTH {
        skipped.push(SkippedEntry {
            name: relative.to_string(),
            reason: "max_depth".into(),
        });
        return Ok(());
    }
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
        let entry_name = entry.file_name();
        if entry_name.to_str().is_none() {
            // `to_string_lossy` would fold two distinct non-UTF8 names onto
            // the same replacement-char member (duplicate ZIP entries).
            // Skip and report: the file keeps existing on disk.
            skipped.push(SkippedEntry {
                name: relative.to_string(),
                reason: "non_utf8_name".into(),
            });
            continue;
        }
        let name = entry_name.to_string_lossy().into_owned();
        let member = if relative.is_empty() {
            name.clone()
        } else {
            format!("{relative}/{name}")
        };
        let file_type = match entry.file_type() {
            Ok(t) => t,
            Err(error) => {
                skipped.push(SkippedEntry {
                    name: member,
                    reason: format!("stat: {error}"),
                });
                continue;
            }
        };
        let path = entry.path();
        if file_type.is_symlink() {
            skipped.push(SkippedEntry {
                name: member,
                reason: "symlink".into(),
            });
            continue;
        }
        let staging_debris = name
            .strip_suffix(".rotating")
            .or_else(|| name.strip_suffix(".rotating-oldest"))
            .is_some_and(is_rotatable_base_name);
        if staging_debris {
            // Rotation staging debris of a rotatable base (a drop or
            // rollback that failed mid rotation): never a history member,
            // and archiving it would duplicate the oldest generation under
            // a second name. A user file that merely ends in ".rotating"
            // is NOT covered — it archives like any other member.
            skipped.push(SkippedEntry {
                name: member,
                reason: "rotation_staging".into(),
            });
            continue;
        }
        if file_type.is_dir() {
            // A subdirectory that cannot be enumerated (locked, permission
            // revoked) must not abort the whole export — the same
            // skip-and-report policy as unreadable files (#241-5b): the
            // remaining members are still worth archiving, and the failure
            // is reported against the subdirectory's member name. Only the
            // top-level logs-dir failure propagates (export_logs_with).
            if let Err(error) =
                collect_members(&path, root_canonical, &member, depth + 1, members, skipped)
            {
                skipped.push(SkippedEntry {
                    name: member,
                    reason: format!("readdir: {error}"),
                });
            }
            continue;
        }
        if !file_type.is_file() {
            skipped.push(SkippedEntry {
                name: member,
                reason: "not_a_regular_file".into(),
            });
            continue;
        }
        // Belt and braces: the canonical path must still live under the logs
        // root (a race-swapped or hard-linked escape is skipped, not read).
        match path.canonicalize() {
            Ok(canonical) if canonical.starts_with(root_canonical) => {}
            _ => {
                skipped.push(SkippedEntry {
                    name: member,
                    reason: "escaped_logs_root".into(),
                });
                continue;
            }
        }
        // Zip member names must be non-empty forward-slash relative paths.
        // A backslash is legal in Unix file names but is the ZIP format's
        // canonical path separator (and a length that no longer fits the
        // u16 name field would corrupt the archive), so any such shape is
        // skipped and reported here — `ZipWriter`'s assert is the
        // last-resort invariant, and a directory entry must never be able
        // to panic the whole export.
        if member.is_empty()
            || member.contains('\\')
            || member.len() > u16::MAX as usize
            || member
                .split('/')
                .any(|part| part == ".." || part.is_empty())
        {
            skipped.push(SkippedEntry {
                name: member,
                reason: "unsafe_member_name".into(),
            });
            continue;
        }
        // Size via a fresh metadata query, NOT the enumeration entry: on
        // NTFS the directory entry's size field lags while a writer holds
        // the file open (probed: entry_meta=0 vs fs_meta=11 for a
        // just-written open log), which made every export of the ACTIVE
        // run log report "changed_during_export" on Windows. `fs::metadata`
        // sees the current size on both platforms, and the post-read
        // re-check below still catches genuine mid-export changes.
        // No-follow size: a symlink swapped in after enumeration must not
        // have its TARGET sized (and later read) as if it were the member —
        // symlink_metadata keeps the check on the enumerated entry itself.
        let size = fs::symlink_metadata(&path).map(|m| m.len()).unwrap_or(0);
        if size > EXPORT_MAX_FILE_BYTES {
            skipped.push(SkippedEntry {
                name: member,
                reason: "too_large".into(),
            });
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
/// an existing file, and the refusal is re-enforced atomically at placement
/// (see [`place_archive`]): a file that appears at the destination while
/// the archive is being built is never clobbered.
pub fn export_logs_zip(user_data: &Path, destination: &Path) -> Result<ExportReport, ExportError> {
    export_logs_with(user_data, destination, false, ExportLimits::default())
}

/// Confirmed-overwrite variant of [`export_logs_zip`] (#149). The one
/// legitimate caller is the shell's export command, whose destination comes
/// from a native save dialog that already prompted the user about replacing
/// the existing file. The archive is still staged in the `.pending` sibling
/// and placed by [`place_archive`] — atomically on POSIX, via the
/// remove+rename fallback (with orphan cleanup) on Windows — never an
/// in-place truncation; the no-overwrite path uses the no-clobber
/// `hard_link` placement instead.
pub fn export_logs_zip_overwrite(
    user_data: &Path,
    destination: &Path,
) -> Result<ExportReport, ExportError> {
    export_logs_with(user_data, destination, true, ExportLimits::default())
}

/// Archive-shape limits applied while building the member list (see the
/// [`EXPORT_MAX_MEMBERS`] / [`EXPORT_MAX_TOTAL_BYTES`] docs). Internal and
/// injectable so tests can exercise the caps without tens of thousands of
/// files.
#[derive(Debug, Clone, Copy)]
struct ExportLimits {
    max_members: usize,
    max_total_bytes: u64,
}

impl Default for ExportLimits {
    fn default() -> Self {
        ExportLimits {
            max_members: EXPORT_MAX_MEMBERS,
            max_total_bytes: EXPORT_MAX_TOTAL_BYTES,
        }
    }
}

fn export_logs_with(
    user_data: &Path,
    destination: &Path,
    overwrite_confirmed: bool,
    limits: ExportLimits,
) -> Result<ExportReport, ExportError> {
    let destination_canonical = validate_destination(user_data, destination, overwrite_confirmed)?;
    let logs = logs_dir(user_data);
    let logs_canonical = logs.canonicalize().map_err(ExportError::Io)?;

    let mut members: Vec<(String, PathBuf, u64)> = Vec::new();
    let mut skipped: Vec<SkippedEntry> = Vec::new();
    collect_members(&logs, &logs_canonical, "", 0, &mut members, &mut skipped)
        .map_err(ExportError::Io)?;
    members.sort_by(|a, b| a.0.cmp(&b.0));

    let mut total_bytes = 0u64;
    // (name, archived size) pairs, appended only as members are actually
    // archived, so the manifest built after the loop describes the archive
    // that was really written.
    let mut included: Vec<(String, u64)> = Vec::new();
    let (dos_date, dos_time) = dos_date_time(unix_now());
    let mut zip = ZipWriter::new();

    for (member, path, size) in &members {
        // Archive-shape caps first (metadata only, before any read): the
        // member count must stay inside the EOCD's u16 entry field and the
        // running total inside the u32 offset field's safe range — beyond
        // either, the archive would silently corrupt instead of failing.
        if included.len() >= limits.max_members {
            skipped.push(SkippedEntry {
                name: member.clone(),
                reason: "too_many_members".into(),
            });
            continue;
        }
        if total_bytes.saturating_add(*size) > limits.max_total_bytes {
            skipped.push(SkippedEntry {
                name: member.clone(),
                reason: "archive_size_cap".into(),
            });
            continue;
        }
        // #241-5b: an unreadable member (locked by another process, permission
        // revoked, vanished between collect and read) joins the size-change
        // race below in the skip-and-report treatment instead of aborting the
        // whole export — the remaining members are still worth archiving.
        // The read itself is bounded by `take(EXPORT_MAX_FILE_BYTES + 1)`:
        // the collect-time size is only a snapshot, and a log still being
        // written can grow to many GiB before this line — `fs::read` would
        // buffer the whole grown file just to reject it in the re-check
        // below. The read is capped one byte PAST the limit instead: a
        // member at exactly the cap is read whole and included, one that
        // grew past it reads back over-cap and fails the re-check as
        // "changed_during_export" without ever being buffered beyond cap+1.
        let data = (|| -> std::io::Result<Vec<u8>> {
            use std::io::Read;
            let file = fs::File::open(path)?;
            let mut data = Vec::new();
            file.take(EXPORT_MAX_FILE_BYTES + 1).read_to_end(&mut data)?;
            Ok(data)
        })();
        let data = match data {
            Ok(data) => data,
            Err(error) => {
                skipped.push(SkippedEntry {
                    name: member.clone(),
                    reason: format!("read: {error}"),
                });
                continue;
            }
        };
        if data.len() as u64 != *size || data.len() as u64 > EXPORT_MAX_FILE_BYTES {
            skipped.push(SkippedEntry {
                name: member.clone(),
                reason: "changed_during_export".into(),
            });
            continue;
        }
        total_bytes += data.len() as u64;
        included.push((member.clone(), data.len() as u64));
        zip.add_file(member, &data, dos_date, dos_time);
    }

    // The manifest is built AFTER the read loop and added as the last
    // member: a member that changed mid-export then appears in its
    // skipped list, not in included — a manifest written before the loop
    // claimed contents the archive does not actually have. Zip readers
    // locate members by name through the central directory, so manifest.json
    // does not need to be the first entry.
    let manifest = serde_json::json!({
        "version": 1,
        "generated_at": unix_now(),
        "source": "user-data:logs",
        "included": included.iter().map(|(name, size)| serde_json::json!({
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

    // Write through a pending sibling so a failed write never leaves a
    // truncated archive at the user-chosen path. #150: a planted
    // symlink/junction AT the pending path is refused outright, and the
    // staging write itself can never go through a link it did not create:
    // any leftover pending entry is removed first (`remove_file` on a
    // symlink unlinks the link — it does not follow it), and the fresh file
    // is claimed with `create_new`, which fails if ANYTHING (including a
    // link replanted in the window) occupies the path.
    let file_name = destination_canonical
        .file_name()
        .ok_or(ExportError::DestinationNoFileName)?
        .to_string_lossy()
        .into_owned();
    let pending = destination_canonical.with_file_name(format!("{file_name}.pending"));
    if is_symlink_at(&pending) {
        return Err(ExportError::PendingIsSymlink);
    }
    remove_file_if_exists(&pending).map_err(ExportError::Io)?;
    let archive = zip.finish();
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&pending)
        .and_then(|mut file| file.write_all(&archive))
        .map_err(|error| match error.kind() {
            std::io::ErrorKind::AlreadyExists => ExportError::PendingIsSymlink,
            _ => ExportError::Io(error),
        })?;
    place_archive(&pending, &destination_canonical, overwrite_confirmed)?;

    Ok(ExportReport {
        destination: destination_canonical,
        files: included.into_iter().map(|(name, _)| name).collect(),
        skipped,
        total_bytes,
    })
}

/// Move the completed archive from its `pending` sibling onto
/// `destination`. #149: the `!overwrite_confirmed` path must not clobber —
/// the validation-time "destination must not exist" check can be undercut
/// by anything created at the destination while the archive was being
/// collected and zipped, and `fs::rename` on Windows replaces whatever sits
/// at the target (MOVEFILE_REPLACE_EXISTING). `hard_link` is the std-only
/// atomic no-clobber placement: it fails with [`AlreadyExists`][kind] when
/// the destination is taken — even by a file created a microsecond ago —
/// and on success the destination and the pending name are two links to
/// the same complete inode, so dropping the pending link finalizes the
/// placement. The pending sibling lives in the destination's own directory
/// (same volume), which is exactly what `hard_link` requires; note that
/// the no-overwrite path also needs hard-link SUPPORT on that filesystem —
/// fine on NTFS/ext4/APFS, but FAT/exFAT and some network volumes refuse
/// links (ERROR_NOT_SUPPORTED / EPERM / EOPNOTSUPP), which surfaces as
/// [`ExportError::Io`] — and production callers avoid that exposure: the
/// shell's export command always carries the save dialog's overwrite
/// confirmation and takes the `overwrite_confirmed` variant. When the
/// placement fails, the pending sibling this export created is removed
/// best-effort so the user's directory is not left with an orphaned,
/// fully-written archive copy.
///
/// The `overwrite_confirmed` path replaces whatever sits there — the user
/// has explicitly sanctioned it. POSIX `rename(2)` does that atomically;
/// Windows needs the historical remove+rename fallback (see `place_archive`),
/// which now also cleans up the pending sibling on failure.
///
/// [kind]: std::io::ErrorKind::AlreadyExists
fn place_archive(
    pending: &Path,
    destination: &Path,
    overwrite_confirmed: bool,
) -> Result<(), ExportError> {
    if overwrite_confirmed {
        // POSIX rename(2) atomically replaces an existing destination, so
        // the common case never has a window where the user's previous
        // archive is already gone and the new one has not landed. Windows
        // rename refuses to replace (AlreadyExists): only there does the
        // historical remove+rename run — and if any destructive step fails,
        // the pending sibling is cleaned up exactly like the hard_link
        // branch below, so a failed overwrite cannot orphan a fully
        // written archive copy next to the (possibly removed) destination.
        match fs::rename(pending, destination) {
            Ok(()) => return Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                if let Err(remove_error) = fs::remove_file(destination) {
                    let _ = remove_file_if_exists(pending);
                    return Err(ExportError::Io(remove_error));
                }
                if let Err(rename_error) = fs::rename(pending, destination) {
                    let _ = remove_file_if_exists(pending);
                    return Err(ExportError::Io(rename_error));
                }
                return Ok(());
            }
            Err(error) => {
                let _ = remove_file_if_exists(pending);
                return Err(ExportError::Io(error));
            }
        }
    }
    if let Err(error) = fs::hard_link(pending, destination) {
        // The archive never reached the destination, so the pending sibling
        // is a fully-written orphan THIS export created — remove it
        // best-effort, ignoring secondary errors: the actionable failure is
        // the placement error, and the orphan cannot be relied on to be
        // cleared later (the next no-overwrite export fails validation on
        // the — now existing — destination long before its pending cleanup).
        let _ = remove_file_if_exists(pending);
        return Err(match error.kind() {
            std::io::ErrorKind::AlreadyExists => ExportError::DestinationExists,
            _ => ExportError::Io(error),
        });
    }
    if let Err(error) = fs::remove_file(pending) {
        // The archive is already atomically in place at the destination; a
        // pending sibling that could not be unlinked is inert clutter —
        // only a confirmed-overwrite export reaches the pending cleanup
        // that clears it, because a no-overwrite export now fails
        // validation on the existing destination first — so the placement
        // still succeeds rather than reporting a failure the caller cannot
        // retry without hitting DestinationExists.
        eprintln!(
            "mangaflow-desktop: could not remove the pending export sibling {}: {error}",
            pending.display()
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    /// Table-driven boundaries for the log-name token gate: exactly 32
    /// lowercase-hex chars. The sweep and RunLog both reject anything else
    /// (case drift, length drift, charset drift), so a false positive would
    /// let a foreign name be treated as owned history.
    #[test]
    fn log_name_token_boundaries() {
        let valid = "0123456789abcdef0123456789abcdef";
        assert!(is_valid_token(valid));
        for invalid in [
            // Case drift.
            "A".repeat(32),
            // Length boundaries on both sides of 32.
            "a".repeat(31),
            "a".repeat(33),
            // Charset drift.
            "g".repeat(32),
            // Empty and near-empty.
            String::new(),
            "a".to_string(),
        ] {
            assert!(
                !is_valid_token(&invalid),
                "invalid log token must be rejected: {invalid}"
            );
        }
    }

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

    /// A `.rotating-oldest` leftover beside an occupied `.keep` slot
    /// cannot be proven to be residue: the rotation must FAIL and leave
    /// both the slot and the leftover untouched (recovery is manual).
    #[test]
    fn rotation_fails_and_preserves_an_ambiguous_oldest_staging_leftover() {
        let user_data = temp_user_data("ambiguousleft");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        let logs_canonical = logs.canonicalize().unwrap();
        let base = logs.join(format!("shell-{}.log", "7".repeat(32)));
        fs::write(generation_path(&base, 5).unwrap(), "content-5").unwrap();
        let staging = rotation_oldest_staging_path(&base).unwrap();
        fs::write(&staging, "possibly-history").unwrap();
        fs::write(&base, "oversized base").unwrap();

        let result = rotate_file(&base, &logs_canonical, 8, ROTATION_KEEP_GENERATIONS);
        assert!(result.is_err(), "{result:?}");
        assert_eq!(
            fs::read_to_string(generation_path(&base, 5).unwrap()).unwrap(),
            "content-5"
        );
        assert_eq!(fs::read_to_string(&staging).unwrap(), "possibly-history");
        assert_eq!(fs::read_to_string(&base).unwrap(), "oversized base");
        let _ = fs::remove_dir_all(&user_data);
    }

    /// An empty `.keep` slot makes the leftover unambiguously the oldest
    /// generation a failed rotation could not restore: the self-heal retry
    /// puts it back and the rotation proceeds normally (the pruned drop at
    /// the end then removes it, exactly like a normal oldest generation).
    #[test]
    fn rotation_self_heals_a_staged_oldest_leftover_into_the_empty_slot() {
        let user_data = temp_user_data("healleft");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        let logs_canonical = logs.canonicalize().unwrap();
        let base = logs.join(format!("shell-{}.log", "6".repeat(32)));
        let staging = rotation_oldest_staging_path(&base).unwrap();
        fs::write(&staging, "content-5").unwrap();
        fs::write(&base, "oversized base").unwrap();

        let rotated = rotate_file(&base, &logs_canonical, 8, ROTATION_KEEP_GENERATIONS);
        assert!(rotated.unwrap(), "the rotation must proceed after the heal");
        assert!(!staging.exists(), "the healed copy is dropped after commit");
        assert_eq!(
            fs::read_to_string(generation_path(&base, 1).unwrap()).unwrap(),
            "oversized base"
        );
        let _ = fs::remove_dir_all(&user_data);
    }

    /// The rollback restore must not rename over an occupied `.keep` slot
    /// (a double failure strands the displaced generation there); the
    /// staged copy stays behind for the next attempt's self-heal instead.
    #[test]
    fn restore_refuses_to_clobber_an_occupied_oldest_slot() {
        let user_data = temp_user_data("restoreocc");
        fs::create_dir_all(logs_dir(&user_data)).unwrap();
        let base = logs_dir(&user_data).join(format!("shell-{}.log", "8".repeat(32)));
        fs::write(generation_path(&base, 5).unwrap(), "displaced").unwrap();
        let staging = rotation_oldest_staging_path(&base).unwrap();
        fs::write(&staging, "staged-copy").unwrap();

        restore_staged_oldest(&Some(staging.clone()), &base, ROTATION_KEEP_GENERATIONS);

        assert_eq!(
            fs::read_to_string(generation_path(&base, 5).unwrap()).unwrap(),
            "displaced",
            "the occupied slot is untouched"
        );
        assert_eq!(fs::read_to_string(&staging).unwrap(), "staged-copy");
        let _ = fs::remove_dir_all(&user_data);
    }

    /// keep=1 degenerates the shift to "no renames at all": the base
    /// becomes the only generation and the previous `.1` is pruned. No
    /// panic, no leftover, exactly one generation survives.
    #[test]
    fn rotation_with_keep_1_prunes_down_to_a_single_generation() {
        let user_data = temp_user_data("keepone");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        let logs_canonical = logs.canonicalize().unwrap();
        let base = logs.join(format!("shell-{}.log", "5".repeat(32)));
        fs::write(generation_path(&base, 1).unwrap(), "old-1").unwrap();
        fs::write(&base, "fresh base").unwrap();

        let rotated = rotate_file(&base, &logs_canonical, 8, 1);
        assert!(rotated.unwrap());
        assert_eq!(
            fs::read_to_string(generation_path(&base, 1).unwrap()).unwrap(),
            "fresh base"
        );
        assert!(!generation_path(&base, 2).unwrap().exists());
        assert!(
            !rotation_oldest_staging_path(&base).unwrap().exists(),
            "the staged old generation is pruned after commit"
        );
        // The sweep regime vacates the base; the next writer re-creates it.
        assert!(!base.exists());
        let _ = fs::remove_dir_all(&user_data);
    }

    /// A failed confirmed-overwrite must not orphan the pending sibling
    /// next to an untouched (or already removed) destination. The old code
    /// removed the destination file and only then renamed, so a rename
    /// failure deleted the user's archive while leaving the `.pending`
    /// copy behind — and that branch had no cleanup at all, unlike the
    /// hard_link branch. `place_archive` is called directly because
    /// `validate_destination` refuses directory destinations long before
    /// placement, and a directory at the destination makes both the rename
    /// and the removal fail portably (EISDIR).
    #[test]
    fn overwrite_placement_failure_cleans_up_the_pending_sibling() {
        let dir = temp_user_data("placefail");
        let pending = dir.join("archive.zip.pending");
        let destination = dir.join("archive.zip");
        fs::write(&pending, "fully written archive").unwrap();
        fs::create_dir_all(&destination).unwrap();

        let error = place_archive(&pending, &destination, true).unwrap_err();
        assert!(matches!(error, ExportError::Io(_)), "{error:?}");
        assert!(destination.is_dir(), "the occupying directory is untouched");
        assert!(
            !pending.exists(),
            "a failed placement must clean up the pending sibling"
        );
        let _ = fs::remove_dir_all(&dir);
    }

    /// Minimal reader for the store-only archives this module writes: walk
    /// the central directory, find `name`, and return its stored bytes. No
    /// compression, no extra fields — exactly the layout `ZipWriter` emits,
    /// so tests can assert on manifest.json inside the produced archive.
    fn zip_member_bytes(archive: &[u8], name: &str) -> Vec<u8> {
        let eocd = archive.len() - 22;
        assert_eq!(&archive[eocd..eocd + 4], &0x0605_4b50u32.to_le_bytes());
        let entries = u16::from_le_bytes([archive[eocd + 10], archive[eocd + 11]]);
        let central_offset = u32::from_le_bytes([
            archive[eocd + 16],
            archive[eocd + 17],
            archive[eocd + 18],
            archive[eocd + 19],
        ]) as usize;
        let mut cursor = central_offset;
        for _ in 0..entries {
            assert_eq!(&archive[cursor..cursor + 4], &0x0201_4b50u32.to_le_bytes());
            let size = u32::from_le_bytes([
                archive[cursor + 24],
                archive[cursor + 25],
                archive[cursor + 26],
                archive[cursor + 27],
            ]) as usize;
            let name_len =
                u16::from_le_bytes([archive[cursor + 28], archive[cursor + 29]]) as usize;
            let local = u32::from_le_bytes([
                archive[cursor + 42],
                archive[cursor + 43],
                archive[cursor + 44],
                archive[cursor + 45],
            ]) as usize;
            let entry_name = std::str::from_utf8(&archive[cursor + 46..cursor + 46 + name_len])
                .unwrap()
                .to_owned();
            cursor += 46 + name_len;
            if entry_name == name {
                let data_at = local + 30 + name_len;
                return archive[data_at..data_at + size].to_vec();
            }
        }
        panic!("member {name} not found in the archive");
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
        let outside = std::env::temp_dir().join(format!(
            "mfd-rotate-out-{}.log",
            crate::protocol::new_token()
        ));
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
        let other_root =
            std::env::temp_dir().join(format!("mfd-rotate-other-{}", crate::protocol::new_token()));
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
            let gen_target = std::env::temp_dir().join(format!(
                "mfd-rotate-gen-{}.log",
                crate::protocol::new_token()
            ));
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
        let outside =
            std::env::temp_dir().join(format!("mfd-open-{}.log", crate::protocol::new_token()));
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
        let outside = std::env::temp_dir().join(format!(
            "mfd-open-outside-{}.log",
            crate::protocol::new_token()
        ));
        let error = open_append_regular(&outside, &logs_canonical).unwrap_err();
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
        assert!(!outside.exists(), "nothing may be created outside the root");

        // A path that only lexically sits inside the logs root but resolves
        // elsewhere through a symlinked directory is refused as well.
        #[cfg(unix)]
        {
            let outside_dir =
                std::env::temp_dir().join(format!("mfd-open-dir-{}", crate::protocol::new_token()));
            fs::create_dir_all(&outside_dir).unwrap();
            let planted = logs.join("planted-dir");
            std::os::unix::fs::symlink(&outside_dir, &planted).unwrap();
            let error = open_append_regular(&planted.join("x.log"), &logs_canonical).unwrap_err();
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

    /// Concurrent record() writers: the inner Mutex serializes whole
    /// lines, so N threads x M records must yield N*M intact JSONL lines —
    /// no interleaved or torn lines, none lost. A regression to an
    /// unsynchronized append (or per-field writes) corrupts under exactly
    /// this load; every line must still parse as an object afterwards.
    #[test]
    fn run_log_concurrent_records_yield_intact_lines() {
        use crate::protocol::new_token;
        let user_data = temp_user_data("concurrent-records");
        let token = new_token();
        let run_log = RunLog::create(&user_data, &token).unwrap();

        const THREADS: usize = 8;
        const RECORDS: usize = 50;
        let run_log = std::sync::Arc::new(run_log);
        let mut handles = Vec::new();
        for t in 0..THREADS {
            let run_log = std::sync::Arc::clone(&run_log);
            handles.push(std::thread::spawn(move || {
                for i in 0..RECORDS {
                    run_log
                        .record(
                            "spawn",
                            &serde_json::json!({ "thread": t, "i": i,
                                "pad": "x".repeat(64) }),
                        )
                        .unwrap();
                }
            }));
        }
        for handle in handles {
            handle.join().expect("record thread must not panic");
        }
        drop(run_log); // close the active file before reading it back

        let content =
            std::fs::read_to_string(shell_log_path(&user_data, &token)).unwrap();
        let lines: Vec<&str> = content.lines().collect();
        assert_eq!(
            lines.len(),
            THREADS * RECORDS,
            "a line was lost or torn: {}",
            content.len()
        );
        for line in &lines {
            let value: serde_json::Value = serde_json::from_str(line)
                .unwrap_or_else(|error| panic!("torn line {line:?}: {error}"));
            assert!(value.is_object());
        }

        let _ = std::fs::remove_dir_all(&user_data);
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
        let link_target = std::env::temp_dir().join(format!(
            "mfd-reopen-evil-{}.log",
            crate::protocol::new_token()
        ));
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
    /// still exercised via the negative case, and the junction-based
    /// sibling test below covers the privilege-free positive case on
    /// Windows).
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

    /// #150 regression, Windows: a DIRECTORY JUNCTION planted at the
    /// `.pending` sibling is refused exactly like a symlink. `mklink /J`
    /// needs no privilege (unlike `symlink_file`), so this positive case
    /// runs on every Windows machine instead of silently degrading when
    /// SeCreateSymbolicLinkPrivilege is missing — junctions share the
    /// reparse-point metadata shape `is_symlink_at` detects.
    #[test]
    #[cfg(windows)]
    fn export_refuses_a_junction_planted_at_the_pending_sibling() {
        let user_data = temp_user_data("pendjunction");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        fs::write(logs.join(format!("shell-{}.log", "f".repeat(32))), "line\n").unwrap();

        let destination =
            std::env::temp_dir().join(format!("mfd-junction-{}.zip", crate::protocol::new_token()));
        let pending = destination.with_file_name(format!(
            "{}.pending",
            destination.file_name().unwrap().to_string_lossy()
        ));
        let target = std::env::temp_dir().join(format!(
            "mfd-junction-target-{}",
            crate::protocol::new_token()
        ));
        fs::create_dir_all(&target).unwrap();
        fs::write(target.join("canary.txt"), "do not touch").unwrap();

        // `mklink` is a cmd.exe builtin; /J creates a junction without any
        // privilege. If even this fails (exotic lockdown), skip gracefully.
        let created = std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(&pending)
            .arg(&target)
            .output()
            .map(|output| output.status.success())
            .unwrap_or(false);
        if !created {
            eprintln!("junction creation failed; running the negative case only");
        } else {
            let error = export_logs_zip(&user_data, &destination).unwrap_err();
            assert!(matches!(error, ExportError::PendingIsSymlink), "{error}");
            assert!(!destination.exists());
            assert!(is_symlink_at(&pending));
            assert_eq!(
                fs::read_to_string(target.join("canary.txt")).unwrap(),
                "do not touch",
                "nothing may be written through the junction"
            );
            // Removing the junction removes the link itself, never the
            // target directory.
            fs::remove_dir(&pending).unwrap();
            assert!(target.join("canary.txt").exists());
            assert!(!destination.exists());
        }

        // Negative case (no privileges needed): a regular file is not a link.
        assert!(!is_symlink_at(&target.join("canary.txt")));

        let _ = fs::remove_dir_all(&user_data);
        let _ = fs::remove_dir_all(&target);
    }

    /// #149 TOCTOU regression: the validation-time "destination must not
    /// exist" check can be undercut by a file that appears at the
    /// destination while the archive is being collected and zipped. The
    /// no-overwrite placement must refuse atomically — `hard_link` fails
    /// with AlreadyExists — and leave the raced file's bytes untouched;
    /// only the confirmed-overwrite placement may replace it.
    #[test]
    fn no_overwrite_placement_refuses_a_destination_created_after_validation() {
        let dir = temp_user_data("clobber");
        let destination = dir.join("logs.zip");
        let pending = dir.join("logs.zip.pending");
        fs::write(&pending, "staged archive bytes").unwrap();

        // The race: a user file occupies the destination at placement time.
        fs::write(&destination, "user file that appeared late").unwrap();
        let error = place_archive(&pending, &destination, false).unwrap_err();
        assert!(matches!(error, ExportError::DestinationExists), "{error}");
        assert_eq!(
            fs::read_to_string(&destination).unwrap(),
            "user file that appeared late",
            "a file created after validation must never be clobbered"
        );
        assert!(
            !pending.exists(),
            "a failed placement must not leak the pending sibling"
        );

        // With the destination free, the no-overwrite placement succeeds and
        // leaves no pending sibling behind.
        fs::remove_file(&destination).unwrap();
        fs::write(&pending, "staged archive bytes").unwrap();
        place_archive(&pending, &destination, false).unwrap();
        assert_eq!(
            fs::read_to_string(&destination).unwrap(),
            "staged archive bytes"
        );
        assert!(!pending.exists());

        // The confirmed-overwrite placement is the one sanctioned replacement.
        fs::write(&pending, "revised archive bytes").unwrap();
        place_archive(&pending, &destination, true).unwrap();
        assert_eq!(
            fs::read_to_string(&destination).unwrap(),
            "revised archive bytes"
        );
        assert!(!pending.exists());

        let _ = fs::remove_dir_all(&dir);
    }

    /// #150 regression: a shift that fails partway must unwind the renames
    /// it already performed. Generation `.3` is a non-empty directory —
    /// the `.4 → .5` rename succeeds (its slot was vacated by staging the
    /// oldest away), then the `.2 → .3` rename fails into the directory —
    /// so the unwind must restore `.4` from `.5` and the staged `.5` from
    /// the staging sibling before the base rolls back, leaving every
    /// surviving generation at its original slot with its content intact.
    #[test]
    fn rotation_shift_failure_unwinds_already_renamed_generations() {
        let user_data = temp_user_data("unwind");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        let logs_canonical = logs.canonicalize().unwrap();
        let base = logs.join(format!("shell-{}.log", "9".repeat(32)));

        fs::write(generation_path(&base, 1).unwrap(), "content-1").unwrap();
        fs::write(generation_path(&base, 2).unwrap(), "content-2").unwrap();
        let blocked = generation_path(&base, 3).unwrap();
        fs::create_dir_all(&blocked).unwrap();
        fs::write(blocked.join("blocker.txt"), "occupied slot").unwrap();
        fs::write(generation_path(&base, 4).unwrap(), "content-4").unwrap();
        fs::write(generation_path(&base, 5).unwrap(), "content-5").unwrap();
        fs::write(&base, "oversized base").unwrap();

        let result = rotate_file(&base, &logs_canonical, 8, ROTATION_KEEP_GENERATIONS);
        assert!(result.is_err(), "renaming into an occupied slot must fail");

        // The base is rolled back and every surviving generation keeps its
        // exact content — nothing stays displaced one slot up.
        assert_eq!(fs::read_to_string(&base).unwrap(), "oversized base");
        assert_eq!(
            fs::read_to_string(generation_path(&base, 1).unwrap()).unwrap(),
            "content-1"
        );
        assert_eq!(
            fs::read_to_string(generation_path(&base, 2).unwrap()).unwrap(),
            "content-2"
        );
        assert_eq!(
            fs::read_to_string(blocked.join("blocker.txt")).unwrap(),
            "occupied slot",
            "the blocking directory itself is untouched"
        );
        assert_eq!(
            fs::read_to_string(generation_path(&base, 4).unwrap()).unwrap(),
            "content-4",
            "the already-renamed .4 must be restored from .5 by the unwind"
        );
        assert_eq!(
            fs::read_to_string(generation_path(&base, 5).unwrap()).unwrap(),
            "content-5",
            "the oldest generation is staged, not deleted: a failed shift \
             must restore it — no rotation failure may destroy history"
        );
        assert!(
            !rotation_staging_path(&base).unwrap().exists(),
            "no staging leftover may remain after the failed attempt"
        );
        let _ = fs::remove_dir_all(&user_data);
    }

    /// #150 regression: the FINAL staging→newest rename failing must unwind
    /// the shift that already committed, not only roll the base back —
    /// otherwise every surviving generation stays displaced one slot up.
    /// A directory planted at `.1` (directories are never generations, so
    /// the shift skips it) makes the staging→`.1` rename fail portably on
    /// both platforms while `.2`/`.3` have already been shifted. `.4`/`.5`
    /// are populated too, pinning the other half of the contract: the
    /// staged oldest generation survives a failure of this FINAL step
    /// (its drop happens only after the rotation fully commits).
    #[test]
    fn rotation_final_rename_failure_unwinds_the_completed_shift() {
        let user_data = temp_user_data("finalrename");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        let logs_canonical = logs.canonicalize().unwrap();
        let base = logs.join(format!("shell-{}.log", "3".repeat(32)));

        let blocked = generation_path(&base, 1).unwrap();
        fs::create_dir_all(&blocked).unwrap();
        fs::write(blocked.join("blocker.txt"), "occupied slot").unwrap();
        fs::write(generation_path(&base, 2).unwrap(), "content-2").unwrap();
        fs::write(generation_path(&base, 3).unwrap(), "content-3").unwrap();
        fs::write(generation_path(&base, 4).unwrap(), "content-4").unwrap();
        fs::write(generation_path(&base, 5).unwrap(), "content-5").unwrap();
        fs::write(&base, "oversized base").unwrap();

        let result = rotate_file(&base, &logs_canonical, 8, ROTATION_KEEP_GENERATIONS);
        assert!(
            result.is_err(),
            "renaming into the directory at .1 must fail"
        );

        // The base rolls back AND the committed shift unwinds: generations
        // end at their original slots, the blocker is untouched.
        assert_eq!(fs::read_to_string(&base).unwrap(), "oversized base");
        assert_eq!(
            fs::read_to_string(blocked.join("blocker.txt")).unwrap(),
            "occupied slot",
            "the blocking directory itself is untouched"
        );
        assert_eq!(
            fs::read_to_string(generation_path(&base, 2).unwrap()).unwrap(),
            "content-2",
            "the shifted .2 must be restored from .3 by the unwind"
        );
        assert_eq!(
            fs::read_to_string(generation_path(&base, 3).unwrap()).unwrap(),
            "content-3",
            "the shifted .3 must be restored from .4 by the unwind"
        );
        assert_eq!(
            fs::read_to_string(generation_path(&base, 4).unwrap()).unwrap(),
            "content-4",
            "the staged oldest is restored after the unwind, so .4 keeps its content"
        );
        assert_eq!(
            fs::read_to_string(generation_path(&base, 5).unwrap()).unwrap(),
            "content-5",
            "the drop only happens after the rotation fully commits — .5 survives"
        );
        assert!(
            !rotation_staging_path(&base).unwrap().exists(),
            "no staging leftover may remain after the failed attempt"
        );
        assert!(
            !rotation_oldest_staging_path(&base).unwrap().exists(),
            "no oldest-staging leftover may remain after the failed attempt"
        );
        let _ = fs::remove_dir_all(&user_data);
    }

    /// A stale regular `.pending` file left by an earlier failed export
    /// must neither block nor misreport the next export: it is removed
    /// before the `create_new` staging claim, and the export succeeds.
    #[test]
    fn export_replaces_a_stale_regular_pending_sibling() {
        let user_data = temp_user_data("stalepending");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        fs::write(logs.join(format!("shell-{}.log", "e".repeat(32))), "line\n").unwrap();

        let destination =
            std::env::temp_dir().join(format!("mfd-stale-{}.zip", crate::protocol::new_token()));
        let pending = destination.with_file_name(format!(
            "{}.pending",
            destination.file_name().unwrap().to_string_lossy()
        ));
        fs::write(
            &pending,
            "half-written archive from an earlier failed export",
        )
        .unwrap();

        let report = export_logs_zip(&user_data, &destination).unwrap();
        assert_eq!(report.files.len(), 1);
        assert_eq!(&fs::read(&destination).unwrap()[0..2], b"PK");
        assert!(
            !pending.exists(),
            "the stale pending file must be replaced, not reused"
        );

        let _ = fs::remove_dir_all(&user_data);
        let _ = fs::remove_file(&destination);
    }

    /// #241-5b regression: one unreadable member must not abort the whole
    /// export — it is skipped and reported like the size-change race, while
    /// the healthy members still land in the archive and the manifest.
    /// Windows: the victim is held open WITHOUT FILE_SHARE_READ, so its
    /// `fs::read` (GENERIC_READ) fails with a sharing violation while the
    /// collect-phase metadata/canonicalize (which request no read access)
    /// keep succeeding and the member reaches the read loop. Unix: mode 000
    /// (skipped under root — permissions cannot stop root from reading).
    #[test]
    fn export_skips_a_member_that_cannot_be_read() {
        let user_data = temp_user_data("unreadable");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        let healthy_name = format!("shell-{}.log", "a".repeat(32));
        fs::write(logs.join(&healthy_name), "healthy line\n").unwrap();
        let victim_name = format!("helper-{}.stderr.log", "b".repeat(32));
        let victim = logs.join(&victim_name);
        fs::write(&victim, "locked away\n").unwrap();

        #[cfg(windows)]
        let lock = lock_member_against_read(&victim);
        #[cfg(unix)]
        let lock = {
            if skip_when_root() {
                eprintln!("running as root: mode 000 cannot fail a read");
                return;
            }
            let mut permissions = fs::metadata(&victim).unwrap().permissions();
            std::os::unix::fs::PermissionsExt::set_mode(&mut permissions, 0o000);
            fs::set_permissions(&victim, permissions).unwrap();
            ()
        };

        let destination = std::env::temp_dir().join(format!(
            "mfd-unreadable-{}.zip",
            crate::protocol::new_token()
        ));
        let report = export_logs_zip(&user_data, &destination).unwrap();
        // Cross-platform release: on unix the lock is a zero-sized marker,
        // on Windows a real handle guard — `let _` drops both immediately.
        let _ = lock;
        #[cfg(unix)]
        {
            let mut permissions = fs::metadata(&victim).unwrap().permissions();
            std::os::unix::fs::PermissionsExt::set_mode(&mut permissions, 0o644);
            let _ = fs::set_permissions(&victim, permissions);
        }

        assert_eq!(report.files, vec![healthy_name.clone()], "{report:?}");
        assert!(
            report
                .skipped
                .iter()
                .any(|entry| entry.name == victim_name && entry.reason.starts_with("read: ")),
            "{report:?}"
        );
        assert_eq!(&fs::read(&destination).unwrap()[0..2], b"PK");

        // manifest.json inside the archive agrees with the report.
        let archive = fs::read(&destination).unwrap();
        let manifest: serde_json::Value =
            serde_json::from_slice(&zip_member_bytes(&archive, "manifest.json")).unwrap();
        let included: Vec<&str> = manifest["included"]
            .as_array()
            .unwrap()
            .iter()
            .map(|entry| entry["name"].as_str().unwrap())
            .collect();
        let skipped: Vec<&str> = manifest["skipped"]
            .as_array()
            .unwrap()
            .iter()
            .map(|entry| entry["name"].as_str().unwrap())
            .collect();
        assert_eq!(included, vec![healthy_name.as_str()], "{manifest}");
        assert!(skipped.contains(&victim_name.as_str()), "{manifest}");

        let _ = fs::remove_dir_all(&user_data);
        let _ = fs::remove_file(&destination);
    }

    /// A logs entry whose file name contains a backslash (a legal byte in
    /// Unix file names, but the ZIP format's path separator — see
    /// `ZipWriter`'s last-resort assert) must be skipped and reported, never
    /// archived: before the skip rule it panicked `desktop_export_logs` on
    /// every invocation. Windows cannot create such a file name, so the case
    /// is Unix-only.
    #[test]
    #[cfg(unix)]
    fn export_skips_members_with_backslashes_in_their_names() {
        let user_data = temp_user_data("backslash");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        fs::write(logs.join(format!("shell-{}.log", "2".repeat(32))), "line\n").unwrap();
        fs::write(logs.join("evil\\name.log"), "must not be archived\n").unwrap();

        let destination = std::env::temp_dir().join(format!(
            "mfd-backslash-{}.zip",
            crate::protocol::new_token()
        ));
        // No panic: the unsafe name is skipped instead of reaching the writer.
        let report = export_logs_zip(&user_data, &destination).unwrap();

        assert_eq!(report.files.len(), 1, "{report:?}");
        assert!(
            report
                .skipped
                .iter()
                .any(|entry| entry.name == "evil\\name.log"
                    && entry.reason == "unsafe_member_name"),
            "{report:?}"
        );
        assert_eq!(&fs::read(&destination).unwrap()[0..2], b"PK");

        let _ = fs::remove_dir_all(&user_data);
        let _ = fs::remove_file(&destination);
    }

    /// The manifest is written AFTER the read loop (#151 review finding):
    /// a member that changes between its stat and its read is excluded from
    /// the archive AND from manifest.json's included list, and is listed as
    /// changed_during_export instead. The realistic trigger is the one the
    /// exporter documents — a log still being appended to while the export
    /// runs — modeled by a monotonically growing writer thread; the many
    /// stable members sorted before the victim keep the stat→read window
    /// wide enough that the writer always lands inside it (the export is
    /// retried a bounded number of times in case a scheduler starved it).
    #[test]
    fn export_manifest_excludes_members_that_change_during_export() {
        let user_data = temp_user_data("manifest");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        for index in 0..600 {
            fs::write(logs.join(format!("a-{index:03}.log")), [b'x'; 8192]).unwrap();
        }
        let victim = logs.join("zz-victim.log");
        fs::write(&victim, "v").unwrap();

        let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let mutator = {
            let stop = std::sync::Arc::clone(&stop);
            let victim = victim.clone();
            std::thread::spawn(move || {
                while !stop.load(std::sync::atomic::Ordering::Relaxed) {
                    let Ok(mut file) = fs::OpenOptions::new().append(true).open(&victim) else {
                        break;
                    };
                    let _ = file.write_all(b"growing");
                }
            })
        };

        let destination =
            std::env::temp_dir().join(format!("mfd-manifest-{}.zip", crate::protocol::new_token()));
        let mut report = None;
        for attempt in 0..3 {
            let candidate = export_logs_zip_overwrite(&user_data, &destination).unwrap();
            if candidate
                .skipped
                .iter()
                .any(|entry| entry.name == "zz-victim.log")
            {
                report = Some(candidate);
                break;
            }
            eprintln!("writer thread did not interleave on attempt {attempt}; retrying");
        }
        stop.store(true, std::sync::atomic::Ordering::Relaxed);
        mutator.join().unwrap();
        let report = report.expect("the growing member must be skipped as changed_during_export");

        assert!(
            report
                .skipped
                .iter()
                .any(|entry| entry.name == "zz-victim.log"
                    && entry.reason == "changed_during_export"),
            "{report:?}"
        );
        assert_eq!(report.files.len(), 600, "{report:?}");

        // manifest.json inside the archive agrees with the report: the
        // victim is absent from included and present under skipped.
        let archive = fs::read(&destination).unwrap();
        let manifest: serde_json::Value =
            serde_json::from_slice(&zip_member_bytes(&archive, "manifest.json")).unwrap();
        let included: Vec<&str> = manifest["included"]
            .as_array()
            .unwrap()
            .iter()
            .map(|entry| entry["name"].as_str().unwrap())
            .collect();
        let skipped: Vec<&str> = manifest["skipped"]
            .as_array()
            .unwrap()
            .iter()
            .map(|entry| entry["name"].as_str().unwrap())
            .collect();
        assert_eq!(included.len(), 600, "{manifest}");
        assert!(!included.contains(&"zz-victim.log"), "{manifest}");
        assert!(skipped.contains(&"zz-victim.log"), "{manifest}");

        let _ = fs::remove_dir_all(&user_data);
        let _ = fs::remove_file(&destination);
    }

    /// Archive-shape caps: members beyond `max_members` (the EOCD u16 entry
    /// field) and members that would push the total beyond `max_total_bytes`
    /// (the u32 offset field's safe range) are skipped with reported reasons
    /// instead of silently wrapping the ZIP structure into a corrupt
    /// archive. The manifest inside the archive must agree with the report.
    #[test]
    fn export_caps_members_and_total_bytes_with_reported_skips() {
        let user_data = temp_user_data("caps");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        for name in ["a-one.log", "b-two.log", "c-three.log", "d-four.log"] {
            fs::write(logs.join(name), "0123456789").unwrap(); // 10 bytes each
        }

        // Member cap: 4 files sorted, only the first two archived.
        let destination_members =
            std::env::temp_dir().join(format!("mfd-caps-m-{}.zip", crate::protocol::new_token()));
        let report = export_logs_with(
            &user_data,
            &destination_members,
            false,
            ExportLimits {
                max_members: 2,
                max_total_bytes: EXPORT_MAX_TOTAL_BYTES,
            },
        )
        .unwrap();
        assert_eq!(report.files, vec!["a-one.log", "b-two.log"], "{report:?}");
        assert_eq!(
            report
                .skipped
                .iter()
                .filter(|entry| entry.reason == "too_many_members")
                .count(),
            2,
            "{report:?}"
        );

        let _ = fs::remove_file(&destination_members);

        // Size cap: all four would fit the member cap, but the 2 GiB…
        // here 25-byte… budget only takes two.
        let destination_sizes =
            std::env::temp_dir().join(format!("mfd-caps-s-{}.zip", crate::protocol::new_token()));
        let report = export_logs_with(
            &user_data,
            &destination_sizes,
            false,
            ExportLimits {
                max_members: EXPORT_MAX_MEMBERS,
                max_total_bytes: 25,
            },
        )
        .unwrap();
        assert_eq!(report.files, vec!["a-one.log", "b-two.log"], "{report:?}");
        assert_eq!(
            report
                .skipped
                .iter()
                .filter(|entry| entry.reason == "archive_size_cap")
                .count(),
            2,
            "{report:?}"
        );
        assert_eq!(report.total_bytes, 20);

        // Both archives are structurally valid and their manifests agree
        // with the reports.
        let archive = fs::read(&destination_sizes).unwrap();
        let manifest: serde_json::Value =
            serde_json::from_slice(&zip_member_bytes(&archive, "manifest.json")).unwrap();
        assert_eq!(manifest["included"].as_array().unwrap().len(), 2);
        assert_eq!(manifest["skipped"].as_array().unwrap().len(), 2);

        // With the real limits the same four files all fit.
        let destination_full =
            std::env::temp_dir().join(format!("mfd-caps-full-{}.zip", crate::protocol::new_token()));
        let report = export_logs_zip(&user_data, &destination_full).unwrap();
        assert_eq!(report.files.len(), 4, "{report:?}");
        assert!(report.skipped.is_empty(), "{report:?}");

        let _ = fs::remove_dir_all(&user_data);
        // Exact-path cleanup only: a prefix sweep over the shared temp
        // directory could delete a concurrent test process's destinations.
        for destination in [destination_members, destination_sizes, destination_full] {
            let _ = fs::remove_file(&destination);
        }
    }

    /// Boundary pin: a member landing the running total EXACTLY on the
    /// cap is included (the skip is strictly `>`), and only the next one
    /// is skipped for the size cap.
    #[test]
    fn export_includes_members_at_exactly_the_total_cap() {
        let user_data = temp_user_data("equpoe");
        let logs = logs_dir(&user_data);
        fs::create_dir_all(&logs).unwrap();
        fs::write(logs.join("a-first.log"), "1".repeat(15)).unwrap();
        fs::write(logs.join("b-second.log"), "2".repeat(10)).unwrap();
        fs::write(logs.join("c-third.log"), "3").unwrap();

        let destination =
            std::env::temp_dir().join(format!("mfd-caps-eq-{}.zip", crate::protocol::new_token()));
        let report = export_logs_with(
            &user_data,
            &destination,
            false,
            ExportLimits {
                max_members: EXPORT_MAX_MEMBERS,
                max_total_bytes: 25,
            },
        )
        .unwrap();
        assert_eq!(report.files, vec!["a-first.log", "b-second.log"], "{report:?}");
        assert_eq!(report.total_bytes, 25, "{report:?}");
        assert_eq!(
            report
                .skipped
                .iter()
                .filter(|entry| entry.reason == "archive_size_cap")
                .count(),
            1,
            "{report:?}"
        );
        let _ = fs::remove_dir_all(&user_data);
        let _ = fs::remove_file(&destination);
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

    /// Windows lock for #241-5b: holds a member open WITHOUT FILE_SHARE_READ
    /// (but with WRITE|DELETE sharing) so a later `fs::read` — which opens
    /// with GENERIC_READ — fails with a sharing violation while the collect
    /// phase's metadata and canonicalize (desired access 0) keep succeeding
    /// and the member still reaches the read loop.
    #[cfg(windows)]
    fn lock_member_against_read(path: &Path) -> RenameLock {
        use std::os::windows::ffi::OsStrExt;
        use windows::Win32::Storage::FileSystem::{
            CreateFileW, FILE_ATTRIBUTE_NORMAL, FILE_CREATION_DISPOSITION,
            FILE_FLAGS_AND_ATTRIBUTES, FILE_SHARE_DELETE, FILE_SHARE_MODE, FILE_SHARE_WRITE,
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
                FILE_SHARE_MODE(FILE_SHARE_WRITE.0 | FILE_SHARE_DELETE.0),
                None,
                FILE_CREATION_DISPOSITION(OPEN_EXISTING.0),
                FILE_FLAGS_AND_ATTRIBUTES(FILE_ATTRIBUTE_NORMAL.0),
                None,
            )
            .expect("open the member without FILE_SHARE_READ for the read-skip test")
        };
        RenameLock(handle)
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
        // Cross-platform release: on unix the lock is a zero-sized marker,
        // on Windows a real handle guard — `let _` drops both immediately.
        let _ = lock;

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
        // Cross-platform release: on unix the lock is a zero-sized marker,
        // on Windows a real handle guard — `let _` drops both immediately.
        let _ = lock;

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
