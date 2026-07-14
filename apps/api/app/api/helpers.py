from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, AssetCandidate, CharacterReference, PageCandidate
from app.schemas import AssetRead, CharacterReferenceRead, PageCandidateRead


def asset_read(asset: Asset) -> AssetRead:
    value = AssetRead.model_validate(asset)
    return value.model_copy(update={"content_url": f"/api/v1/assets/{asset.id}/content"})


def candidate_read(candidate: PageCandidate) -> PageCandidateRead:
    value = PageCandidateRead.model_validate(candidate)
    return value.model_copy(
        update={
            "content_url": (
                f"/api/v1/assets/{candidate.asset_id}/content" if candidate.asset_id else None
            )
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
        content_url=(
            f"/api/v1/assets/{candidate.asset_id}/content" if candidate.asset_id else None
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
