"""Regression: inspection worker normalization and oldest-wins backstop.

Four behaviors pinned:
- a lowercase ``categories`` request is canonicalized (previously the
  lowercase request persisted lowercase verdict rows that never satisfied
  the uppercase completion gate — an unbounded paid re-inspect loop);
- model verdicts are case-normalized before classification and persistence;
- a younger PAGE_INSPECT job dies before the paid call when an older ACTIVE
  inspect exists on the same candidate (worker-side oldest-wins arbitration,
  mirroring style_analyze/story_parse);
- the arbitration-side intent filters from #242's audit are covered in
  tests/test_derived_retry_mutex.py additions.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.domain.states import JobStatus
from app.model_adapters.base import ProviderAdapterError
from app.models import (
    AIModel,
    Asset,
    Chapter,
    GenerationBatch,
    GenerationJob,
    InspectionResult,
    MangaPage,
    PageCandidate,
    Project,
)
from app.services.ai_schemas import InspectionItem, PageInspectionOutput
from app.services.provider_presets import ensure_provider_presets
from app.services.worker_handlers.inspection import _run_inspection

CATEGORIES = ["SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"]


@pytest.fixture
def inspect_catalog(db_session):
    ensure_provider_presets(db_session, get_settings(), auto_commit=False)
    db_session.commit()
    model = db_session.scalar(
        select(AIModel).where(
            AIModel.model_type == "TEXT",
        )
    )
    assert model is not None, "预设目录必须包含文字模型"
    model.enabled = True
    model.confidence = "VERIFIED"
    from app.models import ProviderConnection

    connection = db_session.get(ProviderConnection, model.connection_id)
    connection.enabled = True
    connection.health_state = "HEALTHY"
    db_session.commit()
    return model


@pytest.fixture
def inspect_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    return tmp_path


def _ready_candidate(db, name: str) -> tuple[Project, PageCandidate]:
    project = Project(name=name)
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db.add(chapter)
    db.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=1,
        source_coverage={"complete": True},
        scene_ids=["s1"],
        beat_ids=["b1"],
    )
    db.add(page)
    db.flush()
    batch = GenerationBatch(
        project_id=project.id, chapter_id=chapter.id, page_id=page.id, ordinal=1
    )
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="ready.png",
        storage_key="generated/ready.png",
        mime_type="image/png",
        byte_size=10,
        sha256=name.encode().hex().ljust(64, "0")[:64],
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db.add_all([batch, asset])
    db.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution="DRAFT_1K",
        status="READY",
        asset_id=asset.id,
        based_on_storyboard_version=page.storyboard_version,
    )
    db.add(candidate)
    db.commit()
    return project, candidate



def _materialize_asset_file(storage, candidate) -> None:
    path = storage / "generated" / "ready.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")

def _lease(db, job) -> None:
    job.attempt_count = max(job.attempt_count or 0, 1)
    job.lease_owner = "owner-inspect"
    job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    db.info["job_id"] = job.id
    db.info["job_lease_owner"] = "owner-inspect"
    db.commit()


def _verdict(category: str, outcome: str) -> InspectionItem:
    return InspectionItem(
        category=category,
        outcome=outcome,
        score=0.97,
        severity="INFO",
        details={
            "expected": "x",
            "observed": "x",
            "differences": [],
            "detected_characters": [],
        },
        regions=[],
    )


def test_lowercased_request_converges_and_persists_uppercase(
    db_session, inspect_catalog, inspect_storage, monkeypatch
):
    project, candidate = _ready_candidate(db_session, "小写类别")
    _materialize_asset_file(inspect_storage, candidate)
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
        request_parameters={"categories": [c.lower() for c in CATEGORIES]},
    )
    db_session.add(job)
    db_session.commit()
    _lease(db_session, job)

    class FakeAdapter:
        def analyze_multimodal(self, request, output_schema):
            # The model echoes the lowercase casing it was given.
            return PageInspectionOutput(
                items=[_verdict(c.lower(), "pass") for c in CATEGORIES]
                + [_verdict("presence", "pass")]
            )

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeAdapter())

    _run_inspection(db_session, job)
    db_session.commit()
    db_session.expire_all()

    rows = list(
        db_session.scalars(
            select(InspectionResult).where(
                InspectionResult.candidate_id == candidate.id
            )
        )
    )
    stored = {row.category for row in rows}
    assert stored == set(CATEGORIES) | {"PRESENCE"}
    assert all(row.category == row.category.upper() for row in rows)
    assert all(row.outcome == "PASS" for row in rows)
    refreshed = db_session.get(PageCandidate, candidate.id)
    assert refreshed.status == "INSPECTED"


def test_younger_inspect_dies_before_paid_call(
    db_session, inspect_catalog, inspect_storage, monkeypatch
):
    project, candidate = _ready_candidate(db_session, "质检仲裁兜底")
    _materialize_asset_file(inspect_storage, candidate)
    older = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
        request_parameters={"categories": CATEGORIES},
    )
    younger = GenerationJob(
        project_id=project.id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
        request_parameters={"categories": CATEGORIES},
    )
    db_session.add_all([older, younger])
    db_session.commit()
    _lease(db_session, younger)

    class ForbiddenAdapter:
        def analyze_multimodal(self, request, output_schema):
            raise AssertionError("输家任务不得进入付费调用")

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: ForbiddenAdapter())

    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_inspection(db_session, younger)
    assert excinfo.value.code == "PAGE_INSPECT_CONFLICT"
    assert excinfo.value.retryable is False
    db_session.expire_all()
    assert (
        db_session.get(GenerationJob, older.id).status
        == JobStatus.CONSISTENCY_CHECKING
    )
