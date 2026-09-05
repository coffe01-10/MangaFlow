"""Character model package service: workspec, versions, relations, completeness.

State machine and error boundaries follow
docs/v02-character-model-package-contract.md §5–§7:

- Package/version state transitions are single-transaction operations that
  take the package row lock first (PostgreSQL ``FOR UPDATE``; SQLite write-lock
  savepoints) and re-verify the target under that lock, so publish/activate/
  archive cannot interleave into a pointer that points at an ARCHIVED version.
- Spec edits use the package ``version`` optimistic token; relation edits use a
  compare-and-increment on the parent DRAFT version's ``version`` token, so two
  edits carrying the same token cannot silently overwrite each other.
- Completeness is a read-path recommendation only: deterministic, never stored,
  never entering production gates (contract §7). DRAFT reads the package
  workspec; READY+ reads the frozen ``spec_snapshot``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models import (
    Asset,
    Character,
    CharacterModelPackage,
    CharacterModelPackageVersion,
    CharacterModelPackageVersionOutfit,
    CharacterModelPackageVersionReference,
    CharacterReference,
    MangaPage,
    Outfit,
    Panel,
    Project,
    StyleProfile,
    utcnow,
)
from app.services.ordinal_allocator import (
    ORDINAL_ALLOCATION_MAX_ATTEMPTS,
    is_sqlite_lock_error,
    lock_entity,
    ordinal_savepoint,
    pause_before_ordinal_retry,
)

PACKAGE_ACTIVE = "ACTIVE"
PACKAGE_ARCHIVED = "ARCHIVED"
VERSION_DRAFT = "DRAFT"
VERSION_READY = "READY"
VERSION_IN_PRODUCTION = "IN_PRODUCTION"
VERSION_ARCHIVED = "ARCHIVED"

CORE_ROLES = {"cover", "front", "side", "back", "three_quarter"}
LABELED_ROLES = {"expression", "pose", "extra"}
ALLOWED_ROLES = CORE_ROLES | LABELED_ROLES
ROLE_ORDER = ["cover", "front", "side", "back", "three_quarter", "expression", "pose", "extra"]
IDENTITY_KEYS = ("age_appearance", "gender", "personality", "identity_notes")
VISUAL_KEYS = ("hair", "hair_color", "face", "eyes", "body", "distinguishing_marks")
SPEC_LIMITS = {
    "age_appearance": 120,
    "gender": 32,
    "personality": 800,
    "identity_notes": 2000,
    "hair": 400,
    "hair_color": 400,
    "face": 400,
    "eyes": 400,
    "body": 400,
    "distinguishing_marks": 400,
}
VIEW_POINTS = {"front": 15, "side": 10, "back": 10, "three_quarter": 5}
EXPRESSION_CAP = 4
EXPRESSION_POINTS = 5
IDENTITY_POINTS_PER_KEY = 5
OUTFIT_BOUND_POINTS = 15
OUTFIT_DEFAULT_POINTS = 5


@dataclass
class PackageResolution:
    """§8.1 resolution of one character for one page candidate.

    ``mode`` is ``"explicit"`` (the UI picked an archived/live version), or
    ``"published"`` (default inheritance), or ``"legacy"`` when no package or
    no published version applies (validation stays bytewise as before).
    """

    package: CharacterModelPackage | None
    version: CharacterModelPackageVersion | None
    mode: str
    character_id: str = ""
    character_name: str = ""
    character_asset_id: str | None = None
    reference_role: str | None = None
    reference_label: str | None = None
    outfit_id: str | None = None
    outfit_asset_id: str | None = None
    default_outfit_has_live_reference: bool = False

    @property
    def is_package_mode(self) -> bool:
        return self.mode in {"explicit", "published"}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def spec_fingerprint(spec_snapshot: dict) -> str:
    """Canonical-JSON sha256 used as the audit/dedupe fingerprint (contract §8.2)."""
    return "sha256:" + hashlib.sha256(_json(spec_snapshot).encode("utf-8")).hexdigest()


def _strip_or_none(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="规格取值必须是文本")
    stripped = value.strip()
    return stripped or None


def _validate_identity_spec(value: dict) -> dict:
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="identity_spec 必须是对象")
    unknown = set(value) - set(IDENTITY_KEYS)
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"identity_spec 存在未知键: {sorted(unknown)}"
        )
    normalized: dict[str, str] = {}
    for key in IDENTITY_KEYS:
        text = _strip_or_none(value.get(key))
        if text is None:
            continue
        limit = SPEC_LIMITS[key]
        if len(text) > limit:
            raise HTTPException(
                status_code=422, detail=f"identity_spec.{key} 超出长度上限"
            )
        normalized[key] = text
    return normalized


def _validate_visual_spec(value: dict) -> dict:
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="visual_spec 必须是对象")
    unknown = set(value) - set(VISUAL_KEYS)
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"visual_spec 存在未知键: {sorted(unknown)}"
        )
    normalized: dict[str, str] = {}
    for key in VISUAL_KEYS:
        text = _strip_or_none(value.get(key))
        if text is None:
            continue
        limit = SPEC_LIMITS[key]
        if len(text) > limit:
            raise HTTPException(
                status_code=422, detail=f"visual_spec.{key} 超出长度上限"
            )
        normalized[key] = text
    return normalized


def _validate_negative_constraints(value: list) -> list:
    if not isinstance(value, list):
        raise HTTPException(status_code=422, detail="negative_constraints 必须是数组")
    if len(value) > 20:
        raise HTTPException(status_code=422, detail="负面约束最多 20 项")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise HTTPException(status_code=422, detail="负面约束每项必须是文本")
        text = item.strip()
        if not text:
            continue
        if len(text) > 120:
            raise HTTPException(status_code=422, detail="负面约束每项最多 120 字")
        if text not in normalized:
            normalized.append(text)
    return normalized


def _normalize_spec_payload(payload: dict) -> dict:
    return {
        "identity_spec": _validate_identity_spec(payload.get("identity_spec") or {}),
        "visual_spec": _validate_visual_spec(payload.get("visual_spec") or {}),
        "negative_constraints": _validate_negative_constraints(
            payload.get("negative_constraints") or []
        ),
    }


def _project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _character(db: Session, project_id: str, character_id: str) -> Character:
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    if character.project_id != project_id:
        raise HTTPException(status_code=404, detail="角色不属于当前项目")
    return character


def _package(db: Session, project_id: str, character_id: str) -> CharacterModelPackage:
    package = db.scalar(
        select(CharacterModelPackage).where(
            CharacterModelPackage.character_id == character_id
        )
    )
    if not package:
        raise HTTPException(status_code=404, detail="角色模型包不存在")
    if package.project_id != project_id:
        raise HTTPException(status_code=404, detail="角色模型包不属于当前项目")
    return package


def _owned_version(
    db: Session, package: CharacterModelPackage, version_id: str
) -> CharacterModelPackageVersion:
    version = db.get(CharacterModelPackageVersion, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="模型包版本不存在")
    if version.package_id != package.id:
        raise HTTPException(status_code=409, detail="版本不属于当前角色模型包")
    return version


def owned_version(
    db: Session, package: CharacterModelPackage, version_id: str
) -> CharacterModelPackageVersion:
    """Public read-path accessor for versions owning a given package."""
    return _owned_version(db, package, version_id)


def _require_draft_version(
    db: Session, package: CharacterModelPackage
) -> CharacterModelPackageVersion:
    version = db.scalar(
        select(CharacterModelPackageVersion).where(
            CharacterModelPackageVersion.package_id == package.id,
            CharacterModelPackageVersion.status == VERSION_DRAFT,
        )
    )
    if not version:
        raise HTTPException(
            status_code=409,
            detail="当前没有草稿版本，请先派生或创建草稿版本后再编辑",
        )
    return version


def _check_version_token(version: CharacterModelPackageVersion, token: int) -> None:
    if version.version != token:
        raise HTTPException(
            status_code=409, detail="模型包版本已被更新，请刷新后重试"
        )


def run_package_transaction(
    db: Session,
    package_id: str,
    fn,
    *,
    max_attempts: int = ORDINAL_ALLOCATION_MAX_ATTEMPTS,
):
    """Run ``fn(package)`` under the package row lock with limited retries.

    All writers use the same lock order: the package row first, then the
    bound Asset row for reference changes, then versions/relations. A failed
    final commit rolls the whole operation back and surfaces a controlled 409;
    there is no half-published state.
    """

    last_error: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            with ordinal_savepoint(db):
                package = lock_entity(db, CharacterModelPackage, package_id)
                if package is None:
                    raise HTTPException(status_code=404, detail="角色模型包不存在")
                db.expire_all()
                package = db.get(CharacterModelPackage, package_id)
                result = fn(package)
                db.flush()
            db.commit()
            return result
        except (IntegrityError, OperationalError) as error:
            if isinstance(error, OperationalError) and not is_sqlite_lock_error(error):
                raise
            last_error = error
            db.rollback()
            db.expire_all()
            pause_before_ordinal_retry(attempt, max_attempts)
    raise HTTPException(status_code=409, detail="模型包操作冲突，请稍后重试") from last_error


def run_lock_retry(
    db: Session,
    fn,
    *,
    conflict_detail: str,
    max_attempts: int = ORDINAL_ALLOCATION_MAX_ATTEMPTS,
    commit: bool = False,
):
    """Retry ``fn()`` on SQLite lock/busy with a full rollback.

    Callers that take ``lock_asset_for_ownership`` outside
    ``run_package_transaction`` must use this so ``SQLITE_BUSY`` becomes a
    controlled 409 instead of an unhandled 500. ``fn`` must be the first
    writer in the caller's unit: a failed attempt rolls back the session.
    """

    last_error: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            with ordinal_savepoint(db):
                result = fn()
                db.flush()
            if commit:
                db.commit()
            return result
        except (IntegrityError, OperationalError) as error:
            if isinstance(error, OperationalError) and not is_sqlite_lock_error(error):
                raise
            last_error = error
            db.rollback()
            db.expire_all()
            pause_before_ordinal_retry(attempt, max_attempts)
    raise HTTPException(status_code=409, detail=conflict_detail) from last_error


def create_package(
    db: Session, project_id: str, character_id: str, payload: dict
) -> CharacterModelPackage:
    """§5.3-1: create the compatible ACTIVE package plus an initial V1 DRAFT."""
    _project(db, project_id)
    character = _character(db, project_id, character_id)
    normalized = _normalize_spec_payload(payload)
    project = lock_entity(db, Project, project_id)
    if project is None or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")

    def _create() -> CharacterModelPackage:
        # Issue #145-A: the duplicate read, both INSERT flushes and the commit
        # share one retry window. A concurrent create that slipped past the
        # read surfaces as IntegrityError (uq_character_model_packages_character
        # on PostgreSQL) or a SQLite busy/snapshot lock inside run_lock_retry;
        # each failed attempt rolls back and re-runs the read, so the loser
        # gets the designed 409 instead of an unhandled 500.
        db.expire_all()
        existing = db.scalar(
            select(CharacterModelPackage.id).where(
                CharacterModelPackage.character_id == character.id
            )
        )
        if existing:
            raise HTTPException(status_code=409, detail="角色模型包已存在")
        package = CharacterModelPackage(
            character_id=character.id,
            project_id=project_id,
            identity_spec=normalized["identity_spec"],
            visual_spec=normalized["visual_spec"],
            negative_constraints=normalized["negative_constraints"],
            status=PACKAGE_ACTIVE,
        )
        db.add(package)
        db.flush()
        db.add(
            CharacterModelPackageVersion(
                package_id=package.id,
                version_number=1,
                status=VERSION_DRAFT,
                spec_snapshot={
                    "identity_spec": normalized["identity_spec"],
                    "visual_spec": normalized["visual_spec"],
                    "negative_constraints": normalized["negative_constraints"],
                    "frozen_from": "package",
                },
            )
        )
        db.flush()
        return package

    package = run_lock_retry(
        db,
        _create,
        conflict_detail="角色模型包已存在",
        commit=True,
    )
    db.refresh(package)
    return package


def get_package(db: Session, project_id: str, character_id: str) -> CharacterModelPackage:
    _project(db, project_id)
    return _package(db, project_id, character_id)


def update_package_workspace(
    db: Session, project_id: str, character_id: str, payload: dict
) -> CharacterModelPackage:
    """§5.3-2: edit the package workspec under compare-and-increment."""
    package = _package(db, project_id, character_id)
    token = payload.get("version")
    if not isinstance(token, int) or token < 1:
        raise HTTPException(status_code=422, detail="缺少版本令牌")

    def _update(current: CharacterModelPackage) -> CharacterModelPackage:
        if current.version != token:
            raise HTTPException(status_code=409, detail="角色模型包已被更新，请刷新后重试")
        _require_draft_version(db, current)
        if "identity_spec" in payload:
            current.identity_spec = _validate_identity_spec(payload["identity_spec"] or {})
        if "visual_spec" in payload:
            current.visual_spec = _validate_visual_spec(payload["visual_spec"] or {})
        if "negative_constraints" in payload:
            current.negative_constraints = _validate_negative_constraints(
                payload["negative_constraints"] or []
            )
        current.version += 1
        return current

    return run_package_transaction(db, package.id, _update)


def derive_version(
    db: Session,
    project_id: str,
    character_id: str,
    base_version_id: str | None = None,
) -> CharacterModelPackageVersion:
    """§5.3-4: derive V(n+1) from a published/archived base (never DRAFT)."""
    package = _package(db, project_id, character_id)

    def _derive(current: CharacterModelPackage) -> CharacterModelPackageVersion:
        draft = db.scalar(
            select(CharacterModelPackageVersion).where(
                CharacterModelPackageVersion.package_id == current.id,
                CharacterModelPackageVersion.status == VERSION_DRAFT,
            )
        )
        if draft:
            raise HTTPException(status_code=409, detail="已有草稿版本，请先发布或删除该草稿")
        if base_version_id:
            base = _owned_version(db, current, base_version_id)
        elif current.published_version_id:
            base = _owned_version(db, current, current.published_version_id)
        else:
            raise HTTPException(
                status_code=409, detail="包还没有已发布版本，无法派生新版本"
            )
        if base.status == VERSION_DRAFT:
            raise HTTPException(status_code=422, detail="不能从草稿版本派生")
        next_number = (
            db.scalar(
                select(func.max(CharacterModelPackageVersion.version_number)).where(
                    CharacterModelPackageVersion.package_id == current.id
                )
            )
            or 0
        ) + 1
        base_snapshot = dict(base.spec_snapshot or {})
        base_snapshot["frozen_from"] = "derive"
        new_version = CharacterModelPackageVersion(
            package_id=current.id,
            version_number=next_number,
            status=VERSION_DRAFT,
            spec_snapshot=base_snapshot,
            derived_from_version_id=base.id,
        )
        db.add(new_version)
        db.flush()
        # Copy the whole relation matrix from the base version.
        for reference in db.scalars(
            select(CharacterModelPackageVersionReference).where(
                CharacterModelPackageVersionReference.version_id == base.id
            )
        ):
            db.add(
                CharacterModelPackageVersionReference(
                    version_id=new_version.id,
                    asset_id=reference.asset_id,
                    role=reference.role,
                    label=reference.label,
                    sort_order=reference.sort_order,
                )
            )
        for outfit in db.scalars(
            select(CharacterModelPackageVersionOutfit).where(
                CharacterModelPackageVersionOutfit.version_id == base.id
            )
        ):
            db.add(
                CharacterModelPackageVersionOutfit(
                    version_id=new_version.id,
                    outfit_id=outfit.outfit_id,
                    is_default=outfit.is_default,
                    sort_order=outfit.sort_order,
                )
            )
        db.flush()
        # The workspec is replaced wholesale: an editor holding the old package
        # version token gets a 409 on the next PATCH instead of overwriting the
        # derived draft (contract §5.3-4).
        current.identity_spec = _validate_identity_spec(
            base_snapshot.get("identity_spec") or {}
        )
        current.visual_spec = _validate_visual_spec(base_snapshot.get("visual_spec") or {})
        current.negative_constraints = _validate_negative_constraints(
            base_snapshot.get("negative_constraints") or []
        )
        current.version += 1
        return new_version

    return run_package_transaction(db, package.id, _derive)


def publish_version(
    db: Session, project_id: str, character_id: str, version_id: str
) -> CharacterModelPackageVersion:
    """§5.3-3: freeze the workspec into the version and set the pointer atomically."""
    package = _package(db, project_id, character_id)

    def _publish(current: CharacterModelPackage) -> CharacterModelPackageVersion:
        version = _owned_version(db, current, version_id)
        if version.status != VERSION_DRAFT:
            raise HTTPException(status_code=409, detail="只有草稿版本可以发布")
        active_references = (
            db.scalar(
                select(func.count(CharacterModelPackageVersionReference.id))
                .join(Asset, Asset.id == CharacterModelPackageVersionReference.asset_id)
                .where(
                    CharacterModelPackageVersionReference.version_id == version.id,
                    Asset.deleted_at.is_(None),
                )
            )
            or 0
        )
        if active_references == 0:
            raise HTTPException(
                status_code=422, detail="版本至少需要一张有效参考图才能发布"
            )
        frozen = {
            "identity_spec": dict(current.identity_spec or {}),
            "visual_spec": dict(current.visual_spec or {}),
            "negative_constraints": list(current.negative_constraints or []),
            "frozen_from": "package",
        }
        updated = db.execute(
            update(CharacterModelPackageVersion)
            .where(
                CharacterModelPackageVersion.id == version.id,
                CharacterModelPackageVersion.package_id == current.id,
                CharacterModelPackageVersion.status == VERSION_DRAFT,
            )
            .values(
                status=VERSION_READY,
                published_at=utcnow(),
                spec_snapshot=frozen,
            )
            .execution_options(synchronize_session=False)
        )
        if updated.rowcount != 1:
            raise HTTPException(status_code=409, detail="版本已被并行发布，请刷新后重试")
        current.published_version_id = version.id
        db.flush()
        db.expire_all()
        return db.get(CharacterModelPackageVersion, version.id)

    return run_package_transaction(db, package.id, _publish)


def archive_package(db: Session, project_id: str, character_id: str) -> CharacterModelPackage:
    package = _package(db, project_id, character_id)

    def _archive(current: CharacterModelPackage) -> CharacterModelPackage:
        current.status = PACKAGE_ARCHIVED
        return current

    return run_package_transaction(db, package.id, _archive)


def restore_package(db: Session, project_id: str, character_id: str) -> CharacterModelPackage:
    package = _package(db, project_id, character_id)

    def _restore(current: CharacterModelPackage) -> CharacterModelPackage:
        current.status = PACKAGE_ACTIVE
        return current

    return run_package_transaction(db, package.id, _restore)


def archive_version(
    db: Session, project_id: str, character_id: str, version_id: str
) -> CharacterModelPackageVersion:
    """§5.3-6: ARCHIVED only from READY/IN_PRODUCTION and never while published."""
    package = _package(db, project_id, character_id)

    def _archive(current: CharacterModelPackage) -> CharacterModelPackageVersion:
        version = _owned_version(db, current, version_id)
        if version.status not in {VERSION_READY, VERSION_IN_PRODUCTION}:
            raise HTTPException(status_code=409, detail="只能归档已发布或生产中的版本")
        if version.id == current.published_version_id:
            raise HTTPException(
                status_code=409, detail="请先切换到其他发布版本再归档当前版本"
            )
        version.status = VERSION_ARCHIVED
        db.flush()
        return version

    return run_package_transaction(db, package.id, _archive)


def restore_version(
    db: Session, project_id: str, character_id: str, version_id: str
) -> CharacterModelPackageVersion:
    """§5.3-6: ARCHIVED -> READY; never restores the publish pointer alone."""
    package = _package(db, project_id, character_id)

    def _restore(current: CharacterModelPackage) -> CharacterModelPackageVersion:
        version = _owned_version(db, current, version_id)
        if version.status != VERSION_ARCHIVED:
            raise HTTPException(status_code=409, detail="只有归档版本可以恢复")
        version.status = VERSION_READY
        db.flush()
        return version

    return run_package_transaction(db, package.id, _restore)


def activate_version(
    db: Session,
    project_id: str,
    character_id: str,
    version_id: str,
    expected_published_version_id: str | None,
) -> CharacterModelPackage:
    """§5.3-8: switch the publish pointer with a mandatory CAS token."""
    package = _package(db, project_id, character_id)

    def _activate(current: CharacterModelPackage) -> CharacterModelPackage:
        version = _owned_version(db, current, version_id)
        if version.status not in {VERSION_READY, VERSION_IN_PRODUCTION}:
            raise HTTPException(
                status_code=409, detail="归档版本请先恢复后再设为当前版本"
            )
        if current.published_version_id != expected_published_version_id:
            raise HTTPException(
                status_code=409, detail="发布指针已被并行切换，请刷新后重试"
            )
        current.published_version_id = version.id
        db.flush()
        return current

    return run_package_transaction(db, package.id, _activate)


def delete_draft_version(
    db: Session, project_id: str, character_id: str, version_id: str
) -> None:
    """§5.3-7: physical delete of the DRAFT; the package keeps at least one version."""
    package = _package(db, project_id, character_id)

    def _delete(current: CharacterModelPackage) -> None:
        version = _owned_version(db, current, version_id)
        if version.status != VERSION_DRAFT:
            raise HTTPException(status_code=409, detail="只有草稿版本可以删除")
        remaining = db.scalar(
            select(func.count(CharacterModelPackageVersion.id)).where(
                CharacterModelPackageVersion.package_id == current.id
            )
        )
        if (remaining or 0) <= 1:
            raise HTTPException(
                status_code=409,
                detail="包至少保留一个版本；如需弃用请归档包",
            )
        db.delete(version)
        db.flush()

    return run_package_transaction(db, package.id, _delete)


def lock_asset_for_ownership(db: Session, asset_id: str) -> Asset | None:
    """Serialize cross-character binding and DRAFT cleanup on one Asset row.

    Package writers already lock the package row first; this lock is taken
    afterwards so bind/rebind/cleanup cannot both observe the asset as free.
    PostgreSQL uses ``FOR UPDATE``; SQLite issues a no-op row UPDATE so two
    WAL readers cannot both insert a relation for the same previously unowned
    asset. Callers outside ``run_package_transaction`` must wrap this in
    ``run_lock_retry``. Real PostgreSQL race coverage stays PKG-S14 / NOT RUN.
    """

    asset = lock_entity(db, Asset, asset_id)
    if asset is None:
        return None
    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect_name == "sqlite":
        db.execute(update(Asset).where(Asset.id == asset_id).values(version=Asset.version))
        db.flush()
        asset = db.get(Asset, asset_id)
    return asset


def _foreign_package_reference_id(
    db: Session, *, character_id: str, asset_id: str
) -> str | None:
    """Contract §10.3a query: another character's package version for the asset.

    The owning character's own package matrices never match; any OTHER
    character's version reference (DRAFT or frozen) does.
    """

    return db.scalar(
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
            CharacterModelPackageVersionReference.asset_id == asset_id,
            CharacterModelPackage.character_id != character_id,
        )
        .limit(1)
    )


def assert_asset_not_referenced_by_foreign_packages(
    db: Session, *, character_id: str, asset_id: str
) -> None:
    """Contract §10.3a gate for bind paths outside this module (issue #157).

    Raises 409 when any other character's package version references the
    asset, so one reference image keeps serving at most one character even
    when the caller creates a legacy CharacterReference directly (e.g.
    approve_asset_reference). Shares the exact query ``_check_asset_binding_
    eligible`` uses, excluding the binding character's own packages.
    """

    if _foreign_package_reference_id(db, character_id=character_id, asset_id=asset_id):
        raise HTTPException(
            status_code=409,
            detail="该素材已被其他角色的模型包版本引用，请先在对应版本中解绑",
        )


def _check_asset_binding_eligible(
    db: Session,
    *,
    project_id: str,
    character_id: str,
    asset_id: str,
    label: str = "参考素材",
) -> Asset:
    asset = lock_asset_for_ownership(db, asset_id)
    if not asset or asset.deleted_at is not None:
        raise HTTPException(status_code=404, detail=f"{label}不存在")
    if asset.project_id != project_id:
        raise HTTPException(status_code=409, detail=f"{label}和角色不属于同一项目")
    allowed_generated_kinds = {"character", "outfit"}
    if asset.kind != "CHARACTER_REFERENCE" and not (
        asset.source in {"VERTEX_GENERATED", "AI_GENERATED"}
        and asset.kind in allowed_generated_kinds
    ):
        raise HTTPException(
            status_code=409,
            detail="只有人物参考图或已生成的角色/服装设定页可以绑定角色",
        )
    # One image may serve at most one character: a package version reference
    # cannot point at an asset another character holds as its live reference,
    # nor at an asset already bound inside another character's package matrix
    # (contract §10.3a — an asset can enter a package without ever becoming a
    # legacy CharacterReference).
    existing = db.scalar(
        select(CharacterReference).where(CharacterReference.asset_id == asset.id)
    )
    if existing and existing.character_id != character_id:
        raise HTTPException(
            status_code=409,
            detail="该素材已被其他角色引用，请先在其他角色中解绑",
        )
    if _foreign_package_reference_id(db, character_id=character_id, asset_id=asset.id):
        raise HTTPException(
            status_code=409,
            detail="该素材已被其他角色的模型包版本引用，请先在对应版本中解绑",
        )
    return asset


def _validate_reference_slot(role: str, label: str) -> tuple[str, str]:
    allowed = "cover/front/side/back/three_quarter/expression/pose/extra"
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=422, detail=f"角色参考图槽位必须为 {allowed}"
        )
    label = label.strip()
    if role in CORE_ROLES:
        if label:
            raise HTTPException(status_code=422, detail="核心槽位不允许标签")
        return role, ""
    if not label:
        raise HTTPException(
            status_code=422,
            detail=f"{role} 槽位需要非空标签（如 neutral/joy/anger）",
        )
    return role, label


def bind_reference(
    db: Session,
    project_id: str,
    character_id: str,
    version_id: str,
    *,
    asset_id: str,
    role: str,
    label: str = "",
    sort_order: int = 0,
    token: int,
) -> CharacterModelPackageVersionReference:
    """§5.3-2/§9.1: bind one slot on the DRAFT under compare-and-increment."""
    package = _package(db, project_id, character_id)
    role, label = _validate_reference_slot(role, label)

    def _bind(current: CharacterModelPackage) -> CharacterModelPackageVersionReference:
        version = _owned_version(db, current, version_id)
        if version.status != VERSION_DRAFT:
            raise HTTPException(status_code=409, detail="只有草稿版本可以修改关系")
        _check_version_token(version, token)
        _check_asset_binding_eligible(
            db,
            project_id=project_id,
            character_id=character_id,
            asset_id=asset_id,
        )
        duplicate = db.scalar(
            select(CharacterModelPackageVersionReference.id).where(
                CharacterModelPackageVersionReference.version_id == version.id,
                CharacterModelPackageVersionReference.role == role,
                CharacterModelPackageVersionReference.label == label,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="该槽位已有参考图，请先解绑")
        reference = CharacterModelPackageVersionReference(
            version_id=version.id,
            asset_id=asset_id,
            role=role,
            label=label,
            sort_order=sort_order,
        )
        db.add(reference)
        version.version += 1
        db.flush()
        return reference

    return run_package_transaction(db, package.id, _bind)


def set_cover(
    db: Session,
    project_id: str,
    character_id: str,
    version_id: str,
    *,
    asset_id: str,
    token: int,
) -> CharacterModelPackageVersionReference:
    """§9.1: bind/replace the cover slot atomically (unbind old, bind new)."""
    package = _package(db, project_id, character_id)

    def _set_cover(current: CharacterModelPackage) -> CharacterModelPackageVersionReference:
        version = _owned_version(db, current, version_id)
        if version.status != VERSION_DRAFT:
            raise HTTPException(status_code=409, detail="只有草稿版本可以修改关系")
        _check_version_token(version, token)
        _check_asset_binding_eligible(
            db,
            project_id=project_id,
            character_id=character_id,
            asset_id=asset_id,
        )
        existing = db.scalar(
            select(CharacterModelPackageVersionReference).where(
                CharacterModelPackageVersionReference.version_id == version.id,
                CharacterModelPackageVersionReference.role == "cover",
                CharacterModelPackageVersionReference.label == "",
            )
        )
        if existing:
            db.delete(existing)
            db.flush()
        reference = CharacterModelPackageVersionReference(
            version_id=version.id,
            asset_id=asset_id,
            role="cover",
            label="",
            sort_order=existing.sort_order if existing else 0,
        )
        db.add(reference)
        version.version += 1
        db.flush()
        return reference

    return run_package_transaction(db, package.id, _set_cover)


def unbind_reference(
    db: Session,
    project_id: str,
    character_id: str,
    version_id: str,
    reference_id: str,
    *,
    token: int,
) -> None:
    """§10.1: physical delete of one DRAFT reference row."""
    package = _package(db, project_id, character_id)

    def _unbind(current: CharacterModelPackage) -> None:
        version = _owned_version(db, current, version_id)
        if version.status != VERSION_DRAFT:
            raise HTTPException(status_code=409, detail="只有草稿版本可以修改关系")
        _check_version_token(version, token)
        reference = db.get(CharacterModelPackageVersionReference, reference_id)
        if not reference or reference.version_id != version.id:
            raise HTTPException(status_code=404, detail="参考图绑定不存在")
        db.delete(reference)
        version.version += 1
        db.flush()

    return run_package_transaction(db, package.id, _unbind)


def bind_outfit(
    db: Session,
    project_id: str,
    character_id: str,
    version_id: str,
    *,
    outfit_id: str,
    is_default: bool = False,
    sort_order: int = 0,
    token: int,
) -> CharacterModelPackageVersionOutfit:
    """§9.1: bind one outfit to the DRAFT under compare-and-increment."""
    package = _package(db, project_id, character_id)
    character = _character(db, project_id, character_id)

    def _bind(current: CharacterModelPackage) -> CharacterModelPackageVersionOutfit:
        version = _owned_version(db, current, version_id)
        if version.status != VERSION_DRAFT:
            raise HTTPException(status_code=409, detail="只有草稿版本可以修改关系")
        _check_version_token(version, token)
        outfit = db.get(Outfit, outfit_id)
        if not outfit or outfit.character_id != character.id or outfit.project_id != project_id:
            raise HTTPException(status_code=409, detail="所选服装不属于当前人物")
        duplicate = db.scalar(
            select(CharacterModelPackageVersionOutfit.id).where(
                CharacterModelPackageVersionOutfit.version_id == version.id,
                CharacterModelPackageVersionOutfit.outfit_id == outfit_id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="该服装已绑定到本版本")
        if is_default:
            existing_default = db.scalar(
                select(CharacterModelPackageVersionOutfit.id).where(
                    CharacterModelPackageVersionOutfit.version_id == version.id,
                    CharacterModelPackageVersionOutfit.is_default.is_(True),
                )
            )
            if existing_default:
                raise HTTPException(
                    status_code=409,
                    detail="该版本已存在默认服装，请先解绑或通过设置默认切换",
                )
        relation = CharacterModelPackageVersionOutfit(
            version_id=version.id,
            outfit_id=outfit_id,
            is_default=is_default,
            sort_order=sort_order,
        )
        db.add(relation)
        version.version += 1
        db.flush()
        return relation

    return run_package_transaction(db, package.id, _bind)


def set_default_outfit(
    db: Session,
    project_id: str,
    character_id: str,
    version_id: str,
    outfit_id: str,
    *,
    is_default: bool,
    token: int,
) -> CharacterModelPackageVersionOutfit:
    """§9.1: atomic swap of the default outfit (clear old, set new, one txn)."""
    package = _package(db, project_id, character_id)

    def _set_default(current: CharacterModelPackage) -> CharacterModelPackageVersionOutfit:
        version = _owned_version(db, current, version_id)
        if version.status != VERSION_DRAFT:
            raise HTTPException(status_code=409, detail="只有草稿版本可以修改关系")
        _check_version_token(version, token)
        if not is_default:
            raise HTTPException(status_code=422, detail="取消默认请解绑该服装")
        relation = db.scalar(
            select(CharacterModelPackageVersionOutfit).where(
                CharacterModelPackageVersionOutfit.version_id == version.id,
                CharacterModelPackageVersionOutfit.outfit_id == outfit_id,
            )
        )
        if not relation:
            raise HTTPException(status_code=404, detail="服装绑定不存在")
        db.execute(
            update(CharacterModelPackageVersionOutfit)
            .where(
                CharacterModelPackageVersionOutfit.version_id == version.id,
                CharacterModelPackageVersionOutfit.id != relation.id,
            )
            .values(is_default=False)
            .execution_options(synchronize_session=False)
        )
        relation.is_default = True
        version.version += 1
        db.flush()
        return relation

    return run_package_transaction(db, package.id, _set_default)


def unbind_outfit(
    db: Session,
    project_id: str,
    character_id: str,
    version_id: str,
    outfit_id: str,
    *,
    token: int,
) -> None:
    package = _package(db, project_id, character_id)

    def _unbind(current: CharacterModelPackage) -> None:
        version = _owned_version(db, current, version_id)
        if version.status != VERSION_DRAFT:
            raise HTTPException(status_code=409, detail="只有草稿版本可以修改关系")
        _check_version_token(version, token)
        relation = db.scalar(
            select(CharacterModelPackageVersionOutfit).where(
                CharacterModelPackageVersionOutfit.version_id == version.id,
                CharacterModelPackageVersionOutfit.outfit_id == outfit_id,
            )
        )
        if not relation:
            raise HTTPException(status_code=404, detail="服装绑定不存在")
        db.delete(relation)
        version.version += 1
        db.flush()

    return run_package_transaction(db, package.id, _unbind)


def _spec_facts(
    db: Session, package: CharacterModelPackage, version: CharacterModelPackageVersion
) -> dict:
    """§7.2: READY+ reads the frozen snapshot; DRAFT reads the workspec."""
    if version.status == VERSION_DRAFT:
        return {
            "identity_spec": dict(package.identity_spec or {}),
            "visual_spec": dict(package.visual_spec or {}),
            "negative_constraints": list(package.negative_constraints or []),
            "frozen_from": "package",
        }
    return dict(version.spec_snapshot or {})


def _live_asset_ids(db: Session, asset_ids: list[str]) -> set[str]:
    if not asset_ids:
        return set()
    return set(
        db.scalars(
            select(Asset.id).where(Asset.id.in_(asset_ids), Asset.deleted_at.is_(None))
        )
    )


def completeness(
    db: Session,
    package: CharacterModelPackage,
    version: CharacterModelPackageVersion,
) -> dict:
    """§7.3: deterministic 20/40/20/20 score plus explainable missing items."""
    facts = _spec_facts(db, package, version)
    score = 0
    missing: list[dict] = []

    identity = dict(facts.get("identity_spec") or {})
    identity_labels = {
        "age_appearance": "年龄外观",
        "gender": "性别",
        "personality": "性格",
        "identity_notes": "身份备注",
    }
    for key in IDENTITY_KEYS:
        if str(identity.get(key) or "").strip():
            score += IDENTITY_POINTS_PER_KEY
        else:
            missing.append(
                {
                    "code": "MISSING_IDENTITY",
                    "field": key,
                    "message": f"身份规格缺少{identity_labels[key]}",
                    "suggestion": "在包规格中补充该信息后重新发布版本",
                }
            )

    references = list(
        db.scalars(
            select(CharacterModelPackageVersionReference).where(
                CharacterModelPackageVersionReference.version_id == version.id
            )
        )
    )
    alive_asset_ids = _live_asset_ids(db, [item.asset_id for item in references])
    slot_by_role: dict[str, CharacterModelPackageVersionReference] = {}
    expression_labels: list[str] = []
    for reference in references:
        if reference.role in CORE_ROLES and reference.role in slot_by_role:
            continue
        if reference.role in CORE_ROLES:
            slot_by_role[reference.role] = reference
        elif reference.role == "expression" and reference.label not in expression_labels:
            expression_labels.append(reference.label)
    view_labels = {
        "front": "正面",
        "side": "侧面",
        "back": "背面",
        "three_quarter": "四分之三侧",
    }
    for role, points in VIEW_POINTS.items():
        reference = slot_by_role.get(role)
        if reference and reference.asset_id in alive_asset_ids:
            score += points
        else:
            missing.append(
                {
                    "code": "MISSING_VIEW",
                    "field": role,
                    "message": f"缺少{view_labels[role]}参考",
                    "suggestion": "上传或生成对应角度图后重新发布版本",
                }
            )

    scored_expressions = 0
    for label in expression_labels:
        if scored_expressions >= EXPRESSION_CAP:
            break
        if any(
            reference.label == label and reference.asset_id in alive_asset_ids
            for reference in references
            if reference.role == "expression"
        ):
            score += EXPRESSION_POINTS
            scored_expressions += 1
    missing_expression_count = max(0, EXPRESSION_CAP - scored_expressions)
    for index in range(missing_expression_count):
        missing.append(
            {
                "code": "MISSING_EXPRESSION",
                "field": f"expression[{index}]",
                "message": "核心表情集未满",
                "suggestion": "添加 neutral/joy/anger/sorrow 表情槽位",
            }
        )

    outfit_relations = list(
        db.scalars(
            select(CharacterModelPackageVersionOutfit).where(
                CharacterModelPackageVersionOutfit.version_id == version.id
            )
        )
    )
    has_bound_outfit = False
    for relation in outfit_relations:
        outfit = db.get(Outfit, relation.outfit_id)
        if outfit and _live_asset_ids(db, list(outfit.reference_asset_ids or [])):
            has_bound_outfit = True
            break
    if has_bound_outfit:
        score += OUTFIT_BOUND_POINTS
    else:
        missing.append(
            {
                "code": "MISSING_OUTFIT",
                "field": "outfit_id",
                "message": "尚未绑定任何有参考图的服装",
                "suggestion": "为角色绑定服装并补足服装参考图",
            }
        )
    has_default = any(relation.is_default for relation in outfit_relations)
    if has_default:
        score += OUTFIT_DEFAULT_POINTS
    else:
        missing.append(
            {
                "code": "MISSING_DEFAULT_OUTFIT",
                "field": "is_default",
                "message": "尚未设置默认服装",
                "suggestion": "绑定服装后将其设为默认",
            }
        )

    return {"score": score, "missing": missing}


def version_diff(
    db: Session,
    project_id: str,
    character_id: str,
    base_version_id: str,
    target_version_id: str,
) -> dict:
    """§9.1: slot-wise diff between two versions of the same package."""
    package = _package(db, project_id, character_id)
    base = _owned_version(db, package, base_version_id)
    target = _owned_version(db, package, target_version_id)
    base_facts = _spec_facts(db, package, base)
    target_facts = _spec_facts(db, package, target)

    def _block_diff(base_block: dict, target_block: dict) -> dict:
        added: dict[str, str] = {}
        removed: dict[str, str] = {}
        changed: list[dict] = []
        for key in dict.fromkeys([*base_block.keys(), *target_block.keys()]):
            base_value = str(base_block.get(key) or "")
            target_value = str(target_block.get(key) or "")
            if base_value and not target_value:
                removed[key] = base_block[key]
            elif target_value and not base_value:
                added[key] = target_block[key]
            elif target_value and base_value != target_value:
                changed.append(
                    {
                        "field": key,
                        "base_value": base_block.get(key),
                        "target_value": target_block.get(key),
                    }
                )
        return {"added": added, "removed": removed, "changed": changed}

    base_constraints = list(base_facts.get("negative_constraints") or [])
    target_constraints = list(target_facts.get("negative_constraints") or [])
    base_references = list(
        db.scalars(
            select(CharacterModelPackageVersionReference)
            .where(CharacterModelPackageVersionReference.version_id == base.id)
            .order_by(
                CharacterModelPackageVersionReference.role,
                CharacterModelPackageVersionReference.label,
            )
        )
    )
    target_references = list(
        db.scalars(
            select(CharacterModelPackageVersionReference)
            .where(CharacterModelPackageVersionReference.version_id == target.id)
            .order_by(
                CharacterModelPackageVersionReference.role,
                CharacterModelPackageVersionReference.label,
            )
        )
    )
    reference_base = {(item.role, item.label): item for item in base_references}
    reference_target = {(item.role, item.label): item for item in target_references}
    asset_deleted: dict[str, bool] = {}
    for asset_id in {
        item.asset_id
        for item in [*base_references, *target_references]
        if item.asset_id
    }:
        asset_row = db.get(Asset, asset_id)
        asset_deleted[asset_id] = asset_row.deleted_at is not None if asset_row else True
    added_slots: list[dict] = []
    removed_slots: list[dict] = []
    changed_slots: list[dict] = []
    for key, item in reference_target.items():
        if key not in reference_base:
            added_slots.append(
                {
                    "role": item.role,
                    "label": item.label,
                    "asset_id": item.asset_id,
                    "asset_deleted": asset_deleted.get(item.asset_id, True),
                }
            )
    for key, item in reference_base.items():
        if key not in reference_target:
            removed_slots.append(
                {
                    "role": item.role,
                    "label": item.label,
                    "asset_id": item.asset_id,
                    "asset_deleted": asset_deleted.get(item.asset_id, True),
                }
            )
    for key in reference_base:
        target_item = reference_target.get(key)
        if target_item and target_item.asset_id != reference_base[key].asset_id:
            changed_slots.append(
                {
                    "role": key[0],
                    "label": key[1],
                    "base_asset_id": reference_base[key].asset_id,
                    "target_asset_id": target_item.asset_id,
                    "base_asset_deleted": asset_deleted.get(reference_base[key].asset_id, True),
                    "target_asset_deleted": asset_deleted.get(target_item.asset_id, True),
                }
            )

    base_outfits = list(
        db.scalars(
            select(CharacterModelPackageVersionOutfit)
            .where(CharacterModelPackageVersionOutfit.version_id == base.id)
            .order_by(CharacterModelPackageVersionOutfit.outfit_id)
        )
    )
    target_outfits = list(
        db.scalars(
            select(CharacterModelPackageVersionOutfit)
            .where(CharacterModelPackageVersionOutfit.version_id == target.id)
            .order_by(CharacterModelPackageVersionOutfit.outfit_id)
        )
    )
    outfit_base = {item.outfit_id: item for item in base_outfits}
    outfit_target = {item.outfit_id: item for item in target_outfits}

    def _outfit_slot(item: CharacterModelPackageVersionOutfit) -> dict:
        return {
            "outfit_id": item.outfit_id,
            "is_default": item.is_default,
            "sort_order": item.sort_order,
        }

    added_outfits = [
        _outfit_slot(item)
        for outfit_id, item in outfit_target.items()
        if outfit_id not in outfit_base
    ]
    removed_outfits = [
        _outfit_slot(item)
        for outfit_id, item in outfit_base.items()
        if outfit_id not in outfit_target
    ]
    changed_outfits = [
        _outfit_slot(item)
        for outfit_id, item in outfit_target.items()
        if outfit_id in outfit_base
        and _outfit_slot(outfit_base[outfit_id]) != _outfit_slot(item)
    ]

    return {
        "base_version_id": base.id,
        "target_version_id": target.id,
        "identity_spec": _block_diff(
            base_facts.get("identity_spec") or {},
            target_facts.get("identity_spec") or {},
        ),
        "visual_spec": _block_diff(
            base_facts.get("visual_spec") or {},
            target_facts.get("visual_spec") or {},
        ),
        "negative_constraints": {
            "added": [item for item in target_constraints if item not in base_constraints],
            "removed": [item for item in base_constraints if item not in target_constraints],
        },
        "references": {
            "added": added_slots,
            "removed": removed_slots,
            "changed": changed_slots,
        },
        "outfits": {
            "added": added_outfits,
            "removed": removed_outfits,
            "changed": changed_outfits,
        },
    }


# --- generation chain (contract §8) -----------------------------------------


@dataclass
class PackageResolutionBatch:
    """Result of resolving package versions for one page candidate.

    ``normalized`` keeps the exact ``reference_selections`` shape so the lease
    assembly and worker loading stay unchanged; ``snapshot`` is the frozen
    ``prompt_snapshot["character_packages"]`` block; ``gate`` feeds the
    MISSING_OUTFIT_ASSIGNMENT alternative path; ``productions`` lists
    ``(version_id, from_default_path)`` pairs for IN_PRODUCTION marking.
    """

    normalized: dict[str, dict]
    snapshot: dict[str, dict]
    gate: dict[str, bool]
    productions: list[tuple[str, bool]]


def _version_reference_rows(
    db: Session, version_id: str
) -> list[CharacterModelPackageVersionReference]:
    return list(
        db.scalars(
            select(CharacterModelPackageVersionReference).where(
                CharacterModelPackageVersionReference.version_id == version_id
            )
        )
    )


def _default_version_for_character(
    db: Session, character_id: str
) -> tuple[CharacterModelPackage, CharacterModelPackageVersion] | None:
    package = db.scalar(
        select(CharacterModelPackage).where(
            CharacterModelPackage.character_id == character_id,
            CharacterModelPackage.status == PACKAGE_ACTIVE,
            CharacterModelPackage.published_version_id.is_not(None),
        )
    )
    if not package or not package.published_version_id:
        return None
    version = db.get(CharacterModelPackageVersion, package.published_version_id)
    if not version or version.status not in {VERSION_READY, VERSION_IN_PRODUCTION}:
        return None
    return package, version


def _page_style_id(project: Project, style_id: str | None) -> str | None:
    return style_id or project.default_style_id or None


def _panel_outfit_map(panels: list[Panel], character_id: str) -> dict[str, str]:
    assigned = {
        panel.outfits.get(character_id) for panel in panels if panel.outfits.get(character_id)
    }
    if len(assigned) > 1:
        raise HTTPException(status_code=409, detail="同一页同一角色存在多套服装，请先拆页")
    return {character_id: next(iter(assigned), None)} if assigned else {}


def _mark_in_production(db: Session, version_id: str) -> bool:
    changed = db.execute(
        update(CharacterModelPackageVersion)
        .where(
            CharacterModelPackageVersion.id == version_id,
            CharacterModelPackageVersion.status == VERSION_READY,
        )
        .values(status=VERSION_IN_PRODUCTION)
        .execution_options(synchronize_session=False)
    )
    return changed.rowcount == 1


def _accept_production_version(
    db: Session, version_id: str, *, from_default: bool
) -> bool:
    """READY→IN_PRODUCTION, or accept an already-usable published version.

    Contract §5.2/§5.3-5: the conditional update is idempotent. A later
    default-inherited candidate must keep using an unchanged IN_PRODUCTION
    published version instead of treating zero updated rows as a conflict.
    Explicit selections also accept ARCHIVED (and a still-READY row whose
    update lost a race).
    """

    if _mark_in_production(db, version_id):
        return True
    version = db.get(CharacterModelPackageVersion, version_id)
    if version is None:
        return False
    if version.status == VERSION_IN_PRODUCTION:
        return True
    return not from_default and version.status in {VERSION_READY, VERSION_ARCHIVED}


def _resolve_one_character(
    db: Session,
    *,
    project: Project,
    character: Character,
    visible_panel_outfits: dict[str, str],
    selection: dict,
    style_id: str | None = None,
) -> tuple[PackageResolution, dict, bool] | None:
    """Resolve one character's package version and choices (contract §8.1).

    Returns ``(resolution, normalized_selection, from_default_path)`` or None
    when the character stays on the legacy path.
    """

    explicit_version_id = selection.get("package_version_id")
    if explicit_version_id:
        version = db.get(CharacterModelPackageVersion, explicit_version_id)
        if not version:
            raise HTTPException(status_code=404, detail="模型包版本不存在")
        package = db.get(CharacterModelPackage, version.package_id)
        if package.character_id != character.id:
            raise HTTPException(status_code=409, detail="所选包版本不属于当前出镜人物")
        if package.project_id != project.id:
            raise HTTPException(status_code=409, detail="所选包版本不属于当前项目")
        # Issue #145-C: the explicit path must revalidate the PACKAGE status
        # the same way the default path does, so a package archived before (or
        # concurrently with) the request cannot keep generating. Version-status
        # ARCHIVED stays explicitly selectable (contract §5.2); this gate is
        # about the owning package only.
        if package.status != PACKAGE_ACTIVE:
            raise HTTPException(
                status_code=409,
                detail="所选角色模型包已归档，请先恢复包或重新选择版本",
            )
        if version.status == VERSION_DRAFT:
            raise HTTPException(status_code=422, detail="草稿版本不能用于生成")
        from_default = False
    else:
        default_resolution = _default_version_for_character(db, character.id)
        if not default_resolution:
            return None
        package, version = default_resolution
        from_default = True

    facts = _spec_facts(db, package, version)
    references = _version_reference_rows(db, version.id)
    alive_asset_ids = _live_asset_ids(db, [item.asset_id for item in references])
    reference = None
    character_asset_id = selection.get("character_asset_id")
    if character_asset_id:
        for item in references:
            if item.asset_id == character_asset_id and item.asset_id in alive_asset_ids:
                reference = item
                break
        if not reference:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"请为画面人物 {character.primary_name} "
                    "选择一本版本内有效的人物参考图"
                ),
            )
    else:
        ordered = sorted(
            (item for item in references if item.asset_id in alive_asset_ids),
            key=lambda item: (
                0 if item.role == "front" else 1 if item.role == "cover" else 2,
                item.sort_order,
                item.created_at,
                item.id,
            ),
        )
        if not ordered:
            raise HTTPException(
                status_code=409,
                detail=f"请为画面人物 {character.primary_name} 选择一张人物参考图",
            )
        reference = ordered[0]
    default_relation = db.scalar(
        select(CharacterModelPackageVersionOutfit).where(
            CharacterModelPackageVersionOutfit.version_id == version.id,
            CharacterModelPackageVersionOutfit.is_default.is_(True),
        )
    )
    assigned_outfit_id = visible_panel_outfits.get(character.id)
    # Contract §8.1 outfit chain: explicit selection > panel assignment >
    # the version's default outfit. Resolving the default here keeps the
    # normalized selection consistent with the readiness gate.
    outfit_id = selection.get("outfit_id") or assigned_outfit_id
    if assigned_outfit_id and outfit_id != assigned_outfit_id:
        raise HTTPException(status_code=409, detail="参考确认中的服装与分镜指定服装不一致")
    if not outfit_id and default_relation:
        outfit_id = default_relation.outfit_id
    outfit_asset_id = None
    if outfit_id:
        relation = db.scalar(
            select(CharacterModelPackageVersionOutfit).where(
                CharacterModelPackageVersionOutfit.version_id == version.id,
                CharacterModelPackageVersionOutfit.outfit_id == outfit_id,
            )
        )
        if not relation:
            raise HTTPException(status_code=409, detail="所选服装不属于当前模型包版本")
        outfit = db.get(Outfit, outfit_id)
        if not outfit or outfit.character_id != character.id or outfit.project_id != project.id:
            raise HTTPException(status_code=409, detail="所选服装不属于当前人物")
        live_outfit_ids = _live_asset_ids(db, list(outfit.reference_asset_ids or []))
        outfit_asset_id = selection.get("outfit_asset_id")
        if not outfit_asset_id:
            # Workflow GENERATE and default inheritance omit the asset id;
            # pick the first live reference on the resolved outfit, matching
            # the character_asset_id default chain.
            outfit_asset_id = next(
                (
                    item
                    for item in (outfit.reference_asset_ids or [])
                    if item in live_outfit_ids
                ),
                None,
            )
        elif outfit_asset_id not in (outfit.reference_asset_ids or []):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"请为画面人物 {character.primary_name} "
                    "的服装选择一张已绑定参考图"
                ),
            )
        outfit_asset = db.get(Asset, outfit_asset_id) if outfit_asset_id else None
        if not outfit_asset or outfit_asset.deleted_at is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"请为画面人物 {character.primary_name} "
                    "的服装选择一张已绑定参考图"
                ),
            )

    default_outfit_has_live_reference = False
    if default_relation:
        default_outfit = db.get(Outfit, default_relation.outfit_id)
        if default_outfit and _live_asset_ids(db, list(default_outfit.reference_asset_ids or [])):
            default_outfit_has_live_reference = True

    frozen_style_id = _page_style_id(project, style_id)
    style = db.get(StyleProfile, frozen_style_id) if frozen_style_id else None
    resolution = PackageResolution(
        package=package,
        version=version,
        mode="explicit" if not from_default else "published",
        character_id=character.id,
        character_name=character.primary_name,
        character_asset_id=reference.asset_id,
        reference_role=reference.role,
        reference_label=reference.label,
        outfit_id=outfit_id,
        outfit_asset_id=outfit_asset_id,
        default_outfit_has_live_reference=default_outfit_has_live_reference,
    )
    snapshot = {
        "package_id": package.id,
        "package_version_id": version.id,
        "version_number": version.version_number,
        "spec_fingerprint": spec_fingerprint(facts),
        "primary_name": character.primary_name,
        "aliases": list(character.aliases or []),
        "identity_spec": facts.get("identity_spec") or {},
        "visual_spec": facts.get("visual_spec") or {},
        "negative_constraints": facts.get("negative_constraints") or [],
        "character_asset_id": reference.asset_id,
        "reference_role": reference.role,
        "reference_label": reference.label,
        "outfit_id": outfit_id,
        "outfit_asset_id": outfit_asset_id,
        "style_profile_id": frozen_style_id,
        "style_profile_version": style.version if style else None,
    }
    return resolution, snapshot, from_default


def resolve_package_selections(
    db: Session,
    *,
    project: Project,
    page: MangaPage,
    selections: dict,
    style_id: str | None = None,
) -> PackageResolutionBatch:
    """Contract §8.1/§8.2: resolve every visible character's package version.

    Also performs the same-transaction IN_PRODUCTION conditional update
    (contract §5.2) and re-resolves the default path once when a concurrent
    archive/activate made the first resolution unusable.
    """

    panels = list(db.scalars(select(Panel).where(Panel.page_id == page.id)))
    visible_character_ids = list(
        dict.fromkeys(character_id for panel in panels for character_id in panel.characters)
    )
    batch = PackageResolutionBatch(
        normalized={},
        snapshot={},
        gate={},
        productions=[],
    )
    for character_id in visible_character_ids:
        resolved = _resolve_one_character(
            db,
            project=project,
            character=db.get(Character, character_id),
            visible_panel_outfits=_panel_outfit_map(panels, character_id),
            selection=selections.get(character_id, {}),
            style_id=style_id,
        )
        if not resolved:
            continue
        resolution, snapshot, from_default = resolved
        version_id = resolution.version.id
        eligible = True
        if from_default:
            # Contract §5.3-5: default resolution serializes with archive and
            # activate through the package row lock, and revalidates the
            # package status and the publish pointer before the version is
            # marked IN_PRODUCTION.
            lock_entity(db, CharacterModelPackage, resolution.package.id)
            db.expire_all()
            package_now = db.get(CharacterModelPackage, resolution.package.id)
            if (
                not package_now
                or package_now.status != PACKAGE_ACTIVE
                or package_now.published_version_id != version_id
            ):
                eligible = False
        accepted = (
            _accept_production_version(db, version_id, from_default=from_default)
            if eligible
            else False
        )
        if not accepted and not from_default:
            raise HTTPException(
                status_code=409,
                detail="角色模型包版本已被并发归档或切换，请刷新后重试",
            )
        if not accepted:
            # Default path: re-resolve once; a simultaneously archived or
            # re-published package must not be silently used. An unchanged
            # IN_PRODUCTION published version is accepted above and never
            # reaches this retry.
            retry = _resolve_one_character(
                db,
                project=project,
                character=db.get(Character, character_id),
                visible_panel_outfits=_panel_outfit_map(panels, character_id),
                selection=selections.get(character_id, {}),
                style_id=style_id,
            )
            if not retry:
                raise HTTPException(
                    status_code=409,
                    detail="角色模型包版本已被并发归档或切换，请刷新后重试",
                )
            resolution, snapshot, from_default = retry
            lock_entity(db, CharacterModelPackage, resolution.package.id)
            db.expire_all()
            package_now = db.get(CharacterModelPackage, resolution.package.id)
            if (
                not package_now
                or package_now.status != PACKAGE_ACTIVE
                or package_now.published_version_id != resolution.version.id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="角色模型包版本已被并发归档或切换，请刷新后重试",
                )
            if not _accept_production_version(
                db, resolution.version.id, from_default=True
            ):
                raise HTTPException(
                    status_code=409,
                    detail="角色模型包版本已被并发归档或切换，请刷新后重试",
                )
        batch.normalized[character_id] = {
            "character_asset_id": resolution.character_asset_id,
            "outfit_id": resolution.outfit_id,
            "outfit_asset_id": resolution.outfit_asset_id,
        }
        batch.snapshot[character_id] = snapshot
        batch.gate[character_id] = resolution.default_outfit_has_live_reference
        batch.productions.append((resolution.version.id, from_default))
    return batch


def default_package_gate_context(db: Session, page: MangaPage) -> dict[str, bool]:
    """start_batch gate context: ACTIVE package + published-version default outfit."""
    panels = list(db.scalars(select(Panel).where(Panel.page_id == page.id)))
    visible_character_ids = list(
        dict.fromkeys(character_id for panel in panels for character_id in panel.characters)
    )
    context: dict[str, bool] = {}
    for character_id in visible_character_ids:
        default_resolution = _default_version_for_character(db, character_id)
        if not default_resolution:
            continue
        _package, version = default_resolution
        default_relation = db.scalar(
            select(CharacterModelPackageVersionOutfit).where(
                CharacterModelPackageVersionOutfit.version_id == version.id,
                CharacterModelPackageVersionOutfit.is_default.is_(True),
            )
        )
        if not default_relation:
            continue
        outfit = db.get(Outfit, default_relation.outfit_id)
        if outfit and _live_asset_ids(db, list(outfit.reference_asset_ids or [])):
            context[character_id] = True
    return context


def detach_draft_package_references_for_asset(db: Session, asset_id: str) -> None:
    """Contract §10.3: soft-deleting an asset physically clears DRAFT slot rows.

    READY+ relation rows keep the frozen fact; consumers filter by
    ``Asset.deleted_at`` at read time. Must be the first writer in the
    caller's unit so lock contention can roll back and retry.

    Lock order follows ``run_package_transaction`` (issue #145-B): the parent
    package rows first, then the bound Asset row, then version/relation rows.
    A pre-read discovers which packages own DRAFT references; the mutation
    re-reads under both locks so a concurrent bind/unbind cannot interleave
    into a lost version-token increment.
    """

    def _detach() -> None:
        package_ids = sorted(
            set(
                db.scalars(
                    select(CharacterModelPackageVersion.package_id)
                    .join(
                        CharacterModelPackageVersionReference,
                        CharacterModelPackageVersionReference.version_id
                        == CharacterModelPackageVersion.id,
                    )
                    .where(
                        CharacterModelPackageVersionReference.asset_id == asset_id,
                        CharacterModelPackageVersion.status == VERSION_DRAFT,
                    )
                )
            )
        )
        for package_id in package_ids:
            lock_entity(db, CharacterModelPackage, package_id)
        lock_asset_for_ownership(db, asset_id)
        db.expire_all()
        for reference in db.scalars(
            select(CharacterModelPackageVersionReference).where(
                CharacterModelPackageVersionReference.asset_id == asset_id
            )
        ):
            version = db.get(CharacterModelPackageVersion, reference.version_id)
            if not version:
                continue
            if version.status == VERSION_DRAFT:
                db.delete(reference)
                # Editing a draft requires the parent token; the system must not
                # silently mutate drafts without bumping it.
                version.version += 1

    run_lock_retry(
        db,
        _detach,
        conflict_detail="素材绑定清理冲突，请稍后重试",
    )
