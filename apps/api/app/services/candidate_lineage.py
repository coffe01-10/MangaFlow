"""Candidate lineage and region-regeneration derived candidates (V02-42B).

Implements the local-redraw red lines from ``docs/v02-director-command-lineage-contract.md``
§7 and the V02-42A audit:

- A derived candidate is always a new batch + new ordinal; the parent
  candidate's ``asset_id`` / ``prompt_snapshot`` / ``status`` are never touched.
- The region mask is stored server-side from the validated command polygons;
  clients and models may never provide storage paths.
- Models without the catalog ``accepts_explicit_mask`` capability bit fail with
  a deterministic ``UNSUPPORTED_CAPABILITY`` instead of degrading to a plain
  image-to-image whole-page edit.
- Candidate + lineage + job are created in the caller's transaction; enqueue
  happens only after that transaction commits.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.states import Resolution
from app.models import (
    Asset,
    AssetStatus,
    CandidateLineage,
    GenerationJob,
    LineageKind,
    MangaPage,
    PageCandidate,
)
from app.services.job_service import create_job
from app.services.model_capabilities import (
    REGION_EDIT_SURFACE_LABELS,
    model_region_edit_surface,
    model_supports_explicit_mask,
)
from app.services.model_router import (
    get_catalog_model,
    model_supports_resolution,
    resolve_model,
)
from app.services.ordinal_allocator import create_generation_batch

REGION_JOB_TYPE = "PAGE_REGION_REGENERATE"
REGION_BATCH_KIND = "REGION_REGENERATED"


def attach_derived_lineage(
    db: Session,
    *,
    child: PageCandidate,
    parent: PageCandidate,
    lineage_kind: LineageKind | str,
    source_command_id: str | None = None,
    mask_asset_id: str | None = None,
) -> CandidateLineage:
    """Write the one lineage row for a derived candidate (repair/upscale/region)."""

    kind = LineageKind(lineage_kind) if not isinstance(lineage_kind, LineageKind) else lineage_kind
    snapshot = dict(child.prompt_snapshot or {})
    snapshot["lineage"] = {
        **(snapshot.get("lineage") or {}),
        "parent_candidate_id": parent.id,
        "lineage_kind": kind.value,
        "source_command_id": source_command_id,
        "mask_asset_id": mask_asset_id,
    }
    child.prompt_snapshot = snapshot
    lineage = CandidateLineage(
        child_candidate_id=child.id,
        parent_candidate_id=parent.id,
        lineage_kind=kind,
        source_command_id=source_command_id,
        mask_asset_id=mask_asset_id,
        model_alias=child.model_alias,
        catalog_model_id=child.catalog_model_id,
        resolution=(
            child.resolution.name
            if hasattr(child.resolution, "name")
            else str(child.resolution)
        ),
    )
    db.add(lineage)
    db.flush()
    return lineage


def _http_422(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def inherited_reference_ids(snapshot: dict) -> list[str]:
    """Reference assets the inherited snapshot will feed to the model (§8.4)."""
    selections = snapshot.get("reference_selections") or {}
    asset_ids = [
        asset_id
        for selection in selections.values()
        for asset_id in (selection.get("character_asset_id"), selection.get("outfit_asset_id"))
        if asset_id
    ]
    scene_snapshot = snapshot.get("scene_asset") or {}
    asset_ids.extend(scene_snapshot.get("reference_asset_ids") or [])
    return asset_ids


def store_region_mask_asset(
    db: Session, *, project_id: str, regions: list[dict], source_command_id: str
) -> Asset:
    """Persist the server-owned region mask for one director command.

    The mask content is derived from the validated command polygons only. The
    JSON document is content-addressed by sha256, so the savepoint-rolled
    preview write and the final accept write converge on the same file and
    asset row (deduplicated through the project/sha256 unique constraint).
    """

    settings = get_settings()
    document = {
        "schema_version": 1,
        "kind": "region_mask",
        "source_command_id": source_command_id,
        "regions": [{"points": list(region["points"])} for region in regions],
    }
    encoded = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    existing = db.scalar(
        select(Asset).where(Asset.project_id == project_id, Asset.sha256 == digest)
    )
    if existing is not None:
        return existing
    storage_key = f"generated/{project_id}/region-masks/{digest}.json"
    destination = settings.storage_root / storage_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encoded)
    try:
        with db.begin_nested():
            asset = Asset(
                project_id=project_id,
                kind="region_mask",
                original_name=f"region-mask-{source_command_id}.json",
                display_name=f"局部选区蒙版 {source_command_id[:8]}",
                storage_key=storage_key,
                mime_type="application/json",
                byte_size=len(encoded),
                sha256=digest,
                source="AI_GENERATED",
                status=AssetStatus.GENERATED,
            )
            db.add(asset)
            db.flush()
    except IntegrityError:
        # Another writer inserted the same content first; its file is the same
        # content-addressed bytes, so only the row lookup matters here.
        existing = db.scalar(
            select(Asset).where(Asset.project_id == project_id, Asset.sha256 == digest)
        )
        if existing is None:
            raise
        return existing
    return asset


def create_region_regeneration(
    db: Session,
    *,
    row,
    envelope,
    page: MangaPage,
    parent: PageCandidate,
) -> tuple[PageCandidate, CandidateLineage, GenerationJob]:
    """Create the derived candidate, its lineage row and its job.

    Runs inside the caller's transaction: during propose preview the nested
    savepoint rolls everything back; on accept the service-level commit makes
    it permanent. Enqueueing is deliberately left to the caller after commit.
    """

    payload = envelope.payload
    mask_regions = payload.get("mask") or payload.get("target_regions")
    if not mask_regions:
        # L3 red line: REGION_REGENERATED without a mask is rejected before any
        # job or paid call can exist; silent whole-page regeneration is banned.
        raise _http_422("局部重抽卡缺少 mask，已在调用前拒绝")
    if not parent.asset_id:
        raise _http_422("父候选图片不存在，已在调用前拒绝")

    model_alias = payload.get("model_alias") or parent.model_alias
    resolution_value = payload.get("resolution") or parent.resolution.value
    try:
        resolution = Resolution(resolution_value)
    except ValueError as error:
        raise _http_422("局部重抽卡 resolution 无效") from error
    resolved_model = get_catalog_model(db, model_alias) if model_alias else None
    if resolved_model is None:
        raise _http_422("未识别的模型")
    if not model_supports_explicit_mask(resolved_model):
        # §7/§8-M2: the refusal names the declared surface so a whole-image
        # reference or instruction-only model is never silently treated as a
        # local editor, and nothing falls back to another model or provider.
        raise _http_422(
            "UNSUPPORTED_CAPABILITY：所选模型不具备显式 mask 局部编辑能力"
            f"（当前目录声明：{REGION_EDIT_SURFACE_LABELS[model_region_edit_surface(resolved_model)]}），"
            "不得按普通 image-to-image 整页重绘，已在调用前拒绝"
        )
    settings = get_settings()
    # Full routing/credential validation like the repair route, so accept
    # fails deterministically before any Job exists when the model cannot be
    # dispatched; the resolved row itself was already checked above.
    resolve_model(
        db,
        settings,
        operation="image_edit",
        explicit_reference=model_alias,
        project_id=envelope.target.project_id,
        task_kind=REGION_JOB_TYPE,
    )
    if not model_supports_resolution(resolved_model, resolution.value):
        raise _http_422("所选模型不支持该输出清晰度")

    mask_asset = store_region_mask_asset(
        db,
        project_id=envelope.target.project_id,
        regions=mask_regions,
        source_command_id=row.command_id,
    )
    batch = create_generation_batch(
        db,
        project_id=envelope.target.project_id,
        chapter_id=page.chapter_id,
        page_id=page.id,
        generation_kind=REGION_BATCH_KIND,
        close_open_page_batches=True,
    )
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias=model_alias,
        catalog_model_id=resolved_model.id,
        resolution=resolution,
        status="QUEUED",
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={
            **(parent.prompt_snapshot or {}),
            "lineage": {
                "operation": "regenerate_region",
                "parent_candidate_id": parent.id,
                "lineage_kind": LineageKind.REGION_REGENERATED.value,
                "source_command_id": row.command_id,
                "mask_asset_id": mask_asset.id,
                "mask_sha256": mask_asset.sha256,
            },
        },
    )
    db.add(candidate)
    db.flush()
    lineage = CandidateLineage(
        child_candidate_id=candidate.id,
        parent_candidate_id=parent.id,
        lineage_kind=LineageKind.REGION_REGENERATED,
        source_command_id=row.command_id,
        mask_asset_id=mask_asset.id,
        model_alias=model_alias,
        catalog_model_id=resolved_model.id,
        # Mirror page_candidates.resolution storage (enum member name).
        resolution=resolution.name,
    )
    db.add(lineage)
    job = create_job(
        db,
        project_id=envelope.target.project_id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type=REGION_JOB_TYPE,
        model_alias=model_alias,
        catalog_model_id=resolved_model.id,
        request_parameters={
            "original_candidate_id": parent.id,
            "lineage_kind": LineageKind.REGION_REGENERATED.value,
            "source_command_id": row.command_id,
            "instruction": payload["instruction"],
            "mask_asset_id": mask_asset.id,
            "target_regions": mask_regions,
            "storyboard_version": page.storyboard_version,
        },
        reference_asset_ids=[
            parent.asset_id,
            *inherited_reference_ids(parent.prompt_snapshot or {}),
            mask_asset.id,
        ],
        idempotency_key=f"region:{row.command_id}",
        auto_commit=False,
    )
    candidate.job_id = job.id
    db.flush()
    return candidate, lineage, job
