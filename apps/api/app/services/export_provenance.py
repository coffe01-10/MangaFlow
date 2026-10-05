"""P0-3 创作留痕证明包：导出时的脱敏制作元数据聚合与平台 AI 披露模板。

规格：docs/market-research/功能计划建议.md P0-3。聚合全部来自既有数据——
采用记录（``MangaPage.selected_candidate_id`` + ``selected_candidate_ack_version``
由 ``apps/api/app/api/routes/workflow/generation.py`` 的 ``select_candidate``
在 ``manual_text_confirmed`` 之后写入，是该确认的唯一权威写入点）、五类检查
（``page_completion.latest_inspections_by_category`` 同口径）、原作来源区间
（``PageSourceSegment`` → ``SourceSegment``）、``prompt_snapshot`` 校验和与
``GenerationRecord`` 模型身份。

脱敏红线：绝不输出 API key、连接 base_url、本地绝对路径、prompt 原文；
素材一律用 sha256 引用，内部 UUID 保留作审计锚点。
"""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AIModel,
    Asset,
    Chapter,
    GenerationRecord,
    MangaPage,
    PageCandidate,
    PageSourceSegment,
    Project,
    SourceSegment,
    utcnow,
)
from app.services.page_completion import (
    GATED_QUALITY_CATEGORIES,
    PASSING_QUALITY_OUTCOMES,
    REQUIRED_QUALITY_CATEGORIES,
    latest_inspections_by_category,
)

PROVENANCE_SCHEMA_VERSION = "1.0"
PROVENANCE_FILENAME = "provenance.json"

# 有公开 AI 披露条款的平台（头注注明条款依据）；
# 其余已登记平台与请求未识别值一律走通用模板并标注「无平台背书」。
DISCLOSURE_PLATFORMS = ("KDP", "STEAM", "WEBTOON", "TAPAS", "GENERIC")
_CLAUSE_PLATFORMS = {"KDP", "STEAM"}

_DISCLOSURE_HEADER = (
    "【模板说明】本声明由 MangaFlow 按平台公开口径生成的模板文本，供投稿披露参考，"
    "不构成法律意见或平台背书；提交前请对照平台最新条款自行核对。"
)

_DISCLOSURE_CLAUSES = {
    "KDP": (
        "条款依据：Amazon KDP 帮助页要求申报 AI 生成内容（文本/插图/封面/翻译）；"
        "AI 生成后做大量人工修改仍按 AI 生成申报；AI 辅助（自行创作后由 AI 修改润色）"
        "无需申报。来源：kdp.amazon.com 帮助页；商业模式调研:48。"
    ),
    "STEAM": (
        "条款依据：Steam 自 2024 年 1 月起要求开发者在商店页披露 AI 生成内容及防止"
        "违法内容的措施，并要求开发者对 AI 内容拥有权利。"
        "来源：Wikipedia: Steam_(service)；商业模式调研:49。"
    ),
}

_DISCLOSURE_BODY = (
    "AI 使用披露声明\n"
    "作品：{project_name} / {chapter_title}\n"
    "生成时间：{generated_at}\n"
    "本作品在制作过程中使用了 AI 图像生成；逐页均经人工校对文字、人工采用确认"
    "与视觉质量检查。采用模型、检查结论与原作来源区间详见随包 provenance.json："
    "共 {page_count} 页，每页记录采用候选的模型别名、目录模型标识、prompt 模板"
    "版本与校验和（不含提示词原文）、各检查类别最新结论与检查时间、人工校对"
    "确认凭据与原作片段区间。"
)


def build_disclosure_text(
    platform: str,
    *,
    project_name: str,
    chapter_title: str,
    page_count: int,
    generated_at: str,
) -> str:
    """按平台口径生成披露文本；无专条平台走通用模板并显式标注「无平台背书」。"""

    name = platform.upper()
    if name in _CLAUSE_PLATFORMS:
        clause = _DISCLOSURE_CLAUSES[name]
        endorsement = ""
    else:
        clause = (
            "平台条款：未检索到该平台对 AI 生成内容的公开专条（Webtoon/Tapas 等"
            "见商业模式调研:93 自注）——以下为通用披露模板，无平台背书。"
        )
        endorsement = "\n※ 无平台背书：本模板不代表该平台认可或要求此格式。\n"
    body = _DISCLOSURE_BODY.format(
        project_name=project_name,
        chapter_title=chapter_title,
        generated_at=generated_at,
        page_count=page_count,
    )
    return (
        f"{_DISCLOSURE_HEADER}\n【平台】{name}\n【{clause}】\n"
        f"{endorsement}\n{body}"
    )


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _page_source_segments(db: Session, page_id: str) -> list[dict[str, Any]]:
    rows = list(
        db.execute(
            select(PageSourceSegment, SourceSegment)
            .join(
                SourceSegment,
                SourceSegment.id == PageSourceSegment.source_segment_id,
            )
            .where(PageSourceSegment.page_id == page_id)
            .order_by(SourceSegment.ordinal)
        ).all()
    )
    return [
        {
            "source_segment_id": segment.id,
            "sha256": segment.sha256,
            "start_offset": segment.start_offset,
            "end_offset": segment.end_offset,
        }
        for _, segment in rows
    ]


def _snapshot_provenance(snapshot: dict | None) -> dict[str, Any] | None:
    """prompt_snapshot 只留版本/校验和/操作类型——不带 prompt 原文与素材明细。"""

    if not isinstance(snapshot, dict) or not snapshot:
        return None
    return {
        "prompt_version": snapshot.get("template"),
        "checksum": snapshot.get("checksum"),
        "operation": snapshot.get("operation"),
    }


def _page_provenance(db: Session, page: MangaPage) -> dict[str, Any]:
    candidate = (
        db.get(PageCandidate, page.selected_candidate_id)
        if page.selected_candidate_id
        else None
    )
    candidate_live = (
        candidate is not None
        and candidate.deleted_at is None
        and candidate.is_selected
    )
    ack_current = (
        page.selected_candidate_ack_version is not None
        and page.selected_candidate_ack_version == page.storyboard_version
    )
    # 「人工校对确认」的权威来源是 select_candidate 路由：它在
    # manual_text_confirmed=True 之后才写入 selected_candidate_id 与
    # ack_version（apps/api/app/api/routes/workflow/generation.py:396-467）。
    # 导出侧只做回读：ack 与当前分镜版本一致 + 候选仍为采用态才算确认成立。
    human_review = {
        "manual_text_confirmed": bool(candidate_live and ack_current),
        "selected_candidate_ack_version": page.selected_candidate_ack_version,
        "storyboard_version": page.storyboard_version,
        "ack_current": ack_current,
        "candidate_status": candidate.status if candidate else None,
        "page_continuity_status": page.continuity_status,
    }

    inspections: dict[str, dict[str, Any]] = {}
    if candidate is not None:
        latest = latest_inspections_by_category(db, candidate.id, page.storyboard_version)
        for category in GATED_QUALITY_CATEGORIES:
            row = latest.get(category)
            if row is None:
                # 完整性缺口如实标注「未完成」，缺类绝不缺省通过
                # （README.zh-CN.md:136 缺口在导出层的呈现）。
                entry: dict[str, Any] = {"outcome": "NOT_RUN", "complete": False}
                if category not in REQUIRED_QUALITY_CATEGORIES:
                    entry["required"] = False
                    entry["note"] = "历史页面不追溯（PRESENCE 豁免）"
            else:
                entry = {
                    "outcome": row.outcome,
                    "complete": True,
                    "inspected_at": _iso(row.created_at),
                    "storyboard_version": row.storyboard_version,
                    "severity": row.severity,
                    "passed": row.outcome in PASSING_QUALITY_OUTCOMES,
                }
                if category not in REQUIRED_QUALITY_CATEGORIES:
                    entry["required"] = False
            inspections[category] = entry
    else:
        for category in GATED_QUALITY_CATEGORIES:
            entry = {"outcome": "NOT_RUN", "complete": False}
            if category not in REQUIRED_QUALITY_CATEGORIES:
                entry["required"] = False
                entry["note"] = "历史页面不追溯（PRESENCE 豁免）"
            inspections[category] = entry

    record = (
        db.get(GenerationRecord, candidate.generation_record_id)
        if candidate and candidate.generation_record_id
        else None
    )
    catalog_model = (
        db.get(AIModel, candidate.catalog_model_id)
        if candidate and candidate.catalog_model_id
        else None
    )
    model_identity = None
    if candidate is not None:
        model_identity = {
            "model_alias": candidate.model_alias,
            "catalog_model_id": candidate.catalog_model_id,
            "catalog_model": (
                {
                    "provider_model_id": catalog_model.provider_model_id,
                    "display_name": catalog_model.display_name,
                    "lifecycle": getattr(catalog_model, "lifecycle", None),
                }
                if catalog_model is not None
                else None
            ),
            "generation_record": (
                {
                    "provider": record.provider,
                    "model_id": record.model_id,
                    "prompt_version": record.prompt_version,
                    "prompt_checksum": record.prompt_checksum,
                }
                if record is not None
                else None
            ),
        }

    asset = (
        db.get(Asset, candidate.asset_id)
        if candidate and candidate.asset_id
        else None
    )
    return {
        "page_id": page.id,
        "page_number": page.page_number,
        "adoption": {
            "candidate_id": candidate.id if candidate else None,
            "is_selected": bool(candidate.is_selected) if candidate else False,
            "resolution": (
                candidate.resolution.value
                if candidate is not None and hasattr(candidate.resolution, "value")
                else getattr(candidate, "resolution", None)
            ),
            "adopted_at": _iso(candidate.updated_at) if candidate else None,
            # 素材只给指纹与尺寸——不给 storage_key/原始文件名/本地路径。
            "asset": (
                {
                    "sha256": asset.sha256,
                    "mime_type": asset.mime_type,
                    "byte_size": asset.byte_size,
                    "width": asset.width,
                    "height": asset.height,
                }
                if asset is not None
                else None
            ),
        },
        "human_review": human_review,
        "inspections": inspections,
        "source_segments": _page_source_segments(db, page.id),
        "source_coverage": page.source_coverage or {},
        "prompt": _snapshot_provenance(
            candidate.prompt_snapshot if candidate is not None else None
        ),
        "model": model_identity,
    }


def build_export_provenance(
    db: Session,
    *,
    project: Project,
    chapter: Chapter,
    pages: list[MangaPage],
    disclosure_platform: str | None,
) -> dict[str, Any]:
    generated_at = utcnow()
    document: dict[str, Any] = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "generator": "mangaflow-export-provenance",
        "generated_at": generated_at.isoformat(),
        "project": {"id": project.id, "name": project.name},
        "chapter": {"id": chapter.id, "title": chapter.title},
        "sanitization": {
            "redacted": [
                "api_keys",
                "provider_base_urls",
                "local_paths",
                "storage_keys",
                "prompt_text",
            ],
            "note": "内部 UUID 保留作审计锚点；素材一律以 sha256 引用。",
        },
        "pages": [_page_provenance(db, page) for page in pages],
    }
    if disclosure_platform:
        platform = disclosure_platform.upper()
        text = build_disclosure_text(
            platform,
            project_name=project.name,
            chapter_title=chapter.title,
            page_count=len(pages),
            generated_at=generated_at.isoformat(),
        )
        document["disclosure"] = [
            {
                "platform": platform,
                # 平台专条仅按官方公开口径概述；无专条平台显式标注无背书。
                "platform_endorsed": platform in _CLAUSE_PLATFORMS,
                "not_legal_advice": True,
                "filename": f"disclosure-{platform.lower()}.txt",
                "text": text,
            }
        ]
    return document


def provenance_json_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")


def write_provenance_sidecar(
    destination: Path, document: dict[str, Any]
) -> Path:
    """PDF 无随包通道，写 ``{name}-provenance.json`` 伴随文件。

    伴随文件是导出会话的产物（UUID 唯一名），不在 ExportBundle 行登记、
    不参与下载路由；删除导出时由调用方按同目录前缀清理（见
    ``remove_provenance_sidecar``）。
    """

    sidecar = destination.with_name(f"{destination.stem}.provenance.json")
    temp = destination.with_name(f".{sidecar.name}.{uuid4().hex}.tmp")
    try:
        temp.write_bytes(provenance_json_bytes(document))
        temp.replace(sidecar)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return sidecar


def remove_provenance_sidecar(storage_root: Path, storage_key: str) -> None:
    """导出记录清理时同步删除 PDF 伴随留痕文件（若存在）。"""

    bundle_path = (storage_root / storage_key).resolve()
    sidecar = bundle_path.with_name(f"{bundle_path.stem}.provenance.json")
    exports_root = (storage_root / "exports").resolve()
    if sidecar.is_relative_to(exports_root):
        sidecar.unlink(missing_ok=True)


def disclosure_files(document: dict[str, Any]) -> list[tuple[str, bytes]]:
    """随包披露文本成员：provenance.disclosure[] → disclosure-<platform>.txt。"""

    files: list[tuple[str, bytes]] = []
    for entry in document.get("disclosure") or []:
        files.append(
            (entry["filename"], entry["text"].encode("utf-8"))
        )
    return files


def provenance_token_fragment(
    include_provenance: bool, disclosure_platform: str | None
) -> str:
    """幂等键分量（exports.py token_material 追加）。

    include_provenance / disclosure_platform 改变产物内容，必须进入
    token，否则 reuse_existing 前缀命中会返回「请求含留痕却拿到无留痕」
    的陈旧包。规范化口径与 WebtoonParams.canonical() 相同：参数全部
    序列化成确定字符串。
    """

    return f"prov={int(bool(include_provenance))}|disc={disclosure_platform or '-'}"
