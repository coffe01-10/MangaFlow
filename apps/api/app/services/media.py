from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from PIL.Image import DecompressionBombError

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
