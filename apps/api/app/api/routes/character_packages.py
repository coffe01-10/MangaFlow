"""Character model package routes (contract §9.1 minimal API surface).

Path mount: ``/api/v1/projects/{project_id}/...`` with the package addressed by
``character_id`` (anchor A1: the package id never enters existing URLs). Every
mutation goes through the shared package row lock; relation writes carry the
parent DRAFT version token as an optimistic-lock CAS value.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Character,
    CharacterModelPackage,
    CharacterModelPackageVersion,
    CharacterModelPackageVersionOutfit,
    CharacterModelPackageVersionReference,
    Project,
)
from app.schemas import (
    CharacterModelPackageCreate,
    CharacterModelPackageUpdate,
    PackageActivateRequest,
    PackageCompletenessRead,
    PackageCoverCreate,
    PackageDiffRead,
    PackageOutfitCreate,
    PackageOutfitDefaultUpdate,
    PackageOutfitDelete,
    PackageOutfitRead,
    PackageRead,
    PackageReferenceCreate,
    PackageReferenceDelete,
    PackageReferenceRead,
    PackageSummaryRead,
    PackageVersionDerive,
    PackageVersionRead,
)
from app.services.character_packages import (
    ROLE_ORDER,
    activate_version,
    archive_package,
    archive_version,
    bind_outfit,
    bind_reference,
    completeness,
    create_package,
    delete_draft_version,
    derive_version,
    get_package,
    owned_version,
    publish_version,
    restore_package,
    restore_version,
    set_cover,
    set_default_outfit,
    unbind_outfit,
    unbind_reference,
    update_package_workspace,
    version_diff,
)

router = APIRouter()

_MAX_PAGE_SIZE = 200


def _reference_sort_key(item: CharacterModelPackageVersionReference):
    role_rank = ROLE_ORDER.index(item.role) if item.role in ROLE_ORDER else len(ROLE_ORDER)
    return (role_rank, item.sort_order, item.created_at, item.id)


def _version_read(
    db: Session, package: CharacterModelPackage, version: CharacterModelPackageVersion
) -> PackageVersionRead:
    references = list(
        db.scalars(
            select(CharacterModelPackageVersionReference).where(
                CharacterModelPackageVersionReference.version_id == version.id
            )
        )
    )
    outfits = list(
        db.scalars(
            select(CharacterModelPackageVersionOutfit).where(
                CharacterModelPackageVersionOutfit.version_id == version.id
            )
        )
    )
    outfits.sort(key=lambda item: (item.sort_order, item.created_at, item.id))
    return PackageVersionRead(
        id=version.id,
        package_id=version.package_id,
        version_number=version.version_number,
        status=version.status,
        spec_snapshot=version.spec_snapshot,
        derived_from_version_id=version.derived_from_version_id,
        published_at=version.published_at,
        created_at=version.created_at,
        updated_at=version.updated_at,
        version=version.version,
        references=[
            PackageReferenceRead.model_validate(item)
            for item in sorted(references, key=_reference_sort_key)
        ],
        outfits=[PackageOutfitRead.model_validate(item) for item in outfits],
        completeness=PackageCompletenessRead.model_validate(
            completeness(db, package, version)
        ),
    )


def _package_read(
    db: Session, package: CharacterModelPackage, include_versions: bool = True
) -> PackageRead:
    versions = list(
        db.scalars(
            select(CharacterModelPackageVersion)
            .where(CharacterModelPackageVersion.package_id == package.id)
            .order_by(CharacterModelPackageVersion.version_number.desc())
        )
    )
    published = (
        db.get(CharacterModelPackageVersion, package.published_version_id)
        if package.published_version_id
        else None
    )
    return PackageRead(
        id=package.id,
        character_id=package.character_id,
        project_id=package.project_id,
        identity_spec=package.identity_spec,
        visual_spec=package.visual_spec,
        negative_constraints=package.negative_constraints,
        published_version_id=package.published_version_id,
        status=package.status,
        created_at=package.created_at,
        updated_at=package.updated_at,
        version=package.version,
        versions=[_version_read(db, package, item) for item in versions]
        if include_versions
        else [],
        completeness=(
            PackageCompletenessRead.model_validate(completeness(db, package, published))
            if published
            else None
        ),
    )


def _package_summary(db: Session, package: CharacterModelPackage) -> PackageSummaryRead:
    character = db.get(Character, package.character_id)
    published = (
        db.get(CharacterModelPackageVersion, package.published_version_id)
        if package.published_version_id
        else None
    )
    return PackageSummaryRead(
        id=package.id,
        character_id=package.character_id,
        project_id=package.project_id,
        status=package.status,
        published_version_id=package.published_version_id,
        created_at=package.created_at,
        updated_at=package.updated_at,
        version=package.version,
        character={
            "id": character.id,
            "primary_name": character.primary_name,
            "aliases": character.aliases,
            "alias_conflict": character.alias_conflict,
        }
        if character
        else {
            "id": package.character_id,
            "primary_name": package.character_id,
            "aliases": [],
            "alias_conflict": False,
        },
        published_version_number=published.version_number if published else None,
        published_completeness=(
            PackageCompletenessRead.model_validate(completeness(db, package, published))
            if published
            else None
        ),
    )


def _project_exists(db: Session, project_id: str) -> None:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")


@router.get(
    "/projects/{project_id}/character-packages",
    response_model=list[PackageSummaryRead],
)
def list_packages(
    project_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[PackageSummaryRead]:
    _project_exists(db, project_id)
    if status_filter not in {None, "ACTIVE", "ARCHIVED"}:
        raise HTTPException(status_code=422, detail="状态筛选只支持 ACTIVE 或 ARCHIVED")
    if limit < 1 or limit > _MAX_PAGE_SIZE:
        raise HTTPException(status_code=422, detail=f"limit 必须在 1-{_MAX_PAGE_SIZE} 之间")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset 不能为负数")
    query = select(CharacterModelPackage).where(
        CharacterModelPackage.project_id == project_id
    )
    if status_filter:
        query = query.where(CharacterModelPackage.status == status_filter)
    query = query.order_by(
        CharacterModelPackage.created_at, CharacterModelPackage.id
    ).limit(limit).offset(offset)
    return [_package_summary(db, item) for item in db.scalars(query)]


@router.post(
    "/projects/{project_id}/characters/{character_id}/package",
    response_model=PackageRead,
    status_code=status.HTTP_201_CREATED,
)
def create_character_package(
    project_id: str,
    character_id: str,
    payload: CharacterModelPackageCreate,
    db: Session = Depends(get_db),
) -> PackageRead:
    package = create_package(db, project_id, character_id, payload.model_dump())
    return _package_read(db, package)


@router.get("/projects/{project_id}/characters/{character_id}/package", response_model=PackageRead)
def get_character_package(
    project_id: str,
    character_id: str,
    include_versions: bool = True,
    db: Session = Depends(get_db),
) -> PackageRead:
    return _package_read(db, get_package(db, project_id, character_id), include_versions)


@router.patch(
    "/projects/{project_id}/characters/{character_id}/package", response_model=PackageRead
)
def update_character_package(
    project_id: str,
    character_id: str,
    payload: CharacterModelPackageUpdate,
    db: Session = Depends(get_db),
) -> PackageRead:
    # exclude_unset: PATCH must only replace the blocks the client actually
    # sent; omitted optional spec blocks keep their stored values.
    package = update_package_workspace(
        db, project_id, character_id, payload.model_dump(exclude_unset=True)
    )
    return _package_read(db, package)


@router.post(
    "/projects/{project_id}/characters/{character_id}/package/versions",
    response_model=PackageVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_package_version(
    project_id: str,
    character_id: str,
    payload: PackageVersionDerive,
    db: Session = Depends(get_db),
) -> PackageVersionRead:
    package = get_package(db, project_id, character_id)
    version = derive_version(db, project_id, character_id, payload.base_version_id)
    return _version_read(db, package, version)


@router.post(
    "/projects/{project_id}/characters/{character_id}/package/versions/{version_id}/publish",
    response_model=PackageVersionRead,
)
def publish_character_package_version(
    project_id: str,
    character_id: str,
    version_id: str,
    db: Session = Depends(get_db),
) -> PackageVersionRead:
    package = get_package(db, project_id, character_id)
    version = publish_version(db, project_id, character_id, version_id)
    return _version_read(db, package, version)


@router.post(
    "/projects/{project_id}/characters/{character_id}/package/archive",
    response_model=PackageRead,
)
def archive_character_package(
    project_id: str, character_id: str, db: Session = Depends(get_db)
) -> PackageRead:
    package = archive_package(db, project_id, character_id)
    return _package_read(db, package)


@router.post(
    "/projects/{project_id}/characters/{character_id}/package/restore",
    response_model=PackageRead,
)
def restore_character_package(
    project_id: str, character_id: str, db: Session = Depends(get_db)
) -> PackageRead:
    package = restore_package(db, project_id, character_id)
    return _package_read(db, package)


@router.post(
    "/projects/{project_id}/characters/{character_id}/package/versions/{version_id}/archive",
    response_model=PackageVersionRead,
)
def archive_character_package_version(
    project_id: str,
    character_id: str,
    version_id: str,
    db: Session = Depends(get_db),
) -> PackageVersionRead:
    package = get_package(db, project_id, character_id)
    version = archive_version(db, project_id, character_id, version_id)
    return _version_read(db, package, version)


@router.post(
    "/projects/{project_id}/characters/{character_id}/package/versions/{version_id}/restore",
    response_model=PackageVersionRead,
)
def restore_character_package_version(
    project_id: str,
    character_id: str,
    version_id: str,
    db: Session = Depends(get_db),
) -> PackageVersionRead:
    package = get_package(db, project_id, character_id)
    version = restore_version(db, project_id, character_id, version_id)
    return _version_read(db, package, version)


@router.post(
    "/projects/{project_id}/characters/{character_id}/package/activate",
    response_model=PackageRead,
)
def activate_character_package_version(
    project_id: str,
    character_id: str,
    payload: PackageActivateRequest,
    db: Session = Depends(get_db),
) -> PackageRead:
    package = activate_version(
        db,
        project_id,
        character_id,
        payload.version_id,
        payload.expected_published_version_id,
    )
    return _package_read(db, package)


@router.delete(
    "/projects/{project_id}/characters/{character_id}/package/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_character_package_version(
    project_id: str,
    character_id: str,
    version_id: str,
    db: Session = Depends(get_db),
) -> None:
    delete_draft_version(db, project_id, character_id, version_id)


@router.post(
    "/projects/{project_id}/characters/{character_id}/package/versions/{version_id}/references",
    response_model=PackageReferenceRead,
    status_code=status.HTTP_201_CREATED,
)
def bind_character_package_reference(
    project_id: str,
    character_id: str,
    version_id: str,
    payload: PackageReferenceCreate,
    db: Session = Depends(get_db),
) -> PackageReferenceRead:
    reference = bind_reference(
        db,
        project_id,
        character_id,
        version_id,
        asset_id=payload.asset_id,
        role=payload.role,
        label=payload.label,
        sort_order=payload.sort_order,
        token=payload.version,
    )
    return PackageReferenceRead.model_validate(reference)


@router.put(
    "/projects/{project_id}/characters/{character_id}/package/versions/{version_id}/cover",
    response_model=PackageReferenceRead,
)
def set_character_package_cover(
    project_id: str,
    character_id: str,
    version_id: str,
    payload: PackageCoverCreate,
    db: Session = Depends(get_db),
) -> PackageReferenceRead:
    reference = set_cover(
        db,
        project_id,
        character_id,
        version_id,
        asset_id=payload.asset_id,
        token=payload.version,
    )
    return PackageReferenceRead.model_validate(reference)


@router.delete(
    "/projects/{project_id}/characters/{character_id}/package/versions/{version_id}/references/{reference_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unbind_character_package_reference(
    project_id: str,
    character_id: str,
    version_id: str,
    reference_id: str,
    payload: PackageReferenceDelete,
    db: Session = Depends(get_db),
) -> None:
    unbind_reference(
        db,
        project_id,
        character_id,
        version_id,
        reference_id,
        token=payload.version,
    )


@router.post(
    "/projects/{project_id}/characters/{character_id}/package/versions/{version_id}/outfits",
    response_model=PackageOutfitRead,
    status_code=status.HTTP_201_CREATED,
)
def bind_character_package_outfit(
    project_id: str,
    character_id: str,
    version_id: str,
    payload: PackageOutfitCreate,
    db: Session = Depends(get_db),
) -> PackageOutfitRead:
    relation = bind_outfit(
        db,
        project_id,
        character_id,
        version_id,
        outfit_id=payload.outfit_id,
        is_default=payload.is_default,
        sort_order=payload.sort_order,
        token=payload.version,
    )
    return PackageOutfitRead.model_validate(relation)


@router.patch(
    "/projects/{project_id}/characters/{character_id}/package/versions/{version_id}/outfits/{outfit_id}",
    response_model=PackageOutfitRead,
)
def set_default_character_package_outfit(
    project_id: str,
    character_id: str,
    version_id: str,
    outfit_id: str,
    payload: PackageOutfitDefaultUpdate,
    db: Session = Depends(get_db),
) -> PackageOutfitRead:
    relation = set_default_outfit(
        db,
        project_id,
        character_id,
        version_id,
        outfit_id,
        is_default=payload.is_default,
        token=payload.version,
    )
    return PackageOutfitRead.model_validate(relation)


@router.delete(
    "/projects/{project_id}/characters/{character_id}/package/versions/{version_id}/outfits/{outfit_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unbind_character_package_outfit(
    project_id: str,
    character_id: str,
    version_id: str,
    outfit_id: str,
    payload: PackageOutfitDelete,
    db: Session = Depends(get_db),
) -> None:
    unbind_outfit(
        db,
        project_id,
        character_id,
        version_id,
        outfit_id,
        token=payload.version,
    )


@router.get(
    "/projects/{project_id}/characters/{character_id}/package/diff",
    response_model=PackageDiffRead,
)
def diff_character_package(
    project_id: str,
    character_id: str,
    base_version_id: str,
    target_version_id: str,
    db: Session = Depends(get_db),
) -> dict:
    return version_diff(
        db, project_id, character_id, base_version_id, target_version_id
    )


@router.get(
    "/projects/{project_id}/characters/{character_id}/package/versions/{version_id}/completeness",
    response_model=PackageCompletenessRead,
)
def get_character_package_version_completeness(
    project_id: str,
    character_id: str,
    version_id: str,
    db: Session = Depends(get_db),
) -> dict:
    package = get_package(db, project_id, character_id)
    version = owned_version(db, package, version_id)
    return completeness(db, package, version)
