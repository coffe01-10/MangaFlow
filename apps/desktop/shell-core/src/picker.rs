//! Validated local file/directory picking (V02-54B, "本地文件选择").
//!
//! The WebView cannot read disk paths, so the shell owns the whole pick
//! surface: a native dialog returns a path, that path is validated here
//! against the same boundaries the API enforces for uploads (reference
//! images PNG/JPEG/WebP ≤ `max_upload_bytes`; source texts TXT/Markdown
//! ≤ `max_upload_bytes`, UTF-8 checked server-side), and only validated
//! paths are remembered in a session-scoped registry. The read-back command
//! re-validates on every call and refuses any path that was never picked,
//! so a compromised page cannot turn the shell into an arbitrary file
//! reader. Path traversal is structurally excluded: paths only enter via
//! the OS dialog (never typed into a WebView form), and every validation
//! canonicalizes while rejecting symlinks and `.`/`..` components.

use std::collections::HashMap;
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::sync::Mutex;

/// Mirrors `app.config.Settings.max_upload_bytes` (20 MiB); the API keeps
/// enforcing its own limit — this is the shell-side alignment, not a bypass.
pub const MAX_PICKED_FILE_BYTES: u64 = 20 * 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PickKind {
    /// 原作/正文文本 — same suffix set as `sources.py` upload.
    SourceText,
    /// 参考图/素材 — same type set as `uploads.py` `REFERENCE_IMAGE_TYPES`
    /// (PNG/JPEG/WebP; pixel/bomb checks stay server-side).
    ReferenceImage,
}

impl PickKind {
    pub fn parse(raw: &str) -> Option<PickKind> {
        match raw {
            "source_text" => Some(PickKind::SourceText),
            "reference_image" => Some(PickKind::ReferenceImage),
            _ => None,
        }
    }

    pub fn allowed_suffixes(self) -> &'static [&'static str] {
        match self {
            PickKind::SourceText => &[".txt", ".md", ".markdown"],
            PickKind::ReferenceImage => &[".png", ".jpg", ".jpeg", ".webp"],
        }
    }

    /// Dialog filter label + extensions (no dots, as rfd expects).
    pub fn dialog_filter(self) -> (&'static str, &'static [&'static str]) {
        match self {
            PickKind::SourceText => (
                "文本原文（TXT/Markdown）",
                &["txt", "md", "markdown"],
            ),
            PickKind::ReferenceImage => (
                "图片素材（PNG/JPEG/WebP）",
                &["png", "jpg", "jpeg", "webp"],
            ),
        }
    }
}

#[derive(Debug)]
pub enum PickError {
    EmptyPath,
    NotAbsolute,
    DotComponents,
    DoesNotExist,
    IsSymlink,
    NotARegularFile,
    NotADirectory,
    ForbiddenSuffix { allowed: &'static [&'static str] },
    TooLarge { size: u64, cap: u64 },
    NotRegistered,
    GrewDuringRead,
    Io(std::io::Error),
}

impl std::fmt::Display for PickError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PickError::EmptyPath => write!(f, "路径为空"),
            PickError::NotAbsolute => write!(f, "必须是绝对路径"),
            PickError::DotComponents => write!(f, "路径不能包含 . / .. 成分"),
            PickError::DoesNotExist => write!(f, "路径不存在"),
            PickError::IsSymlink => write!(f, "不允许符号链接路径"),
            PickError::NotARegularFile => write!(f, "不是常规文件"),
            PickError::NotADirectory => write!(f, "不是目录"),
            PickError::ForbiddenSuffix { allowed } => {
                write!(f, "文件类型不在允许范围内（允许：{allowed:?}）")
            }
            PickError::TooLarge { size, cap } => {
                write!(f, "文件 {size} 字节超过上限 {cap} 字节")
            }
            PickError::NotRegistered => write!(f, "该路径不是本会话中通过选择得到的，已拒绝"),
            PickError::GrewDuringRead => write!(f, "文件在读取期间超过上限"),
            PickError::Io(error) => write!(f, "读取失败: {error}"),
        }
    }
}

fn check_no_traversal(raw: &Path) -> Result<(), PickError> {
    if raw.as_os_str().is_empty() {
        return Err(PickError::EmptyPath);
    }
    if !raw.is_absolute() {
        return Err(PickError::NotAbsolute);
    }
    if raw
        .components()
        .any(|c| matches!(c, Component::ParentDir | Component::CurDir))
    {
        return Err(PickError::DotComponents);
    }
    Ok(())
}

#[derive(Debug, Clone)]
pub struct PickedFile {
    /// Canonical path (safe to hand back to the frontend as an opaque key).
    pub path: PathBuf,
    pub name: String,
    pub size_bytes: u64,
    pub kind: PickKind,
}

#[derive(Debug, Clone)]
pub struct PickedDirectory {
    pub path: PathBuf,
    pub name: String,
}

/// Validate a path that came from a native pick dialog: no traversal
/// components, no symlink anywhere in the final resolution, a regular file
/// whose canonical file name still matches what was picked, and the
/// kind-aligned suffix/size policy.
pub fn validate_picked_file(raw: &Path, kind: PickKind) -> Result<PickedFile, PickError> {
    check_no_traversal(raw)?;
    let meta = raw
        .symlink_metadata()
        .map_err(|error| match error.kind() {
            std::io::ErrorKind::NotFound => PickError::DoesNotExist,
            _ => PickError::Io(error),
        })?;
    if meta.is_symlink() {
        return Err(PickError::IsSymlink);
    }
    if !meta.is_file() {
        return Err(PickError::NotARegularFile);
    }
    let canonical = raw.canonicalize().map_err(PickError::Io)?;
    if canonical.file_name() != raw.file_name() {
        // The final component resolved through a symlink to a different name.
        return Err(PickError::IsSymlink);
    }
    let size = canonical.metadata().map_err(PickError::Io)?.len();
    let suffix = raw
        .extension()
        .map(|ext| format!(".{}", ext.to_string_lossy().to_ascii_lowercase()))
        .unwrap_or_default();
    if !kind.allowed_suffixes().contains(&suffix.as_str()) {
        return Err(PickError::ForbiddenSuffix {
            allowed: kind.allowed_suffixes(),
        });
    }
    if size > MAX_PICKED_FILE_BYTES {
        return Err(PickError::TooLarge {
            size,
            cap: MAX_PICKED_FILE_BYTES,
        });
    }
    Ok(PickedFile {
        name: raw
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default(),
        path: canonical,
        size_bytes: size,
        kind,
    })
}

/// Validate a picked directory (import roots for 原作/素材 workflows).
pub fn validate_picked_directory(raw: &Path) -> Result<PickedDirectory, PickError> {
    check_no_traversal(raw)?;
    let meta = raw
        .symlink_metadata()
        .map_err(|error| match error.kind() {
            std::io::ErrorKind::NotFound => PickError::DoesNotExist,
            _ => PickError::Io(error),
        })?;
    if meta.is_symlink() {
        return Err(PickError::IsSymlink);
    }
    if !meta.is_dir() {
        return Err(PickError::NotADirectory);
    }
    let canonical = raw.canonicalize().map_err(PickError::Io)?;
    if canonical.file_name() != raw.file_name() {
        return Err(PickError::IsSymlink);
    }
    Ok(PickedDirectory {
        name: raw
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default(),
        path: canonical,
    })
}

/// Session-scoped registry of validated picks. Keys are canonical paths;
/// only registered paths may ever be read back by the page.
#[derive(Default)]
pub struct PickedRegistry {
    entries: Mutex<HashMap<PathBuf, PickKind>>,
}

impl PickedRegistry {
    pub fn new() -> PickedRegistry {
        PickedRegistry::default()
    }

    pub fn register(&self, picked: &PickedFile) {
        self.entries
            .lock()
            .expect("picked registry lock")
            .insert(picked.path.clone(), picked.kind);
    }

    pub fn kind_of(&self, canonical: &Path) -> Option<PickKind> {
        self.entries
            .lock()
            .expect("picked registry lock")
            .get(canonical)
            .copied()
    }
}

/// Read a previously picked file back for the page (which then uploads it
/// through the ordinary API upload endpoints). Re-validates the full policy
/// and registry membership on every call — a pick is a capability, not a
/// permanent grant.
pub fn read_registered_file(
    registry: &PickedRegistry,
    raw: &Path,
) -> Result<(PickedFile, Vec<u8>), PickError> {
    let canonical = raw.canonicalize().map_err(|error| match error.kind() {
        std::io::ErrorKind::NotFound => PickError::DoesNotExist,
        _ => PickError::Io(error),
    })?;
    let kind = registry
        .kind_of(&canonical)
        .ok_or(PickError::NotRegistered)?;
    let picked = validate_picked_file(raw, kind)?;
    if picked.path != canonical {
        // The path re-resolved differently from the registered canonical key.
        return Err(PickError::IsSymlink);
    }
    let file = std::fs::File::open(&picked.path).map_err(PickError::Io)?;
    let mut buffer = Vec::new();
    file.take(MAX_PICKED_FILE_BYTES + 1)
        .read_to_end(&mut buffer)
        .map_err(PickError::Io)?;
    if buffer.len() as u64 > MAX_PICKED_FILE_BYTES {
        return Err(PickError::GrewDuringRead);
    }
    Ok((picked, buffer))
}
