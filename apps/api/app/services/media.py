import logging
import os
from collections.abc import Callable
from datetime import timedelta
from itertools import batched
from pathlib import Path
from shutil import rmtree
from time import time
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from PIL.Image import DecompressionBombError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Asset, ExportBundle

LOGGER = logging.getLogger("mangaflow.media")

IMAGE_FORMAT_MIME = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
}

# Floor for the shorter image side. Real page/asset images are hundreds of
# pixels, while a broken endpoint can emit a decodeable stub (e.g. a 1x1
# placeholder); anything below this is rejected as degenerate. Kept at 8 px so
# legitimately small reference uploads still pass (existing product behavior).
_MIN_IMAGE_SIDE = 8

# Boot-sweep defaults for unreferenced generated media. Passed in explicitly by
# ``main`` because ``app.config`` stays the owner of tunable settings; the floor
# keeps a caller from configuring a window that could race a live generation
# whose owning row has not committed yet.
DEFAULT_ORPHAN_GRACE = timedelta(days=7)
MIN_ORPHAN_GRACE_SECONDS = 3600.0
_REFERENCE_QUERY_BATCH = 500


def inspect_upload_image(
    path: Path,
    *,
    max_pixels: int,
    max_side: int,
) -> tuple[int, int, str, str]:
    """Return width, height, MIME and suffix from a fully decoded image.

    ``Image.verify()`` only validates headers, so the file is opened a second
    time and fully decoded with ``load()``: a truncated file (valid JPEG/PNG
    header with cut entropy data) must fail here instead of being adopted as a
    SUCCEEDED output. The shorter side must also be at least ``8`` px
    (``_MIN_IMAGE_SIDE``) — smaller images are treated as degenerate output.
    """

    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            # verify() deliberately skips entropy data; load() forces the
            # full decode so truncated bodies raise instead of passing.
            image.load()
            width, height = image.size
            fmt = (image.format or "").upper()
    except DecompressionBombError as error:
        raise ValueError("图片像素数超过上限") from error
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        # Pillow raises SyntaxError for corrupt chunk CRCs in otherwise
        # well-formed PNGs; without this it escapes upload handling as a 500.
        raise ValueError("图片文件损坏或格式不符") from error
    if width <= 0 or height <= 0 or width > max_side or height > max_side:
        raise ValueError("图片宽高超过上限")
    if min(width, height) < _MIN_IMAGE_SIDE:
        raise ValueError("图片尺寸过小")
    if width * height > max_pixels:
        raise ValueError("图片像素数超过上限")
    mapped = IMAGE_FORMAT_MIME.get(fmt)
    if mapped is None:
        raise ValueError("不支持的图片格式")
    mime, suffix = mapped
    return width, height, mime, suffix


def create_thumbnails(
    source: Path,
    root: Path,
    asset_id: str,
    *,
    max_pixels: int | None = None,
    max_side: int | None = None,
) -> dict[int, str]:
    """Create bounded WebP previews beside the configured media root.

    Generated provider payloads pass through here too, so callers should pass
    the configured pixel/side caps: a hostile or broken endpoint returning an
    oversized image must fail deterministically instead of exhausting CPU and
    memory during thumbnailing.
    """

    root = root.resolve()
    source = source.resolve()
    if not source.is_relative_to(root):
        raise ValueError("素材路径越界")
    if max_pixels is not None or max_side is not None:
        with Image.open(source) as probe:
            width, height = probe.size
        if max_pixels is not None and width * height > max_pixels:
            raise ValueError("生成图片超出像素上限")
        if max_side is not None and max(width, height) > max_side:
            raise ValueError("生成图片超出边长上限")
    output_dir = root / "thumbnails" / asset_id
    output_dir.mkdir(parents=True, exist_ok=True)
    keys: dict[int, str] = {}
    with Image.open(source) as opened:
        normalized = ImageOps.exif_transpose(opened)
        for size in (320, 640):
            preview = normalized.copy()
            preview.thumbnail((size, size), Image.Resampling.LANCZOS)
            if preview.mode not in {"RGB", "RGBA"}:
                preview = preview.convert("RGBA" if "A" in preview.getbands() else "RGB")
            destination = output_dir / f"{size}.webp"
            # In-place writes raced the on-demand regeneration path: a
            # concurrent reader could stream a half-written webp. Write to a
            # unique temp file and replace atomically instead.
            temp = output_dir / f".{size}.{uuid4().hex}.tmp"
            try:
                preview.save(temp, format="WEBP", quality=82, method=6)
                temp.replace(destination)
            except BaseException:
                temp.unlink(missing_ok=True)
                raise
            finally:
                preview.close()
            keys[size] = destination.relative_to(root).as_posix()
    return keys


def remove_thumbnails(root: Path, asset_id: str) -> None:
    """Remove previews belonging to one known asset without crossing the media root."""

    root = root.resolve()
    thumbnails_root = (root / "thumbnails").resolve()
    output_dir = (thumbnails_root / asset_id).resolve()
    if output_dir.is_relative_to(thumbnails_root) and output_dir.is_dir():
        rmtree(output_dir)


def sanitize_stored_filename(
    value: str, *, max_length: int = 255, default: str = "upload"
) -> str:
    """Reduce a client-supplied filename to a safe stored display name.

    ``Path(...).name`` strips ``/`` but keeps backslashes, control
    characters, trailing dots/spaces (which Windows ignores) and over-long
    values that overflow the column on PostgreSQL. The stored name flows
    into zip members, Content-Disposition and the library UI, so it must be
    safe in all three.
    """

    flattened = value.replace("\\", "/").split("/")[-1]
    # A colon would turn the stored name into an NTFS alternate data stream
    # (and Windows pathlib treats drive-suffixed names oddly), so it is
    # replaced like any other separator.
    cleaned = "".join(
        character for character in flattened if character.isprintable() and character != ":"
    ).strip()
    # Strip AFTER truncation: a 255-char cut can otherwise re-create the
    # trailing dot/space that Windows ignores.
    return cleaned[:max_length].rstrip(". ") or default


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def sweep_orphan_generated_files(
    settings: Settings,
    session_factory: Callable[[], Session] | None = None,
    *,
    older_than: timedelta = DEFAULT_ORPHAN_GRACE,
) -> dict[str, int]:
    """Delete generated and uploaded files no row references.

    Orphan origin: ``_save_generated_asset`` / ``_save_asset_candidate``
    write bytes under ``storage/generated`` before any DB row exists, and the
    post-call completion CAS / lease-lost rollback in ``worker_tasks`` can
    discard the owning rows while leaving the file plus its thumbnails on
    disk. The same window exists for user uploads (``uploads/{project_id}``
    is written before the row insert; a crash, or the loser of a concurrent
    resurrect — see the upload route's version-CAS — leaves its bytes
    behind), and for export bundles (``exports.py`` writes the archive under
    ``storage/exports`` before its ``ExportBundle`` row commits). This sweep
    walks ``storage/generated``, ``storage/thumbnails``, ``storage/exports``
    and the whole ``upload_root`` and unlinks files that are (a) older than
    ``older_than`` (floored at one hour) and (b) referenced by no ``Asset``
    row through ``storage_key``, ``thumbnail_320_key`` or
    ``thumbnail_640_key`` — soft-deleted rows count as references too,
    because asset deletes unlink no files by design — nor by any
    ``ExportBundle.storage_key``. The two checks move together: scanning
    ``exports`` without the bundle reference column would delete in-use
    export products (#634).

    Conservative: symlinks/junctions are neither followed nor unlinked,
    walk errors are logged and skipped without aborting the sweep, reference
    lookups run in bounded batches, and directories left empty are pruned
    best-effort. Returns counters for observability/logging by the caller.
    """

    window = max(older_than.total_seconds(), MIN_ORPHAN_GRACE_SECONDS)
    cutoff = time() - window
    if session_factory is None:
        from app.database import SessionLocal

        session_factory = SessionLocal
    root = settings.storage_root.resolve()
    upload_root = settings.upload_root.resolve()

    candidates: list[tuple[Path, str]] = []

    def _log_walk_error(error: OSError) -> None:
        LOGGER.warning("orphan sweep skipped unreadable path %s", error.filename)

    # Upload files live directly under per-project directories (plus their
    # ``thumbnails`` subtree), while generated media sits under the named
    # storage subdirectories; scanning upload_root wholesale covers both.
    scan_roots: list[tuple[Path, tuple[str, ...]]] = [
        (root, ("generated", "thumbnails", "exports")),
    ]
    if upload_root != root:
        scan_roots.append((upload_root, ("",)))
    for base, relative_roots in scan_roots:
        for relative_root in relative_roots:
            top = base / relative_root if relative_root else base
            if not top.is_dir():
                continue
            for current, dirnames, filenames in os.walk(
                top, followlinks=False, onerror=_log_walk_error
            ):
                # Prune link-like children regardless of how the platform
                # classifies junctions during iteration: never descend into them.
                for name in list(dirnames):
                    if _is_link(Path(current) / name):
                        dirnames.remove(name)
                for name in filenames:
                    path = Path(current) / name
                    if _is_link(path):
                        continue
                    try:
                        if path.stat().st_mtime >= cutoff:
                            continue
                    except OSError:
                        LOGGER.warning("orphan sweep skipped unstattable file %s", path)
                        continue
                    candidates.append((path, path.relative_to(base).as_posix()))

    referenced: set[str] = set()
    if candidates:
        with session_factory() as db:
            for chunk in batched(
                [key for _path, key in candidates], _REFERENCE_QUERY_BATCH
            ):
                rows = db.execute(
                    select(
                        Asset.storage_key,
                        Asset.thumbnail_320_key,
                        Asset.thumbnail_640_key,
                    ).where(
                        or_(
                            Asset.storage_key.in_(chunk),
                            Asset.thumbnail_320_key.in_(chunk),
                            Asset.thumbnail_640_key.in_(chunk),
                        )
                    )
                ).all()
                for storage_key, thumb_320, thumb_640 in rows:
                    referenced.update(
                        key for key in (storage_key, thumb_320, thumb_640) if key
                    )
                # The exports subtree is scanned too (#634), so every bundle
                # row's key — live or awaiting its own superseded-prune — must
                # pin its bytes, exactly like a soft-deleted Asset reference.
                referenced.update(
                    key
                    for key in db.scalars(
                        select(ExportBundle.storage_key).where(
                            ExportBundle.storage_key.in_(chunk)
                        )
                    )
                    if key
                )

    counts = {"removed": 0, "failed": 0, "scanned": len(candidates)}
    for path, key in candidates:
        if key in referenced:
            continue
        try:
            # Re-check liveness: the file may have vanished since the walk.
            if path.is_file():
                path.unlink()
            counts["removed"] += 1
        except OSError:
            LOGGER.warning("orphan sweep could not unlink %s", path)
            counts["failed"] += 1

    for relative_root in ("generated", "thumbnails", "exports"):
        _prune_empty_directories(root / relative_root)
    # Same best-effort pruning for upload_root: project directories whose
    # files were all swept must not accumulate; the root itself is kept by
    # _prune_empty_directories' top guard.
    if upload_root != root:
        _prune_empty_directories(upload_root)
    if counts["removed"] or counts["failed"]:
        LOGGER.info(
            "orphan media sweep: %s removed, %s failed, %s scanned",
            counts["removed"],
            counts["failed"],
            counts["scanned"],
        )
    return counts


def _prune_empty_directories(top: Path) -> None:
    """Best-effort removal of directories left empty by the sweep.

    Own traversal instead of ``os.walk``: on Windows, ``os.walk`` avoids
    symlinks but not junctions, so a naive bottom-up walk could descend into
    a junction and rmdir a directory inside its target, outside the media
    root. Links are treated as content (never crossed, never pruned).
    """

    if not top.is_dir() or _is_link(top):
        return
    empties: list[Path] = []
    stack = [top]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        all_empty = True
        for entry in entries:
            entry_path = Path(entry.path)
            if _is_link(entry_path):
                all_empty = False
                continue
            if entry.is_dir(follow_symlinks=False):
                stack.append(entry_path)
            else:
                all_empty = False
        if all_empty and current != top:
            empties.append(current)
    # Children were appended after their parents, so reversed order removes
    # leaves first; a failed rmdir (locked, newly non-empty) is skipped.
    for directory in reversed(empties):
        try:
            directory.rmdir()
        except OSError:
            continue
