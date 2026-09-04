//! Unified desktop log layout and export (V02-54B, ADR §4.5).
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

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Component, Path, PathBuf};

use serde::Serialize;

use crate::protocol::unix_now;
use crate::ziparch::{dos_date_time, ZipWriter};

pub const LOGS_DIR_NAME: &str = "logs";
/// Per-file read cap for archive members; larger logs are reported as skipped
/// instead of failing the whole export. Log rotation is NOT implemented yet
/// (tracked in the ADR D6 matrix), so the cap is the only size guard.
pub const EXPORT_MAX_FILE_BYTES: u64 = 64 * 1024 * 1024;

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

/// Append-only JSON-lines writer for shell-run milestones. Identity fields
/// only (token/pid/port/origin/state) — never commands, env, or secrets.
pub struct RunLog {
    file: std::sync::Mutex<std::fs::File>,
}

impl RunLog {
    pub fn create(user_data: &Path, token: &str) -> std::io::Result<RunLog> {
        if !is_valid_token(token) {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "run log token must be 32 hex chars",
            ));
        }
        fs::create_dir_all(logs_dir(user_data))?;
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(shell_log_path(user_data, token))?;
        Ok(RunLog {
            file: std::sync::Mutex::new(file),
        })
    }

    pub fn record(&self, event: &str, fields: &serde_json::Value) -> std::io::Result<()> {
        let line = serde_json::json!({ "ts": unix_now(), "event": event, "fields": fields });
        let mut file = self.file.lock().expect("run log lock");
        file.write_all(serde_json::to_string(&line).unwrap().as_bytes())?;
        file.write_all(b"\n")
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
}
