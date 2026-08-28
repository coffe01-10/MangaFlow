"""Local fake production-gate projects for isolated browser/performance runs."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.states import Resolution
from app.models import (
    Asset,
    Chapter,
    GenerationBatch,
    InspectionResult,
    MangaPage,
    PageCandidate,
    Panel,
    Project,
)

MISSING_NAME = "e2e-gate-missing"
FAILED_NAME = "e2e-gate-failed"
STALE_NAME = "e2e-gate-stale"
READY_NAME = "e2e-gate-ready"
LIGHTHOUSE_NAME = "e2e-lighthouse-workbench"

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a4944415478da63000100000500010d0a2db40000000049454e44ae426082"
)

_CATEGORIES = ("SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY")


def _asset(session: Session, project: Project, storage_root: Path, digest: str) -> Asset:
    key = f"{digest}.png"
    (storage_root / key).write_bytes(PNG_1X1)
    asset = Asset(
        project_id=project.id,
        kind="PAGE_CANDIDATE",
        original_name=key,
        storage_key=key,
        mime_type="image/png",
        byte_size=len(PNG_1X1),
        sha256=digest,
        width=1,
        height=1,
        source="GENERATED",
        status="GENERATED",
    )
    session.add(asset)
    session.flush()
    from app.services.media import create_thumbnails

    thumbs = create_thumbnails(storage_root / key, storage_root, asset.id)
    asset.thumbnail_320_key = thumbs[320]
    asset.thumbnail_640_key = thumbs[640]
    session.flush()
    return asset


def _page_with_candidate(
    session: Session,
    *,
    project: Project,
    title: str,
    storage_root: Path,
    digest: str,
    selected: bool,
    ack_version: int | None,
    storyboard_version: int,
    candidate_status: str,
    continuity: str,
    inspections: dict[str, str] | None,
) -> MangaPage:
    chapter = Chapter(project_id=project.id, title=title, ordinal=1)
    session.add(chapter)
    session.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=storyboard_version,
        selected_candidate_ack_version=ack_version,
        continuity_status=continuity,
        source_coverage={"complete": True},
    )
    session.add(page)
    session.flush()
    session.add(
        Panel(
            page_id=page.id,
            reading_order=1,
            background="巷口灯还亮着",
        )
    )
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=1,
        generation_kind="PAGE",
        status="OPEN",
    )
    session.add(batch)
    session.flush()
    asset = _asset(session, project, storage_root, digest)
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status=candidate_status,
        asset_id=asset.id,
        based_on_storyboard_version=ack_version or 1,
        is_selected=selected,
    )
    session.add(candidate)
    session.flush()
    if selected:
        page.selected_candidate_id = candidate.id
    for category, outcome in (inspections or {}).items():
        session.add(
            InspectionResult(
                candidate_id=candidate.id,
                storyboard_version=storyboard_version if ack_version == storyboard_version else storyboard_version,
                category=category,
                outcome=outcome,
                score=1.0 if outcome in {"PASS", "MATCH", "ACCEPTABLE"} else 0.1,
            )
        )
    return page


def seed_gate_projects(database_url: str, storage_root: Path) -> dict[str, str]:
    engine = create_engine(database_url)
    ids: dict[str, str] = {}
    with Session(engine) as session:
        missing = Project(name=MISSING_NAME)
        failed = Project(name=FAILED_NAME)
        stale = Project(name=STALE_NAME)
        ready = Project(name=READY_NAME)
        lighthouse = Project(name=LIGHTHOUSE_NAME)
        session.add_all([missing, failed, stale, ready, lighthouse])
        session.flush()

        _page_with_candidate(
            session,
            project=missing,
            title="缺项",
            storage_root=storage_root,
            digest="a" * 64,
            selected=True,
            ack_version=1,
            storyboard_version=1,
            candidate_status="COMPLETED",
            continuity="NOT_CHECKED",
            inspections=None,
        )
        _page_with_candidate(
            session,
            project=failed,
            title="失败",
            storage_root=storage_root,
            digest="b" * 64,
            selected=True,
            ack_version=1,
            storyboard_version=1,
            candidate_status="NEEDS_REVIEW",
            continuity="NEEDS_REVIEW",
            inspections={category: "FAIL" for category in _CATEGORIES},
        )
        _page_with_candidate(
            session,
            project=stale,
            title="过期",
            storage_root=storage_root,
            digest="c" * 64,
            selected=True,
            ack_version=1,
            storyboard_version=2,
            candidate_status="INSPECTED",
            continuity="PASSED",
            inspections={category: "PASS" for category in _CATEGORIES},
        )
        _page_with_candidate(
            session,
            project=ready,
            title="全通过",
            storage_root=storage_root,
            digest="d" * 64,
            selected=True,
            ack_version=1,
            storyboard_version=1,
            candidate_status="INSPECTED",
            continuity="PASSED",
            inspections={category: "PASS" for category in _CATEGORIES},
        )
        _page_with_candidate(
            session,
            project=lighthouse,
            title="工作台",
            storage_root=storage_root,
            digest="e" * 64,
            selected=True,
            ack_version=1,
            storyboard_version=1,
            candidate_status="INSPECTED",
            continuity="PASSED",
            inspections={category: "PASS" for category in _CATEGORIES},
        )
        session.commit()
        ids = {
            "missing": missing.id,
            "failed": failed.id,
            "stale": stale.id,
            "ready": ready.id,
            "lighthouse": lighthouse.id,
        }
    engine.dispose()
    return ids
