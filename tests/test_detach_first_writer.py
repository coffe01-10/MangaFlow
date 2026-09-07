"""detach-must-be-first-writer regressions for the deletion teardown units.

``run_lock_retry`` (character_packages) rolls the whole session back before
retrying a SQLITE_BUSY attempt, so any write a route performs BEFORE calling
``detach_draft_package_references_for_asset(s)`` is silently discarded and
never re-applied. The tests force exactly one such rollback and verify the
route's writes still land after the retry:

- ``delete_outfit``: the teardown detaches several assets through ONE
  retryable unit; a SQLITE_BUSY on the SECOND asset's ownership lock must
  roll back the WHOLE unit and the retry must clear BOTH assets' DRAFT
  package references (a per-asset detach loop used to discard the first
  asset's uncommitted clears and commit a partial teardown: assets gone,
  DRAFT slots still occupied);
- ``delete_candidate`` (workflow): the conditional claim UPDATE used to run
  before the detach, so a detach lock-retry discarded the claim and committed
  a partial teardown (asset gone, candidate alive).
"""

from sqlalchemy import select

from app.domain.states import Resolution
from app.models import (
    Asset,
    AssetCandidate,
    CharacterModelPackage,
    CharacterModelPackageVersion,
    CharacterModelPackageVersionReference,
    CharacterReference,
    GenerationBatch,
)
from app.services.character_packages import detach_draft_package_references_for_asset


def _project(client, name: str) -> dict:
    response = client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _character(client, project_id: str, name: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/characters",
        json={"primary_name": name, "aliases": []},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _orm_asset(db, project_id: str, *, kind: str, sha256: str) -> Asset:
    asset = Asset(
        project_id=project_id,
        kind=kind,
        original_name=f"{kind}.png",
        storage_key=f"detach-first/{kind}-{sha256[:6]}.png",
        mime_type="image/png",
        byte_size=64,
        sha256=sha256,
        source="AI_GENERATED",
        status="GENERATED",
    )
    db.add(asset)
    db.flush()
    return asset


def _orm_batch(db, project_id: str, *, target_type: str, target_id: str) -> GenerationBatch:
    batch = GenerationBatch(
        project_id=project_id,
        target_type=target_type,
        target_id=target_id,
        generation_kind=target_type,
        ordinal=1,
    )
    db.add(batch)
    db.flush()
    return batch


def _orm_candidate(
    db, batch_id: str, asset_id: str, *, variant: str, ordinal: int = 1
) -> AssetCandidate:
    candidate = AssetCandidate(
        batch_id=batch_id,
        ordinal=ordinal,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant=variant,
        status="READY",
    )
    db.add(candidate)
    db.flush()
    candidate.asset_id = asset_id
    db.commit()
    return candidate


def _orm_draft_package_with_references(
    db, project_id: str, character_id: str, bindings: list[tuple[str, str]]
) -> CharacterModelPackageVersion:
    package = CharacterModelPackage(project_id=project_id, character_id=character_id)
    db.add(package)
    db.flush()
    version = CharacterModelPackageVersion(
        package_id=package.id, version_number=1, status="DRAFT"
    )
    db.add(version)
    db.flush()
    for asset_id, role in bindings:
        db.add(
            CharacterModelPackageVersionReference(
                version_id=version.id, asset_id=asset_id, role=role, label=""
            )
        )
    db.commit()
    return version


def _orm_draft_package_with_reference(
    db, project_id: str, character_id: str, asset_id: str
) -> CharacterModelPackageVersion:
    return _orm_draft_package_with_references(
        db, project_id, character_id, [(asset_id, "front")]
    )


def _detach_with_one_busy_rollback(monkeypatch, route_module) -> dict:
    """Patch the route's detach so its first call emulates a SQLITE_BUSY first
    attempt: run_lock_retry rolls the whole session back before retrying, so
    writes the route performed before detach are silently discarded."""

    fired = {"rolled_back": False}

    def detach_with_busy_first_attempt(db, asset_id):
        if not fired["rolled_back"]:
            fired["rolled_back"] = True
            db.rollback()
        return detach_draft_package_references_for_asset(db, asset_id)

    monkeypatch.setattr(
        route_module, "detach_draft_package_references_for_asset", detach_with_busy_first_attempt
    )
    return fired


def test_delete_outfit_multi_asset_detach_atomic_across_lock_retry(
    client, db_session, monkeypatch
):
    """A SQLITE_BUSY on the SECOND asset's ownership lock rolls back the
    WHOLE multi-asset detach unit; the retry clears BOTH assets' DRAFT
    references, and the teardown commits fully (tombstones and soft-deletes
    land, no orphaned DRAFT slots)."""
    import sqlite3

    import app.services.character_packages as character_packages_service
    from sqlalchemy.exc import OperationalError

    monkeypatch.setattr(
        "app.services.character_packages.pause_before_ordinal_retry",
        lambda *_args: None,
    )
    project = _project(client, "服装多素材清理")
    character = _character(client, project["id"], "林澈")
    outfit = client.post(
        f"/api/v1/projects/{project['id']}/outfits",
        json={
            "character_id": character["id"],
            "name": "常服",
            "components": {"top": "衬衫"},
            "reference_asset_ids": [],
        },
    )
    assert outfit.status_code == 201, outfit.text
    outfit_id = outfit.json()["id"]
    generated_first = _orm_asset(
        db_session, project["id"], kind="outfit", sha256="a" * 64
    )
    generated_second = _orm_asset(
        db_session, project["id"], kind="outfit", sha256="b" * 64
    )
    batch = _orm_batch(
        db_session, project["id"], target_type="OUTFIT", target_id=outfit_id
    )
    candidate_first = _orm_candidate(
        db_session, batch.id, generated_first.id, variant="OUTFIT_SHEET"
    )
    candidate_second = _orm_candidate(
        db_session, batch.id, generated_second.id, variant="OUTFIT_SHEET", ordinal=2
    )
    draft_version = _orm_draft_package_with_references(
        db_session,
        project["id"],
        character["id"],
        [(generated_first.id, "front"), (generated_second.id, "side")],
    )
    draft_version_before = draft_version.version
    candidate_versions_before = (candidate_first.version, candidate_second.version)

    real_lock = character_packages_service.lock_asset_for_ownership
    lock_state = {"seen_first": False, "busy_fired": False}

    def lock_busy_on_second_asset(db, asset_id):
        # Emulate SQLITE_BUSY when the retryable unit reaches the SECOND
        # distinct asset's ownership lock: the first attempt must roll back
        # everything (including asset #1's clears), and the retried attempt
        # must redo the whole sorted loop.
        if not lock_state["busy_fired"]:
            if not lock_state["seen_first"]:
                lock_state["seen_first"] = True
            else:
                lock_state["busy_fired"] = True
                raise OperationalError(
                    "UPDATE assets", None, sqlite3.OperationalError("database is locked")
                )
        return real_lock(db, asset_id)

    monkeypatch.setattr(
        character_packages_service, "lock_asset_for_ownership", lock_busy_on_second_asset
    )

    deleted = client.delete(f"/api/v1/outfits/{outfit_id}")
    assert deleted.status_code == 204, deleted.text
    assert lock_state["busy_fired"] is True

    db_session.expire_all()
    # BOTH assets' DRAFT references are cleared: the rollback discarded the
    # whole first attempt and the retry re-detached the first asset too.
    for asset in (generated_first, generated_second):
        assert db_session.get(Asset, asset.id).deleted_at is not None
        assert (
            db_session.scalar(
                select(CharacterModelPackageVersionReference.id).where(
                    CharacterModelPackageVersionReference.asset_id == asset.id
                )
            )
            is None
        )
    for candidate, token_before in zip(
        (candidate_first, candidate_second), candidate_versions_before
    ):
        candidate_row = db_session.get(AssetCandidate, candidate.id)
        assert candidate_row.deleted_at is not None
        assert candidate_row.version == token_before + 1
    # Each detached slot bumps the parent DRAFT token exactly once: with the
    # old per-asset detach loop the rollback ate the first bump and the
    # first asset's slot row survived the commit.
    db_session.expire_all()
    assert (
        db_session.get(CharacterModelPackageVersion, draft_version.id).version
        == draft_version_before + 2
    )


def test_delete_candidate_claim_survives_detach_lock_retry(client, db_session, monkeypatch):
    """The claim UPDATE cannot be lost to a detach lock-retry: asset gone must
    never pair with a live candidate."""
    import app.api.routes.workflow.generation as workflow_generation_routes

    project = _project(client, "候选首写者")
    character = _character(client, project["id"], "陈昊")
    asset = _orm_asset(db_session, project["id"], kind="character", sha256="1" * 64)
    batch = _orm_batch(
        db_session, project["id"], target_type="CHARACTER", target_id=character["id"]
    )
    candidate = _orm_candidate(db_session, batch.id, asset.id, variant="SHEET")
    db_session.add(
        CharacterReference(
            character_id=character["id"], asset_id=asset.id, angle="complete_sheet"
        )
    )
    draft_version = _orm_draft_package_with_reference(
        db_session, project["id"], character["id"], asset.id
    )
    db_session.commit()
    candidate_version_before = candidate.version
    draft_version_before = draft_version.version

    fired = _detach_with_one_busy_rollback(monkeypatch, workflow_generation_routes)
    deleted = client.delete(f"/api/v1/candidates/{candidate.id}")
    assert deleted.status_code == 204, deleted.text
    assert fired["rolled_back"] is True

    db_session.expire_all()
    candidate_row = db_session.get(AssetCandidate, candidate.id)
    assert candidate_row.deleted_at is not None
    assert candidate_row.version == candidate_version_before + 1
    assert db_session.get(Asset, asset.id).deleted_at is not None
    assert (
        db_session.scalar(
            select(CharacterReference.id).where(CharacterReference.asset_id == asset.id)
        )
        is None
    )
    assert (
        db_session.scalar(
            select(CharacterModelPackageVersionReference.id).where(
                CharacterModelPackageVersionReference.asset_id == asset.id
            )
        )
        is None
    )
    db_session.expire_all()
    assert db_session.get(
        CharacterModelPackageVersion, draft_version.id
    ).version == draft_version_before + 1
