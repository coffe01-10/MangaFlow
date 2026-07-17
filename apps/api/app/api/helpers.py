from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, AssetCandidate, CharacterReference, MangaPage, PageCandidate
from app.schemas import AssetRead, CharacterReferenceRead, PageCandidateRead


def asset_read(asset: Asset) -> AssetRead:
    value = AssetRead.model_validate(asset)
    return value.model_copy(
        update={
            "content_url": f"/api/v1/assets/{asset.id}/content",
            "thumbnail_url": f"/api/v1/assets/{asset.id}/thumbnail/640",
        }
    )


def candidate_version_state(
    candidate: PageCandidate, page: MangaPage | None
) -> tuple[str, list[str]]:
    if candidate.based_on_storyboard_version is None:
        if (
            page is not None
            and candidate.is_selected
            and page.selected_candidate_ack_version == page.storyboard_version
        ):
            return "STALE_ACCEPTED", ["GENERATION_VERSION_UNKNOWN"]
        return "LEGACY_UNKNOWN", ["GENERATION_VERSION_UNKNOWN"]
    if page is None or candidate.based_on_storyboard_version == page.storyboard_version:
        return "CURRENT", []
    if (
        candidate.is_selected
        and page.selected_candidate_ack_version == page.storyboard_version
    ):
        return "STALE_ACCEPTED", ["STORYBOARD_CHANGED"]
    return "STALE", ["STORYBOARD_CHANGED"]


def candidate_read(
    candidate: PageCandidate,
    page: MangaPage | None = None,
) -> PageCandidateRead:
    value = PageCandidateRead.model_validate(candidate)
    version_state, staleness_reasons = candidate_version_state(candidate, page)
    return value.model_copy(
        update={
            "prompt_snapshot": candidate.prompt_snapshot,
            "version_state": version_state,
            "staleness_reasons": staleness_reasons,
            "content_url": (
                f"/api/v1/assets/{candidate.asset_id}/content" if candidate.asset_id else None
            ),
            "thumbnail_url": (
                f"/api/v1/assets/{candidate.asset_id}/thumbnail/640"
                if candidate.asset_id
                else None
            ),
        }
    )


def asset_candidate_read(candidate: AssetCandidate) -> PageCandidateRead:
    return PageCandidateRead(
        id=candidate.id,
        batch_id=candidate.batch_id,
        page_id=None,
        ordinal=candidate.ordinal,
        model_alias=candidate.model_alias,
        resolution=candidate.resolution,
        status=candidate.status,
        asset_id=candidate.asset_id,
        job_id=candidate.job_id,
        is_favorite=candidate.is_favorite,
        is_selected=False,
        created_at=candidate.created_at,
        variant=candidate.variant,
        prompt_snapshot=candidate.prompt_snapshot,
        content_url=(
            f"/api/v1/assets/{candidate.asset_id}/content" if candidate.asset_id else None
        ),
        thumbnail_url=(
            f"/api/v1/assets/{candidate.asset_id}/thumbnail/640"
            if candidate.asset_id
            else None
        ),
    )


def character_references(db: Session, character_id: str) -> list[CharacterReferenceRead]:
    return [
        CharacterReferenceRead.model_validate(item)
        for item in db.scalars(
            select(CharacterReference)
            .where(CharacterReference.character_id == character_id)
            .order_by(CharacterReference.is_canonical.desc(), CharacterReference.created_at)
        )
    ]
