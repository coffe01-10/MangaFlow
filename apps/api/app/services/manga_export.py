"""PUB-01C WEBTOON 条漫导出：切片计算与流式 ZIP 渲染。

契约：`docs/pub-01b-webtoon-export-contract.md`。预览侧双语实现在
`apps/web/lib/mobile-preview.ts`（`computeStrip`）——页间优先、超限单页
等距硬切，两侧边界必须一致，改动须同步。
"""

import hashlib
import json
import shutil
import time
import zipfile
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image

SLICE_COUNT_LIMIT = 999
MAX_EXPANDED_BYTES = 2 * 1024**3


class WebtoonExportError(ValueError):
    """确定性导出失败（参数/素材/体量），路由层翻译成 HTTP 错误。

    ``status_code`` 区分契约 §7 的错误类别：参数非法与体量超限为 422，
    素材数据缺陷（尺寸缺失等，与「采用素材不存在」同类）为 409。
    """

    def __init__(self, detail: str, status_code: int = 422):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class WebtoonParams:
    preset: str
    width: int
    format: str
    quality: int | None
    gap_px: int
    max_slice_height: int

    def canonical(self) -> str:
        """幂等键片段：参数规范化序列化，参与导出 token。"""
        quality = self.quality if self.quality is not None else "-"
        return (
            f"{self.preset}|w{self.width}|{self.format}|q{quality}"
            f"|g{self.gap_px}|h{self.max_slice_height}"
        )


WEBTOON_PRESETS: dict[str, WebtoonParams] = {
    "STANDARD": WebtoonParams("STANDARD", 1080, "JPEG", 88, 16, 4096),
    "COMPACT": WebtoonParams("COMPACT", 800, "JPEG", 85, 0, 8192),
    "HQ_PNG": WebtoonParams("HQ_PNG", 1440, "PNG", None, 0, 8192),
}


def resolve_webtoon_params(
    *,
    preset: str | None,
    width: int | None,
    format: str | None,
    quality: int | None,
    gap_px: int | None,
    max_slice_height: int | None,
) -> WebtoonParams:
    name = preset or "STANDARD"
    base = WEBTOON_PRESETS.get(name)
    if base is None:
        raise WebtoonExportError(f"未知条漫预设：{name}")
    params = replace(
        base,
        width=width if width is not None else base.width,
        format=format if format is not None else base.format,
        gap_px=gap_px if gap_px is not None else base.gap_px,
        max_slice_height=(
            max_slice_height if max_slice_height is not None else base.max_slice_height
        ),
    )
    if params.format == "JPEG":
        params = replace(params, quality=quality if quality is not None else (base.quality or 88))
    else:
        params = replace(params, quality=None)
    if not 320 <= params.width <= 4096:
        raise WebtoonExportError("条漫目标宽度须在 320–4096 像素之间")
    if not 0 <= params.gap_px <= 128:
        raise WebtoonExportError("页间距须在 0–128 像素之间")
    if not 512 <= params.max_slice_height <= 16384:
        raise WebtoonExportError("最大分片高度须在 512–16384 像素之间")
    if params.quality is not None and not 50 <= params.quality <= 100:
        raise WebtoonExportError("JPEG 压缩质量须在 50–100 之间")
    return params


def scaled_page_height(src_width: int | None, src_height: int | None, target_width: int) -> int:
    if not src_width or not src_height or src_width <= 0 or src_height <= 0:
        # 契约 §7：尺寸缺失是素材数据缺陷（409），不是参数错误（422）。
        raise WebtoonExportError("采用素材缺少尺寸信息，无法条漫导出", status_code=409)
    return round(src_height * target_width / src_width)


@dataclass(frozen=True)
class SlicePart:
    """片内一个纵向段：page_index 页的 [src_top, src_top + height) 缩放段。"""

    page_index: int
    src_top: int
    height: int


@dataclass(frozen=True)
class Slice:
    index: int
    parts: tuple[SlicePart, ...]
    height: int
    hard_cut: bool


def compute_slices(
    heights: list[int], gap_px: int, max_slice_height: int
) -> list[Slice]:
    """契约 §3 的唯一切片算法（与前端 computeStrip 同语义）。

    页间优先贪心；单页 h > max_slice_height 时等距硬切成独立片。
    返回的每片高度恒 ≤ max_slice_height。
    """
    slices: list[Slice] = []
    parts: list[SlicePart] = []
    cursor = 0
    slice_start = 0
    for index, height in enumerate(heights):
        top = cursor
        bottom = top + height
        if top > slice_start and bottom - slice_start > max_slice_height:
            _flush_slice(slices, parts, gap_px)
            parts = []
            slice_start = top
        if height > max_slice_height:
            if parts:
                _flush_slice(slices, parts, gap_px)
                parts = []
            offset = 0
            while offset < height:
                seg = min(max_slice_height, height - offset)
                slices.append(
                    Slice(
                        len(slices) + 1,
                        (SlicePart(index, offset, seg),),
                        seg,
                        True,
                    )
                )
                offset += seg
            slice_start = bottom
        else:
            parts.append(SlicePart(index, 0, height))
        cursor = bottom + gap_px
    if parts:
        _flush_slice(slices, parts, gap_px)
    return slices


def _flush_slice(slices: list[Slice], parts: list[SlicePart], gap_px: int) -> None:
    slices.append(
        Slice(len(slices) + 1, tuple(parts), _slice_content_height(parts, gap_px), False)
    )


def _slice_content_height(parts: list[SlicePart], gap_px: int) -> int:
    """片内容高度：段高之和 + 片内页间间距。"""
    total = sum(part.height for part in parts)
    if len(parts) > 1:
        total += gap_px * (len(parts) - 1)
    return total


def _open_scaled(source: Path, width: int, expected_height: int) -> Image.Image:
    with Image.open(source) as img:
        img.load()
        scaled = img.resize((width, expected_height), Image.LANCZOS)
    if scaled.mode in ("RGBA", "LA", "PA") or scaled.mode == "P":
        background = Image.new("RGB", scaled.size, (255, 255, 255))
        alpha = scaled.convert("RGBA")
        background.paste(alpha, mask=alpha.getchannel("A"))
        return background
    return scaled.convert("RGB") if scaled.mode != "RGB" else scaled


def build_webtoon_zip(
    destination_tmp: Path,
    *,
    pages: list,
    assets: list,
    params: WebtoonParams,
    asset_path,
    project_name: str,
    chapter_id: str,
    chapter_title: str,
    project_id: str,
    provenance: dict | None = None,
) -> None:
    """把采用页流式渲染成分片 ZIP 写入 ``destination_tmp``（契约 §5/§6）。

    峰值内存：一张源图 + 一张缩放页 + 一个片缓冲（width × max_slice_height）。
    分片先落 `.{serial}.slices` 临时目录再打包，异常时尽力清理。
    """
    heights = [
        scaled_page_height(asset.width, asset.height, params.width) for asset in assets
    ]
    slices = compute_slices(heights, params.gap_px, params.max_slice_height)
    if not slices:
        raise WebtoonExportError("章节没有可导出的页面")
    if len(slices) > SLICE_COUNT_LIMIT:
        raise WebtoonExportError("分片数量超过上限（999）")
    expanded = params.width * sum(s.height for s in slices) * 3
    if expanded > MAX_EXPANDED_BYTES:
        raise WebtoonExportError("条漫总像素超过导出上限")

    slices_dir = destination_tmp.with_name(f"{destination_tmp.name}.slices")
    slices_dir.mkdir(parents=True, exist_ok=True)
    suffix = "jpg" if params.format == "JPEG" else "png"
    manifest_slices: list[dict] = []
    try:
        current_page: int | None = None
        current_image: Image.Image | None = None
        for slice_ in slices:
            canvas = Image.new("RGB", (params.width, slice_.height), (255, 255, 255))
            offset = 0
            manifest_pages: list[dict] = []
            previous_index: int | None = None
            for part in slice_.parts:
                if part.page_index != current_page:
                    if current_image is not None:
                        current_image.close()
                    source = asset_path(assets[part.page_index])
                    current_image = _open_scaled(
                        source, params.width, heights[part.page_index]
                    )
                    current_page = part.page_index
                if previous_index is not None and part.page_index != previous_index:
                    offset += params.gap_px
                region = current_image.crop(
                    (0, part.src_top, params.width, part.src_top + part.height)
                )
                canvas.paste(region, (0, offset))
                page = pages[part.page_index]
                asset = assets[part.page_index]
                src_factor = asset.height / heights[part.page_index]
                manifest_pages.append(
                    {
                        "page_id": page.id,
                        "page_number": page.page_number,
                        "src_rect": [
                            0,
                            round(part.src_top * src_factor),
                            asset.width,
                            round(part.height * src_factor),
                        ],
                        "dst_rect": [0, offset, params.width, part.height],
                    }
                )
                offset += part.height
                previous_index = part.page_index
            name = f"slice-{slice_.index:04d}.{suffix}"
            slice_path = slices_dir / name
            save_kwargs = {"quality": params.quality} if params.format == "JPEG" else {}
            canvas.save(slice_path, format=params.format, **save_kwargs)
            canvas.close()
            digest = hashlib.sha256(slice_path.read_bytes()).hexdigest()
            manifest_slices.append(
                {
                    "file": name,
                    "index": slice_.index,
                    "width": params.width,
                    "height": slice_.height,
                    "sha256": digest,
                    "hard_cut": slice_.hard_cut,
                    "pages": manifest_pages,
                }
            )
        if current_image is not None:
            current_image.close()
            current_image = None
        manifest = {
            # schema 1.1: 携带留痕证明包时 manifest 追加顶层 "provenance" 键
            # （P0-3，契约 docs/pub-01b-webtoon-export-contract.md §4）。
            "schema_version": "1.1" if provenance is not None else "1.0",
            "generator": "mangaflow-webtoon-export",
            "export_type": "WEBTOON",
            "project": {"id": project_id, "name": project_name},
            "chapter": {"id": chapter_id, "title": chapter_title},
            "params": {
                "preset": params.preset,
                "width": params.width,
                "format": params.format,
                "quality": params.quality,
                "gap_px": params.gap_px,
                "max_slice_height": params.max_slice_height,
            },
            "page_count": len(pages),
            "total_height": sum(heights) + params.gap_px * max(0, len(heights) - 1),
            "slices": manifest_slices,
        }
        if provenance is not None:
            manifest["provenance"] = provenance
        with zipfile.ZipFile(destination_tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry in manifest_slices:
                archive.write(slices_dir / entry["file"], arcname=entry["file"])
            archive.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
    finally:
        if current_image is not None:
            current_image.close()
        shutil.rmtree(slices_dir, ignore_errors=True)


def sweep_stale_webtoon_artifacts(output_dir: Path, keep_name: str) -> None:
    """清理同目录孤儿：上一轮崩溃残留的 `.{serial}.slices` 与 `.tmp`。

    只动 mtime 早于 10 分钟前的条目，避免误删并发导出正在写入的在途
    临时文件。
    """
    if not output_dir.is_dir():
        return
    cutoff = time.time() - 600
    for child in output_dir.iterdir():
        if child.name == keep_name:
            continue
        try:
            if child.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        if child.is_dir() and child.name.startswith(".") and child.name.endswith(".slices"):
            shutil.rmtree(child, ignore_errors=True)
        elif child.is_file() and child.name.startswith(".") and child.name.endswith(".tmp"):
            with suppress(OSError):
                child.unlink()
