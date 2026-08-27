from pathlib import Path
from shutil import rmtree

from PIL import Image, ImageOps, UnidentifiedImageError
from PIL.Image import DecompressionBombError

IMAGE_FORMAT_MIME = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
}


def inspect_upload_image(
    path: Path,
    *,
    max_pixels: int,
    max_side: int,
) -> tuple[int, int, str, str]:
    """Return width, height, MIME and suffix from the decoded image header."""

    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            fmt = (image.format or "").upper()
    except DecompressionBombError as error:
        raise ValueError("图片像素数超过上限") from error
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("图片文件损坏或格式不符") from error
    if width <= 0 or height <= 0 or width > max_side or height > max_side:
        raise ValueError("图片宽高超过上限")
    if width * height > max_pixels:
        raise ValueError("图片像素数超过上限")
    mapped = IMAGE_FORMAT_MIME.get(fmt)
    if mapped is None:
        raise ValueError("不支持的图片格式")
    mime, suffix = mapped
    return width, height, mime, suffix


def create_thumbnails(source: Path, root: Path, asset_id: str) -> dict[int, str]:
    """Create bounded WebP previews beside the configured media root."""

    root = root.resolve()
    source = source.resolve()
    if not source.is_relative_to(root):
        raise ValueError("素材路径越界")
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
            preview.save(destination, format="WEBP", quality=82, method=6)
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
