//! V02-54B integration tests: validated local file/directory picking.
//!
//! Policies mirror the API upload boundaries (sources: TXT/Markdown;
//! references: PNG/JPEG/WebP; both ≤ 20 MiB). The registry is the security
//! core: a path that was never returned by a pick can never be read back.

use std::fs;
use std::path::{Path, PathBuf};

use mangaflow_desktop_shell_core::picker::{
    read_registered_file, validate_picked_directory, validate_picked_file, PickError, PickKind,
    PickedRegistry,
};
use mangaflow_desktop_shell_core::protocol::new_token;

fn temp_dir(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "mangaflow-desktop-picker-{tag}-{}-{}",
        std::process::id(),
        new_token()
    ));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

/// File symlinks need Developer Mode / admin on Windows, so the caller runs
/// the link-specific assertions only when creation succeeds (same precedent
/// as `logs.rs` `export_refuses_a_stale_symlink_planted_at_the_pending_sibling`).
fn file_symlink(target: &Path, link: &Path) -> bool {
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(target, link).unwrap();
        true
    }
    #[cfg(windows)]
    {
        std::os::windows::fs::symlink_file(target, link).is_ok()
    }
}

/// Directory links: junctions share the reparse-point metadata shape that
/// `is_symlink` detects and are creatable without any privilege on Windows.
fn dir_link(target: &Path, link: &Path) -> bool {
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(target, link).unwrap();
        true
    }
    #[cfg(windows)]
    {
        // `mklink` is a cmd.exe builtin; /J creates a junction unprivileged.
        std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(link)
            .arg(target)
            .output()
            .map(|output| output.status.success())
            .unwrap_or(false)
    }
}

#[test]
fn source_text_pick_round_trips_through_registry_readback() {
    let dir = temp_dir("roundtrip");
    let source = dir.join("原作.md");
    fs::write(&source, "# 第一章\n".repeat(10)).unwrap();

    let picked = validate_picked_file(&source, PickKind::SourceText).expect("valid pick");
    assert_eq!(picked.name, "原作.md");
    assert_eq!(picked.path, source.canonicalize().unwrap());

    let registry = PickedRegistry::new();
    registry.register(&picked);
    let (re_picked, bytes) = read_registered_file(&registry, &source).expect("registered read");
    assert_eq!(re_picked.path, picked.path);
    assert_eq!(bytes, b"# \xe7\xac\xac\xe4\xb8\x80\xe7\xab\xa0\n".repeat(10).as_slice());

    // A file that was never picked is refused even if it exists and matches
    // the suffix policy — the registry is the capability boundary.
    let stranger = dir.join("stranger.txt");
    fs::write(&stranger, "nope").unwrap();
    let error = read_registered_file(&registry, &stranger).unwrap_err();
    assert!(matches!(error, PickError::NotRegistered), "{error}");
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn suffix_and_size_policies_mirror_upload_boundaries() {
    let dir = temp_dir("policy");

    // Sources: TXT/Markdown only.
    let txt = dir.join("a.txt");
    fs::write(&txt, "正文").unwrap();
    assert!(validate_picked_file(&txt, PickKind::SourceText).is_ok());
    // Reference kind must not be used to read a text file.
    let error = validate_picked_file(&txt, PickKind::ReferenceImage).unwrap_err();
    assert!(matches!(error, PickError::ForbiddenSuffix { .. }), "{error}");

    // References: PNG/JPEG/WebP only.
    let png = dir.join("ref.PNG"); // case-insensitive suffix
    fs::write(&png, b"\x89PNG\r\n").unwrap();
    assert!(validate_picked_file(&png, PickKind::ReferenceImage).is_ok());
    let gif = dir.join("ref.gif");
    fs::write(&gif, b"GIF89a").unwrap();
    let error = validate_picked_file(&gif, PickKind::ReferenceImage).unwrap_err();
    assert!(matches!(error, PickError::ForbiddenSuffix { .. }), "{error}");

    // Size cap = API max_upload_bytes (20 MiB).
    let huge = dir.join("huge.png");
    let file = fs::File::create(&huge).unwrap();
    file.set_len(20 * 1024 * 1024 + 1).unwrap();
    let error = validate_picked_file(&huge, PickKind::ReferenceImage).unwrap_err();
    assert!(
        matches!(
            error,
            PickError::TooLarge { size, cap }
                if size == 20 * 1024 * 1024 + 1 && cap == 20 * 1024 * 1024
        ),
        "{error}"
    );
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn traversal_shapes_are_rejected_before_any_read() {
    let dir = temp_dir("traversal");

    let relative = Path::new("relative.txt");
    assert!(matches!(
        validate_picked_file(relative, PickKind::SourceText),
        Err(PickError::NotAbsolute)
    ));

    let dot = dir.join("sub");
    fs::create_dir_all(&dot).unwrap();
    fs::write(dot.join("a.txt"), "x").unwrap();
    let escaped = dir.join("sub").join("..").join("a.txt");
    let error = validate_picked_file(&escaped, PickKind::SourceText).unwrap_err();
    assert!(matches!(error, PickError::DotComponents), "{error}");

    // A symlinked final component is refused even when the suffix is right.
    let real = dir.join("real.md");
    fs::write(&real, "x").unwrap();
    let link = dir.join("link.md");
    if file_symlink(&real, &link) {
        let error = validate_picked_file(&link, PickKind::SourceText).unwrap_err();
        assert!(matches!(error, PickError::IsSymlink), "{error}");
    } else {
        eprintln!("file symlink creation not permitted; skipping the link assertion");
    }

    // Directory picked as a file.
    let error = validate_picked_file(&dir, PickKind::SourceText).unwrap_err();
    assert!(matches!(error, PickError::NotARegularFile), "{error}");

    assert!(matches!(
        validate_picked_file(&dir.join("missing.txt"), PickKind::SourceText),
        Err(PickError::DoesNotExist)
    ));
    let _ = fs::remove_dir_all(&dir);
}

/// A pick whose PARENT directory chain contains a link must be refused even
/// though the final component is a perfectly regular file: the old check
/// only saw the last entry (`symlink_metadata` on the full path), so a
/// file reached through a symlinked/junctioned directory validated as if
/// it lived where it was picked. On Windows a junction needs no privilege,
/// which made this a privilege-free way to resolve a picked path into an
/// undisclosed location. The registry is unaffected: it keys on canonical
/// paths, and a read-back re-validates the raw path and fails the same way.
#[test]
fn picks_through_linked_parent_directories_are_refused() {
    let dir = temp_dir("parent-link");
    let real = dir.join("real");
    fs::create_dir_all(&real).unwrap();
    fs::write(real.join("ref.png"), b"\x89PNG\r\n").unwrap();

    let linked_dir = dir.join("linked-dir");
    if !dir_link(&real, &linked_dir) {
        eprintln!("directory link creation failed; skipping the link assertions");
        let _ = fs::remove_dir_all(&dir);
        return;
    }

    // A regular, suffix-correct file behind a linked parent is refused.
    let error = validate_picked_file(&linked_dir.join("ref.png"), PickKind::ReferenceImage)
        .unwrap_err();
    assert!(matches!(error, PickError::IsSymlink), "{error}");

    // Deeply nested: the link may sit anywhere along the chain.
    let nested = real.join("nested");
    fs::create_dir_all(&nested).unwrap();
    fs::write(nested.join("ref.png"), b"\x89PNG\r\n").unwrap();
    let error = validate_picked_file(
        &linked_dir.join("nested").join("ref.png"),
        PickKind::ReferenceImage,
    )
    .unwrap_err();
    assert!(matches!(error, PickError::IsSymlink), "{error}");

    // The same policy applies to directory picks through a link.
    let error = validate_picked_directory(&linked_dir.join("nested")).unwrap_err();
    assert!(matches!(error, PickError::IsSymlink), "{error}");

    // The real paths keep validating — the policy refuses the link, not
    // the location.
    validate_picked_file(&real.join("ref.png"), PickKind::ReferenceImage)
        .expect("the real path must stay valid");
    validate_picked_directory(&nested).expect("the real directory must stay valid");

    // A registry read-back through the linked path is refused as well: the
    // re-validation of the raw path fails before any byte is read.
    let registry = PickedRegistry::new();
    registry.register(&validate_picked_file(&real.join("ref.png"), PickKind::ReferenceImage).unwrap());
    let error =
        read_registered_file(&registry, &linked_dir.join("ref.png")).unwrap_err();
    assert!(matches!(error, PickError::IsSymlink), "{error}");

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn directory_pick_validates_type_and_symlinks() {
    let dir = temp_dir("dirs");
    let material = dir.join("素材");
    fs::create_dir_all(&material).unwrap();
    let picked = validate_picked_directory(&material).expect("valid directory pick");
    assert_eq!(picked.name, "素材");

    let file = dir.join("not-a-dir.txt");
    fs::write(&file, "x").unwrap();
    assert!(matches!(
        validate_picked_directory(&file),
        Err(PickError::NotADirectory)
    ));

    let link = dir.join("dir-link");
    if dir_link(&material, &link) {
        assert!(matches!(
            validate_picked_directory(&link),
            Err(PickError::IsSymlink)
        ));
        // Removes the link itself; the target directory survives.
        #[cfg(unix)]
        fs::remove_file(&link).unwrap();
        #[cfg(windows)]
        fs::remove_dir(&link).unwrap();
        assert!(material.is_dir());
    } else {
        eprintln!("directory link creation failed; skipping the link assertion");
    }
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn readback_refails_when_picked_file_is_swapped_or_grows() {
    let dir = temp_dir("swap");
    let source = dir.join("chapter.txt");
    fs::write(&source, "正文").unwrap();

    let registry = PickedRegistry::new();
    registry.register(&validate_picked_file(&source, PickKind::SourceText).unwrap());

    // Replace the picked file with a symlink to a forbidden type: the read
    // re-resolves the canonical path and no longer finds it in the registry
    // (a pick is a capability for that exact file, not for its path string).
    let other = dir.join("other.png");
    fs::write(&other, b"\x89PNG\r\n").unwrap();
    fs::remove_file(&source).unwrap();
    if file_symlink(&other, &source) {
        let error = read_registered_file(&registry, &source).unwrap_err();
        assert!(matches!(error, PickError::NotRegistered), "{error}");
        fs::remove_file(&source).unwrap();
    } else {
        eprintln!("file symlink creation not permitted; skipping the swap assertion");
        fs::write(&source, "正文").unwrap();
    }

    // A registered file that grows past the cap between pick and read is
    // refused by the re-validation, not silently truncated into the page.
    // (write rather than append: this handle only grows the file — on
    // Windows an append-only handle lacks the FILE_WRITE_DATA that
    // SetEndOfFile needs.)
    let grower = dir.join("grower.txt");
    fs::write(&grower, "x").unwrap();
    registry.register(&validate_picked_file(&grower, PickKind::SourceText).unwrap());
    let file = fs::OpenOptions::new().write(true).open(&grower).unwrap();
    file.set_len(20 * 1024 * 1024 + 1).unwrap();
    let error = read_registered_file(&registry, &grower).unwrap_err();
    assert!(matches!(error, PickError::TooLarge { .. }), "{error}");
    let _ = fs::remove_dir_all(&dir);
}

/// Table-driven shape boundaries: each malformed shape fails cleanly with
/// its dedicated error instead of panicking or slipping through. Rows are
/// (label, path); the deterministic rows assert their exact error variant,
/// the filesystem-variant rows (ENAMETOOLONG/EACCES) assert a clean
/// rejection only, because the exact io::ErrorKind varies by platform and
/// filesystem.
#[test]
fn picker_shape_boundaries_fail_cleanly() {
    let dir = temp_dir("shapes");
    fs::create_dir_all(&dir).unwrap();
    fs::write(dir.join("real.txt"), "正文").unwrap();

    let overlong_component = dir.join(format!("{}.txt", "长".repeat(300)));
    let mut deep = dir.clone();
    for _ in 0..4096 {
        deep = deep.join("nested");
    }
    deep.push("leaf.txt");

    let cases: Vec<(&str, PathBuf)> = vec![
        ("empty", PathBuf::new()),
        ("relative", PathBuf::from("relative/正文.txt")),
        ("nonexistent", dir.join("missing.txt")),
        ("overlong component", overlong_component),
        ("overlong total path", deep),
    ];
    for (label, path) in &cases {
        for kind in [PickKind::SourceText, PickKind::ReferenceImage] {
            assert!(
                validate_picked_file(path, kind).is_err(),
                "{label}/{kind:?}: file pick must be rejected"
            );
        }
        assert!(
            validate_picked_directory(path).is_err(),
            "{label}: directory pick must be rejected"
        );
    }

    // Deterministic rows, exact variants (unix: /proc-free, pure stdio).
    #[cfg(unix)]
    {
        assert!(matches!(
            validate_picked_file(Path::new(""), PickKind::SourceText),
            Err(PickError::EmptyPath)
        ));
        assert!(matches!(
            validate_picked_file(Path::new("relative/正文.txt"), PickKind::SourceText),
            Err(PickError::NotAbsolute)
        ));
        assert!(matches!(
            validate_picked_file(&dir.join("missing.txt"), PickKind::SourceText),
            Err(PickError::DoesNotExist)
        ));
    }

    // Permission-denied ancestor: validation through it fails with EACCES
    // instead of succeeding (skipped under root, which reads through the
    // mode bits).
    #[cfg(unix)]
    if unsafe { libc::geteuid() } != 0 {
        use std::os::unix::fs::PermissionsExt;
        let locked_parent = dir.join("locked");
        fs::create_dir_all(locked_parent.join("inner")).unwrap();
        fs::write(locked_parent.join("inner").join("leaf.txt"), "x").unwrap();
        let mut perms = fs::metadata(&locked_parent).unwrap().permissions();
        perms.set_mode(0o000);
        fs::set_permissions(&locked_parent, perms).unwrap();
        assert!(
            validate_picked_file(&locked_parent.join("inner").join("leaf.txt"), PickKind::SourceText)
                .is_err(),
            "a pick through a locked ancestor must be rejected"
        );
        let mut restore = fs::metadata(&locked_parent).unwrap().permissions();
        restore.set_mode(0o755);
        fs::set_permissions(&locked_parent, restore).unwrap();
    }
    let _ = fs::remove_dir_all(&dir);
}

/// The kind→suffix mapping pinned exactly, including the allowed set each
/// ForbiddenSuffix carries (the dialog shows it): each kind accepts only
/// its own extensions and rejects the other kind's.
#[test]
fn suffix_policy_maps_per_kind_with_exact_allowed_sets() {
    let dir = temp_dir("suffixmap");
    for name in ["story.md", "story.txt", "story.markdown", "pic.png", "pic.jpg", "pic.jpeg", "pic.webp"] {
        fs::write(dir.join(name), b"x").unwrap();
    }
    let cases: Vec<(&str, PickKind, bool, &[&str])> = vec![
        ("story.md", PickKind::SourceText, true, &[".txt", ".md", ".markdown"]),
        ("story.txt", PickKind::SourceText, true, &[".txt", ".md", ".markdown"]),
        ("story.markdown", PickKind::SourceText, true, &[".txt", ".md", ".markdown"]),
        ("pic.png", PickKind::SourceText, false, &[".txt", ".md", ".markdown"]),
        ("story.md", PickKind::ReferenceImage, false, &[".png", ".jpg", ".jpeg", ".webp"]),
        ("pic.png", PickKind::ReferenceImage, true, &[".png", ".jpg", ".jpeg", ".webp"]),
        ("pic.jpg", PickKind::ReferenceImage, true, &[".png", ".jpg", ".jpeg", ".webp"]),
        ("pic.jpeg", PickKind::ReferenceImage, true, &[".png", ".jpg", ".jpeg", ".webp"]),
        ("pic.webp", PickKind::ReferenceImage, true, &[".png", ".jpg", ".jpeg", ".webp"]),
    ];
    for (name, kind, accepted, allowed) in cases {
        match validate_picked_file(&dir.join(name), kind) {
            Ok(picked) => assert!(accepted, "{name}/{kind:?} must be accepted"),
            Err(PickError::ForbiddenSuffix { allowed: carried }) => {
                assert!(!accepted, "{name}/{kind:?} must be rejected");
                assert_eq!(carried, allowed, "{name}");
            }
            Err(other) => panic!("{name}/{kind:?}: unexpected {other:?}"),
        }
    }
}
