from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.helpers import character_references
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


def _read(db: Session, character: Character) -> CharacterRead:
    return CharacterRead.model_validate(character).model_copy(
        update={"references": character_references(db, character.id)}
    )


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
    db.commit()
    db.refresh(character)
    return _read(db, character)


@router.patch("/characters/{character_id}", response_model=CharacterRead)
def update_character(
    character_id: str,
    payload: CharacterUpdate,
    db: Session = Depends(get_db),
) -> CharacterRead:
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    if character.version != payload.version:
        raise HTTPException(status_code=409, detail="角色已被更新，请刷新后重试")
    values = payload.model_dump(exclude_unset=True, exclude={"version"})
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
    for key, value in values.items():
        setattr(character, key, value)
    character.status = "NEEDS_CONFIRMATION" if character.alias_conflict else "CANONICAL"
    character.version += 1
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
) -> CharacterReference:
    character = db.get(Character, character_id)
    asset = db.get(Asset, payload.asset_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
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
                db.commit()
                db.refresh(existing)
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
    db.commit()
    db.refresh(reference)
    return reference


@router.delete("/character-references/{reference_id}", status_code=status.HTTP_204_NO_CONTENT)
def unbind_reference(reference_id: str, db: Session = Depends(get_db)) -> None:
    reference = db.get(CharacterReference, reference_id)
    if not reference:
        raise HTTPException(status_code=404, detail="角色参考绑定不存在")
    db.delete(reference)
    db.commit()
