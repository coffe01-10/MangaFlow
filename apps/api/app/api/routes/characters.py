from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.helpers import character_references, ensure_project_scope, reject_required_nulls
from app.database import get_db
from app.models import (
    Asset,
    Character,
    CharacterModelPackage,
    CharacterModelPackageVersion,
    CharacterModelPackageVersionReference,
    CharacterReference,
    Project,
)
from app.schemas import (
    CharacterCreate,
    CharacterRead,
    CharacterReferenceCreate,
    CharacterReferenceRead,
    CharacterUpdate,
)
from app.services.character_packages import lock_asset_for_ownership, run_lock_retry

router = APIRouter()


def _normalize(value: str) -> str:
    return "".join(value.split()).casefold()


def _tokens(primary_name: str, aliases: list[str]) -> set[str]:
    return {_normalize(item) for item in [primary_name, *aliases] if item.strip()}


def _has_conflict(
    db: Session,
    project_id: str,
    primary_name: str,
    aliases: list[str],
    exclude_id: str | None = None,
) -> bool:
    incoming = _tokens(primary_name, aliases)
    others = list(db.scalars(select(Character).where(Character.project_id == project_id)))
    return any(
        incoming & _tokens(item.primary_name, item.aliases)
        for item in others
        if item.id != exclude_id
    )


def _recompute_project_conflicts(db: Session, project_id: str) -> None:
    """Refresh ``alias_conflict`` for every character in the project.

    The one-sided check only compared the incoming row against pre-existing
    rows, so the first 「Bob」 kept alias_conflict=false forever after a second
    「Bob」 was created. Flags must reflect the current name set on both sides.
    A peer that flips into conflict also drops to NEEDS_CONFIRMATION (same
    rule as create); a resolved conflict leaves status alone — reference
    approval owns the promotion back to CANONICAL.
    """

    items = list(db.scalars(select(Character).where(Character.project_id == project_id)))
    token_sets = [(item, _tokens(item.primary_name, item.aliases)) for item in items]
    for item, tokens in token_sets:
        conflict = any(
            tokens & other_tokens
            for other, other_tokens in token_sets
            if other.id != item.id
        )
        if conflict != item.alias_conflict:
            item.alias_conflict = conflict
            item.version += 1
            if conflict:
                item.status = "NEEDS_CONFIRMATION"


def _live_reference_exists(db: Session, character_id: str) -> bool:
    return (
        db.scalar(
            select(CharacterReference.id)
            .join(Asset, Asset.id == CharacterReference.asset_id)
            .where(
                CharacterReference.character_id == character_id,
                Asset.deleted_at.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def _read(db: Session, character: Character) -> CharacterRead:
    return CharacterRead.model_validate(character).model_copy(
        update={"references": character_references(db, character.id)}
    )


def _ensure_character_scope(db: Session, character: Character, project_id: str | None) -> None:
    """Issue #143 scope guard for routes keyed by ``character_id``.

    An omitted parameter keeps the historical behavior, a mismatched one
    hides the character behind the shared 「不属于当前项目」 404.
    """

    ensure_project_scope(db, character, project_id, label="角色")


@router.get("/projects/{project_id}/characters", response_model=list[CharacterRead])
def list_characters(project_id: str, db: Session = Depends(get_db)) -> list[CharacterRead]:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    characters = list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project_id)
            .order_by(Character.created_at)
        )
    )
    return [_read(db, item) for item in characters]


@router.post(
    "/projects/{project_id}/characters",
    response_model=CharacterRead,
    status_code=status.HTTP_201_CREATED,
)
def create_character(
    project_id: str,
    payload: CharacterCreate,
    db: Session = Depends(get_db),
) -> CharacterRead:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    aliases = list(dict.fromkeys(item.strip() for item in payload.aliases if item.strip()))
    conflict = _has_conflict(db, project_id, payload.primary_name, aliases)
    character = Character(
        project_id=project_id,
        primary_name=payload.primary_name.strip(),
        aliases=aliases,
        aliases_normalized=[_normalize(item) for item in aliases],
        alias_conflict=conflict,
        canonical_description=payload.canonical_description,
        locked_features=payload.locked_features,
        forbidden_changes=payload.forbidden_changes,
        status="NEEDS_CONFIRMATION" if conflict else "UPLOADED",
    )
    db.add(character)
    # flush so the project-wide conflict recompute (which re-reads every row)
    # sees this insert in the same unit — the session is autoflush=False.
    db.flush()
    _recompute_project_conflicts(db, project_id)
    db.commit()
    db.refresh(character)
    return _read(db, character)


@router.patch("/characters/{character_id}", response_model=CharacterRead)
def update_character(
    character_id: str,
    payload: CharacterUpdate,
    db: Session = Depends(get_db),
    project_id: str | None = None,
) -> CharacterRead:
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    _ensure_character_scope(db, character, project_id)
    values = payload.model_dump(exclude_unset=True, exclude={"version"})
    reject_required_nulls(Character, values)
    primary_name = values.get("primary_name", character.primary_name).strip()
    aliases = list(
        dict.fromkeys(
            item.strip() for item in values.get("aliases", character.aliases) if item.strip()
        )
    )
    values["primary_name"] = primary_name
    values["aliases"] = aliases
    values["aliases_normalized"] = [_normalize(item) for item in aliases]
    values["alias_conflict"] = _has_conflict(
        db, character.project_id, primary_name, aliases, character.id
    )
    # Claim the row with an atomic conditional update so concurrent PATCHes
    # cannot both pass an in-memory version comparison (same pattern as
    # _claim_panel_version / scene asset PATCH).
    claimed = db.execute(
        update(Character)
        .where(Character.id == character.id, Character.version == payload.version)
        .values(version=Character.version + 1)
        .execution_options(synchronize_session=False)
    )
    if not claimed.rowcount:
        db.rollback()
        raise HTTPException(status_code=409, detail="角色已被更新，请刷新后重试")
    for key, value in values.items():
        setattr(character, key, value)
    # Status derives from alias conflict and live references — a metadata edit
    # must not fabricate CANONICAL for a reference-less character (that
    # transition belongs to reference approval; retract demotes back).
    if character.alias_conflict:
        character.status = "NEEDS_CONFIRMATION"
    elif _live_reference_exists(db, character.id):
        character.status = "CANONICAL"
    else:
        character.status = "UPLOADED"
    _recompute_project_conflicts(db, character.project_id)
    db.commit()
    db.refresh(character)
    return _read(db, character)


@router.post(
    "/characters/{character_id}/references",
    response_model=CharacterReferenceRead,
    status_code=status.HTTP_201_CREATED,
)
def bind_reference(
    character_id: str,
    payload: CharacterReferenceCreate,
    db: Session = Depends(get_db),
    project_id: str | None = None,
) -> CharacterReference:
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    _ensure_character_scope(db, character, project_id)

    def _bind() -> CharacterReference:
        asset = lock_asset_for_ownership(db, payload.asset_id)
        if not asset or asset.deleted_at is not None:
            raise HTTPException(status_code=404, detail="参考素材不存在")
        if asset.project_id != character.project_id:
            raise HTTPException(status_code=409, detail="参考图和角色不属于同一项目")
        allowed_generated_kinds = {"character", "outfit"}
        if asset.kind != "CHARACTER_REFERENCE" and not (
            asset.source in {"VERTEX_GENERATED", "AI_GENERATED"}
            and asset.kind in allowed_generated_kinds
        ):
            raise HTTPException(
                status_code=409,
                detail="只有人物参考图或已生成的角色/服装设定页可以绑定角色",
            )
        # Contract §10.3a: an asset referenced by another character's package
        # version matrix (DRAFT or frozen) cannot serve that character here,
        # whether or not it already has a CharacterReference row.
        foreign_package_reference = db.scalar(
            select(CharacterModelPackageVersionReference.id)
            .join(
                CharacterModelPackageVersion,
                CharacterModelPackageVersion.id
                == CharacterModelPackageVersionReference.version_id,
            )
            .join(
                CharacterModelPackage,
                CharacterModelPackage.id == CharacterModelPackageVersion.package_id,
            )
            .where(
                CharacterModelPackageVersionReference.asset_id == asset.id,
                CharacterModelPackage.character_id != character_id,
            )
            .limit(1)
        )
        if foreign_package_reference:
            raise HTTPException(
                status_code=409,
                detail="该素材已被角色模型包版本引用，请先在对应版本中解绑或放弃换绑",
            )
        existing = db.scalar(
            select(CharacterReference).where(CharacterReference.asset_id == asset.id)
        )
        if existing:
            if existing.character_id == character_id:
                if payload.is_canonical and not existing.is_canonical:
                    db.execute(
                        update(CharacterReference)
                        .where(CharacterReference.character_id == character_id)
                        .values(is_canonical=False)
                    )
                    existing.is_canonical = True
                return existing
            db.delete(existing)
            db.flush()
        if payload.is_canonical:
            db.execute(
                update(CharacterReference)
                .where(CharacterReference.character_id == character_id)
                .values(is_canonical=False)
            )
        reference = CharacterReference(
            character_id=character_id,
            asset_id=asset.id,
            angle=payload.angle,
            is_canonical=payload.is_canonical,
        )
        db.add(reference)
        return reference

    reference = run_lock_retry(
        db,
        _bind,
        conflict_detail="角色参考绑定冲突，请稍后重试",
        commit=True,
    )
    db.refresh(reference)
    return reference


@router.delete("/character-references/{reference_id}", status_code=status.HTTP_204_NO_CONTENT)
def unbind_reference(
    reference_id: str, db: Session = Depends(get_db), project_id: str | None = None
) -> None:
    reference = db.get(CharacterReference, reference_id)
    if not reference:
        raise HTTPException(status_code=404, detail="角色参考绑定不存在")
    ensure_project_scope(db, reference, project_id, label="角色参考绑定")
    db.delete(reference)
    db.commit()
