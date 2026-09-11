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
    SwappedAfterValidation,
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
            PickError::SwappedAfterValidation => {
                write!(f, "文件在校验后被替换，已拒绝读取")
            }
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

/// Reject a symlink/junction ANYWHERE in the directory chain, not just at
/// the final component. `symlink_metadata` on the full path only sees the
/// last entry; an intermediate link (on Windows a junction needs no
/// privilege at all) would silently resolve a picked path into an
/// undisclosed location, breaking the module contract that no symlink
/// survives validation. Each ancestor is checked with `symlink_metadata`,
/// which never follows links; the full path itself is skipped here because
/// every caller checks the final component separately. Relative paths never
/// reach this function — `check_no_traversal` runs first.
fn reject_intermediate_links(raw: &Path) -> Result<(), PickError> {
    for ancestor in raw.ancestors().skip(1) {
        if ancestor.as_os_str().is_empty() {
            continue;
        }
        let meta = ancestor
            .symlink_metadata()
            .map_err(|error| match error.kind() {
                std::io::ErrorKind::NotFound => PickError::DoesNotExist,
                _ => PickError::Io(error),
            })?;
        if meta.is_symlink() {
            return Err(PickError::IsSymlink);
        }
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
/// components, no symlink anywhere in the chain (intermediate directories
/// included — see [`reject_intermediate_links`]), a regular file
/// whose canonical file name still matches what was picked, and the
/// kind-aligned suffix/size policy.
pub fn validate_picked_file(raw: &Path, kind: PickKind) -> Result<PickedFile, PickError> {
    check_no_traversal(raw)?;
    reject_intermediate_links(raw)?;
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
    reject_intermediate_links(raw)?;
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

/// (volume/device, index/inode) identity of a PATH, from a metadata stat.
#[cfg(unix)]
fn path_identity(path: &Path) -> std::io::Result<(u64, u64)> {
    use std::os::unix::fs::MetadataExt;
    let meta = std::fs::metadata(path)?;
    Ok((meta.dev(), meta.ino()))
}

/// Windows path identity: std exposes no file index, so open a handle and ask
/// the filesystem. Only used as the EXPECTED side of the comparison in
/// [`read_registered_file`] — the final read still opens its own handle and
/// re-checks identity, so this probe handle closing first is harmless.
#[cfg(windows)]
fn path_identity(path: &Path) -> std::io::Result<(u64, u64)> {
    handle_identity(&std::fs::File::open(path)?)
}

/// (volume/device, index/inode) identity of an OPEN HANDLE: fstat on Unix,
/// GetFileInformationByHandle on Windows. Comparing this against the pre-open
/// path identity detects a file swapped between validation and open — the
/// open handle then belongs to different on-disk object than the one the
/// policy validated.
#[cfg(unix)]
fn handle_identity(file: &std::fs::File) -> std::io::Result<(u64, u64)> {
    use std::os::unix::io::AsRawFd;
    let mut stat: libc::stat = unsafe { std::mem::zeroed() };
    // SAFETY: stat is a valid out-pointer for the duration of the call and
    // the fd is owned by `file`.
    if unsafe { libc::fstat(file.as_raw_fd(), &mut stat) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok((stat.st_dev as u64, stat.st_ino as u64))
}

#[cfg(windows)]
fn handle_identity(file: &std::fs::File) -> std::io::Result<(u64, u64)> {
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
    };
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    // SAFETY: the handle is owned by `file` and outlives the call; `info` is
    // a valid out-pointer.
    let result = unsafe { GetFileInformationByHandle(HANDLE(file.as_raw_handle()), &mut info) };
    result.map_err(|error| std::io::Error::from_raw_os_error(error.code().0 as i32))?;
    Ok((
        info.dwVolumeSerialNumber as u64,
        ((info.nFileIndexHigh as u64) << 32) | (info.nFileIndexLow as u64),
    ))
}

/// Read a previously picked file back for the page (which then uploads it
/// through the ordinary API upload endpoints). Re-validates the full policy
/// and registry membership on every call — a pick is a capability, not a
/// permanent grant.
pub fn read_registered_file(
    registry: &PickedRegistry,
    raw: &Path,
) -> Result<(PickedFile, Vec<u8>), PickError> {
    read_registered_file_with(registry, raw, || {})
}

/// Test seam for the post-open identity check: `between_identity_and_open`
/// runs exactly where a real swap would have to happen (after the policy
/// validated the path's identity, before the read opens its handle).
pub fn read_registered_file_with(
    registry: &PickedRegistry,
    raw: &Path,
    between_identity_and_open: impl FnOnce(),
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
    // Post-open identity check (#308): the opened handle must belong to the
    // same on-disk object the policy just validated. A swap between the
    // stat and the open makes the handle's identity mismatch and fails
    // closed instead of serving replaced bytes.
    let expected = path_identity(&picked.path).map_err(PickError::Io)?;
    between_identity_and_open();
    let file = std::fs::File::open(&picked.path).map_err(PickError::Io)?;
    if handle_identity(&file).map_err(PickError::Io)? != expected {
        return Err(PickError::SwappedAfterValidation);
    }
    let mut buffer = Vec::new();
    file.take(MAX_PICKED_FILE_BYTES + 1)
        .read_to_end(&mut buffer)
        .map_err(PickError::Io)?;
    if buffer.len() as u64 > MAX_PICKED_FILE_BYTES {
        return Err(PickError::GrewDuringRead);
    }
    Ok((picked, buffer))
}


#[cfg(test)]
mod registry_tests {
    use super::*;

    /// Re-picking the same canonical path with a different kind must
    /// REPLACE the registration — the map is keyed by canonical path, so
    /// the readback's validation policy follows the LATEST pick. A
    /// regression to "first wins" (or an entry-per-pick multimap) would
    /// serve stale-kind validation for a file the user re-picked as
    /// something else. The registry is memory-only: no fs touch here.
    #[test]
    fn re_registering_a_path_replaces_the_kind() {
        let registry = PickedRegistry::new();
        let picked = |kind| PickedFile {
            path: PathBuf::from("/tmp/picked.dat"),
            name: "picked.dat".into(),
            size_bytes: 8,
            kind,
        };
        registry.register(&picked(PickKind::SourceText));
        assert_eq!(
            registry.kind_of(Path::new("/tmp/picked.dat")),
            Some(PickKind::SourceText)
        );
        registry.register(&picked(PickKind::ReferenceImage));
        assert_eq!(
            registry.kind_of(Path::new("/tmp/picked.dat")),
            Some(PickKind::ReferenceImage),
            "the latest pick must win"
        );
        // And an unknown path stays unknown.
        assert_eq!(registry.kind_of(Path::new("/tmp/never-picked.dat")), None);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `PickKind::parse` is the string gate between the WebView invoke and
    /// the pick surface: only the two exact identifiers route anywhere, and
    /// everything else — unknown, wrong case, empty — must fail closed to
    /// None rather than default to a kind.
    #[test]
    fn pick_kind_parses_only_the_exact_invoke_identifiers() {
        assert_eq!(PickKind::parse("source_text"), Some(PickKind::SourceText));
        assert_eq!(
            PickKind::parse("reference_image"),
            Some(PickKind::ReferenceImage)
        );
        for rejected in ["", "Source_Text", "SOURCE_TEXT", "source-text",
                         "source_text ", " reference_image", "image", "text"] {
            assert_eq!(
                PickKind::parse(rejected),
                None,
                "a non-exact identifier must not route to a kind: {rejected:?}"
            );
        }
    }

    /// The dialog filter (what the OS dialog offers) and the validator's
    /// allowed suffixes (what the shell accepts afterwards) are two tables
    /// that must agree: a filter entry without a validator suffix would let
    /// a user pick a file the shell then refuses; a validator suffix
    /// without a filter entry makes it unpickable in practice. Both derive
    /// from the same kind here — pin the correspondence plus the no-dot
    /// form rfd expects.
    #[test]
    fn dialog_filter_extensions_mirror_the_allowed_suffixes() {
        for kind in [PickKind::SourceText, PickKind::ReferenceImage] {
            let (label, extensions) = kind.dialog_filter();
            assert!(!label.is_empty());
            let suffixes = kind.allowed_suffixes();
            assert_eq!(extensions.len(), suffixes.len());
            for (extension, suffix) in extensions.iter().zip(suffixes) {
                assert!(!extension.contains('.'),
                    "rfd expects bare extensions: {extension}");
                assert_eq!(&format!(".{extension}"), suffix,
                    "filter/validator drift for {kind:?}: {extension} vs {suffix}");
            }
        }
        // The exact sets are pinned in the integration suite
        // (suffix_policy_maps_per_kind_with_exact_allowed_sets); here the
        // kinds must simply not swap tables with each other.
        assert_ne!(
            PickKind::SourceText.allowed_suffixes(),
            PickKind::ReferenceImage.allowed_suffixes()
        );
    }

    /// Mirrors `app.config.Settings.max_upload_bytes`; a silent drift here
    /// would make the shell refuse (or over-accept relative to the dialog's
    /// promise) files the API treats differently.
    #[test]
    fn max_pick_bytes_stays_aligned_with_the_upload_cap() {
        assert_eq!(MAX_PICKED_FILE_BYTES, 20 * 1024 * 1024);
    }
}
