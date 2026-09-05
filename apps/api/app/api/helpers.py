import re
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Asset,
    AssetCandidate,
    Character,
    CharacterReference,
    ExportBundle,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    ModelCallAttempt,
    Outfit,
    PageCandidate,
    StyleProfile,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowVersion,
)
from app.schemas import AssetRead, CharacterReferenceRead, PageCandidateRead


class ProjectOwned(Protocol):
    project_id: str


ProjectScopeResolver = Callable[[Session, Any], "str | None"]


def _scope_from_column(db: Session, obj: ProjectOwned) -> str | None:
    return obj.project_id


def _scope_via_generation_batch(
    db: Session, obj: PageCandidate | AssetCandidate
) -> str | None:
    batch = db.get(GenerationBatch, obj.batch_id)
    return batch.project_id if batch else None


def _scope_via_generation_job(db: Session, obj: ModelCallAttempt) -> str | None:
    if obj.project_id is not None:
        return obj.project_id
    job = db.get(GenerationJob, obj.job_id) if obj.job_id else None
    return job.project_id if job else None


def _scope_via_character(db: Session, obj: CharacterReference) -> str | None:
    character = db.get(Character, obj.character_id)
    return character.project_id if character else None


def _scope_via_workflow_definition(db: Session, obj: WorkflowVersion) -> str | None:
    workflow = db.get(WorkflowDefinition, obj.workflow_id)
    return workflow.project_id if workflow else None


# One resolver per entity (issue #143): the mapping table keeps every project
# ownership chain explicit instead of an if/else pyramid at each call site.
_PROJECT_SCOPE_RESOLVERS: Mapping[type, ProjectScopeResolver] = {
    GenerationJob: _scope_from_column,
    GenerationBatch: _scope_from_column,
    Asset: _scope_from_column,
    Outfit: _scope_from_column,
    StyleProfile: _scope_from_column,
    ExportBundle: _scope_from_column,
    WorkflowDefinition: _scope_from_column,
    WorkflowRun: _scope_from_column,
    PageCandidate: _scope_via_generation_batch,
    AssetCandidate: _scope_via_generation_batch,
    ModelCallAttempt: _scope_via_generation_job,
    CharacterReference: _scope_via_character,
    WorkflowVersion: _scope_via_workflow_definition,
}


def resolve_project_scope(db: Session, obj: Any) -> str | None:
    """Resolve the project owning ``obj`` directly or through its parent row.

    ``None`` means the ownership chain is broken (orphaned parent / unlinked
    audit row); callers treat that as "not owned by any project the caller
    named" so the scoped path fails closed.
    """

    resolver = _PROJECT_SCOPE_RESOLVERS.get(type(obj))
    if resolver is None:
        raise TypeError(f"no project scope resolver registered for {type(obj).__name__}")
    return resolver(db, obj)


def ensure_project_scope(
    db: Session,
    obj: Any,
    project_id: str | None,
    *,
    label: str,
) -> None:
    """Return a 404 unless ``obj`` belongs to ``project_id`` (issue #143).

    The object-id routes carry no project path segment and the web client
    never sends one, so ``project_id`` arrives as an optional query parameter:
    omitting it keeps the historical behavior for existing callers, while a
    mismatched value hides the object behind the same 「不属于当前项目」 404
    the scoped list/bulk endpoints already return.
    """

    if project_id is None:
        return
    if resolve_project_scope(db, obj) != project_id:
        raise HTTPException(status_code=404, detail=f"{label}不存在或不属于当前项目")


# A JSON ``\ud800`` escape becomes a lone surrogate code point inside the
# parsed Python string. Lone surrogates cannot be bound to the database
# (UnicodeEncodeError) or re-encoded into a response, and any surrogate left
# in a Python str is by definition unpaired — the JSON parser already
# combines well-formed pairs into single supplementary characters. Replacing
# them with U+FFFD keeps legal text byte-for-byte identical and only scrubs
# the unrepresentable code points.
_LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def sanitize_surrogates(value: Any) -> Any:
    """Return ``value`` with lone surrogates replaced by U+FFFD, recursively.

    Pydantic already rejects surrogates in typed ``str`` fields (422), but
    untyped ``dict``/``Any`` payloads (panel actions, scene palettes, legacy
    regions) pass validation and poison the stored row — the DB bind or the
    response encoding then fails. Models are copied, not mutated, so
    ``model_dump``/``exclude_unset`` semantics in callers stay intact.
    """

    if isinstance(value, str):
        return _LONE_SURROGATE_RE.sub("\ufffd", value)
    if isinstance(value, dict):
        return {key: sanitize_surrogates(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_surrogates(item) for item in value]
    if isinstance(value, BaseModel):
        updates = {}
        for name, item in vars(value).items():
            if name.startswith("__"):
                continue
            cleaned = sanitize_surrogates(item)
            if cleaned is not item:
                updates[name] = cleaned
        return value.model_copy(update=updates) if updates else value
    return value


def reject_required_nulls(model_cls: Any, changes: Mapping[str, Any]) -> None:
    """Reject explicit ``null`` for NOT NULL columns with a 422.

    ``exclude_unset`` keeps explicit nulls, and applying them to non-nullable
    columns surfaced as raw IntegrityError / AttributeError / TypeError 500s
    instead of a validation error. Nullable columns may still be cleared.

    As a second duty, string values (recursively, inside dicts/lists) are
    scrubbed of lone surrogates in place: every caller applies this mapping
    straight to ORM columns, where a surrogate would fail the DB bind with a
    500. Legal text is unchanged.
    """

    mapper = sa_inspect(model_cls)
    offending = []
    for key, value in changes.items():
        if value is not None:
            continue
        prop = mapper.attrs.get(key)
        columns = getattr(prop, "columns", None) if prop is not None else None
        if columns and columns[0].nullable is False:
            offending.append(key)
    if offending:
        raise HTTPException(
            status_code=422,
            detail=f"字段不能为 null：{', '.join(sorted(offending))}",
        )
    if isinstance(changes, dict):
        for key, value in changes.items():
            cleaned = sanitize_surrogates(value)
            if cleaned is not value:
                changes[key] = cleaned


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
            .join(Asset, Asset.id == CharacterReference.asset_id)
            .where(
                CharacterReference.character_id == character_id,
                Asset.deleted_at.is_(None),
            )
            .order_by(CharacterReference.is_canonical.desc(), CharacterReference.created_at)
        )
    ]
