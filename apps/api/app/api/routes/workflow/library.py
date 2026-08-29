"""Project library route: generation batch and candidate browsing."""

import base64
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.api.helpers import asset_candidate_read, candidate_read
from app.database import get_db
from app.domain.states import Resolution
from app.models import (
    AssetCandidate,
    Chapter,
    Character,
    GenerationBatch,
    MangaPage,
    Outfit,
    PageCandidate,
    Panel,
    Project,
)
from app.schemas import (
    GenerationBatchRead,
    LibraryBatchRead,
    LibraryRead,
    PageCandidateRead,
)

router = APIRouter()


@router.get("/projects/{project_id}/library", response_model=LibraryRead)
def library(
    project_id: str,
    group_by: str = Query(default="batch", pattern="^batch$"),
    chapter_id: str | None = None,
    model_alias: str | None = None,
    favorite: bool | None = None,
    generation_kind: str | None = None,
    character_id: str | None = None,
    resolution: Resolution | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> LibraryRead:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    batch_query = select(GenerationBatch).where(GenerationBatch.project_id == project_id)
    if chapter_id:
        chapter = db.get(Chapter, chapter_id)
        if (
            not chapter
            or chapter.project_id != project_id
            or chapter.deleted_at is not None
        ):
            raise HTTPException(status_code=404, detail="筛选章节不存在或不属于当前项目")
        batch_query = batch_query.where(GenerationBatch.chapter_id == chapter_id)
    if generation_kind:
        batch_query = batch_query.where(GenerationBatch.generation_kind == generation_kind.upper())
    if date_from:
        batch_query = batch_query.where(GenerationBatch.created_at >= date_from)
    if date_to:
        batch_query = batch_query.where(GenerationBatch.created_at <= date_to)
    if character_id:
        character = db.get(Character, character_id)
        if not character or character.project_id != project_id:
            raise HTTPException(status_code=404, detail="筛选角色不存在或不属于当前项目")
        outfit_ids = set(db.scalars(select(Outfit.id).where(Outfit.character_id == character_id)))
        page_ids = {
            panel.page_id
            for panel in db.scalars(
                select(Panel)
                .join(MangaPage, MangaPage.id == Panel.page_id)
                .join(Chapter, Chapter.id == MangaPage.chapter_id)
                .where(Chapter.project_id == project_id)
            )
            if character_id in panel.characters
        }
        character_filters = [
            and_(
                GenerationBatch.target_type == "CHARACTER",
                GenerationBatch.target_id == character_id,
            )
        ]
        if outfit_ids:
            character_filters.append(
                and_(
                    GenerationBatch.target_type == "OUTFIT",
                    GenerationBatch.target_id.in_(outfit_ids),
                )
            )
        if page_ids:
            character_filters.append(GenerationBatch.page_id.in_(page_ids))
        batch_query = batch_query.where(or_(*character_filters))

    page_filters = [
        PageCandidate.batch_id == GenerationBatch.id,
        PageCandidate.deleted_at.is_(None),
        PageCandidate.status.not_in({"FAILED", "CANCELLED"}),
    ]
    asset_filters = [
        AssetCandidate.batch_id == GenerationBatch.id,
        AssetCandidate.deleted_at.is_(None),
        AssetCandidate.status.not_in({"FAILED", "CANCELLED"}),
    ]
    if model_alias:
        page_filters.append(PageCandidate.model_alias == model_alias)
        asset_filters.append(AssetCandidate.model_alias == model_alias)
    if favorite is not None:
        page_filters.append(PageCandidate.is_favorite == favorite)
        asset_filters.append(AssetCandidate.is_favorite == favorite)
    if resolution:
        page_filters.append(PageCandidate.resolution == resolution)
        asset_filters.append(AssetCandidate.resolution == resolution)
    batch_query = batch_query.where(
        or_(
            exists(select(1).where(*page_filters)),
            exists(select(1).where(*asset_filters)),
        )
    )

    if cursor:
        try:
            payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
            cursor_time = datetime.fromisoformat(payload["created_at"])
            cursor_id = payload["id"]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=422, detail="素材库游标无效") from error
        batch_query = batch_query.where(
            or_(
                GenerationBatch.created_at < cursor_time,
                and_(
                    GenerationBatch.created_at == cursor_time,
                    GenerationBatch.id < cursor_id,
                ),
            )
        )

    batches = list(
        db.scalars(
            batch_query.order_by(
                GenerationBatch.created_at.desc(), GenerationBatch.id.desc()
            ).limit(limit + 1)
        )
    )
    has_more = len(batches) > limit
    batches = batches[:limit]
    next_cursor = None
    if has_more and batches:
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "created_at": batches[-1].created_at.isoformat(),
                    "id": batches[-1].id,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        ).decode("ascii")

    batch_ids = [batch.id for batch in batches]
    candidates_by_batch: dict[str, list[PageCandidateRead]] = {
        batch_id: [] for batch_id in batch_ids
    }
    if batch_ids:
        page_query = select(PageCandidate).where(
            PageCandidate.batch_id.in_(batch_ids),
            PageCandidate.deleted_at.is_(None),
            PageCandidate.status.not_in({"FAILED", "CANCELLED"}),
        )
        asset_query = select(AssetCandidate).where(
            AssetCandidate.batch_id.in_(batch_ids),
            AssetCandidate.deleted_at.is_(None),
            AssetCandidate.status.not_in({"FAILED", "CANCELLED"}),
        )
        if model_alias:
            page_query = page_query.where(PageCandidate.model_alias == model_alias)
            asset_query = asset_query.where(AssetCandidate.model_alias == model_alias)
        if favorite is not None:
            page_query = page_query.where(PageCandidate.is_favorite == favorite)
            asset_query = asset_query.where(AssetCandidate.is_favorite == favorite)
        if resolution:
            page_query = page_query.where(PageCandidate.resolution == resolution)
            asset_query = asset_query.where(AssetCandidate.resolution == resolution)
        for item in db.scalars(
            page_query.order_by(PageCandidate.batch_id, PageCandidate.ordinal.desc())
        ):
            candidates_by_batch[item.batch_id].append(candidate_read(item))
        for item in db.scalars(
            asset_query.order_by(AssetCandidate.batch_id, AssetCandidate.ordinal.desc())
        ):
            candidates_by_batch[item.batch_id].append(asset_candidate_read(item))

    groups = [
        LibraryBatchRead(
            batch=GenerationBatchRead.model_validate(batch),
            candidates=candidates_by_batch[batch.id],
        )
        for batch in batches
    ]
    all_candidates = [candidate for group in groups for candidate in group.candidates]
    return LibraryRead(
        groups=groups,
        total_candidates=len(all_candidates),
        favorite_count=sum(item.is_favorite for item in all_candidates),
        next_cursor=next_cursor,
        limit=limit,
    )
