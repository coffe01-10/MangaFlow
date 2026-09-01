"""Local fake production-gate projects for isolated browser/performance runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
    ModelCallAttempt,
    ModelPricingVersion,
    PageCandidate,
    Panel,
    Project,
    ProviderUsageReconciliation,
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


def seed_usage_ledger(session: Session, project: Project) -> None:
    """Offline usage-ledger fixtures covering every dashboard cost semantic."""
    now = datetime.now(UTC)
    session.add_all(
        [
            ModelPricingVersion(
                provider="e2e-gate-provider",
                model_id="e2e-gate-image",
                pricing_version="e2e-2026.09-v1",
                currency="CNY",
                effective_from=now - timedelta(days=60),
                request_each=Decimal("0.01"),
                output_image_each=Decimal("0.12"),
            ),
            ModelPricingVersion(
                provider="e2e-gate-provider",
                model_id="e2e-gate-text",
                pricing_version="e2e-2026.09-v1",
                currency="USD",
                effective_from=now - timedelta(days=60),
                input_tokens_per_million=Decimal("500"),
                output_tokens_per_million=Decimal("1500"),
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            ModelCallAttempt(
                project_id=project.id,
                job_attempt=1,
                dispatch_no=1,
                outcome="SUCCEEDED",
                channel="HTTP_API",
                provider="e2e-gate-provider",
                model_id="e2e-gate-image",
                request_id="req_e2e_image_1",
                started_at=now - timedelta(days=2, hours=3),
                finished_at=now - timedelta(days=2, hours=3) + timedelta(seconds=4),
                duration_ms=4120,
                usage={"output_images": 1},
                usage_status="COMPLETE",
                usage_source="ADAPTER_ESTIMATED",
                unit_kind="IMAGES",
                output_images=1,
            ),
            ModelCallAttempt(
                project_id=project.id,
                job_attempt=1,
                dispatch_no=2,
                route_switched=True,
                outcome="FAILED",
                channel="HTTP_API",
                provider="e2e-gate-provider",
                model_id="e2e-gate-image",
                request_id="req_e2e_image_0",
                started_at=now - timedelta(days=2, hours=3, minutes=1),
                duration_ms=1800,
                error_code="PROVIDER_RATE_LIMIT",
                error_message="429 rate limited (redacted)",
                usage=None,
            ),
            ModelCallAttempt(
                project_id=project.id,
                job_attempt=1,
                dispatch_no=1,
                outcome="SUCCEEDED",
                channel="HTTP_API",
                provider="e2e-gate-provider",
                model_id="e2e-gate-text",
                request_id="req_e2e_text_1",
                started_at=now - timedelta(days=1, hours=5),
                duration_ms=2200,
                usage={
                    "prompt_tokens": 1200,
                    "completion_tokens": 480,
                    "prompt_tokens_details": {"cached_tokens": 300},
                },
                usage_status="COMPLETE",
                usage_source="PROVIDER_REPORTED",
                unit_kind="TEXT_TOKENS",
                input_tokens=1200,
                output_tokens=480,
                cached_input_tokens=300,
                cache_hit=True,
            ),
            ModelCallAttempt(
                project_id=project.id,
                job_attempt=1,
                dispatch_no=1,
                outcome="SUCCEEDED",
                channel="CLI",
                provider="e2e-gate-cli",
                model_id="e2e-gate-cli-codex",
                started_at=now - timedelta(days=1, hours=1),
                duration_ms=56000,
                usage={"input_tokens": 900, "output_tokens": 150},
                usage_status="COMPLETE",
                usage_source="PROVIDER_REPORTED",
                unit_kind="TEXT_TOKENS",
                input_tokens=900,
                output_tokens=150,
            ),
            ModelCallAttempt(
                project_id=project.id,
                job_attempt=1,
                dispatch_no=1,
                outcome="SUCCEEDED",
                channel="HTTP_API",
                provider="e2e-gate-provider",
                model_id="e2e-gate-image",
                started_at=now - timedelta(hours=5),
                duration_ms=3800,
                usage=None,
            ),
            ModelCallAttempt(
                project_id=project.id,
                job_attempt=1,
                dispatch_no=1,
                outcome=None,
                channel="HTTP_API",
                provider="e2e-gate-provider",
                model_id="e2e-gate-text",
                started_at=now - timedelta(minutes=30),
            ),
        ]
    )
    session.add(
        ProviderUsageReconciliation(
            provider="e2e-gate-provider",
            model_id="e2e-gate-image",
            channel="HTTP_API",
            billing_account_id="e2e-billing-account",
            import_batch_id="e2e-batch-1",
            idempotency_key="e2e-line-1",
            period_start=now - timedelta(days=29),
            period_end=now - timedelta(days=1),
            currency="CNY",
            billed_amount=Decimal("66.00"),
            source_note="e2e 离线对账样例，非真实账单",
            entered_by="e2e-operator",
        )
    )


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
        seed_usage_ledger(session, ready)
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
