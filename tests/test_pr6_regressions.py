import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from redis import Redis
from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import worker_tasks
from app.api.routes import sources, uploads, workflow
from app.config import Settings, get_settings
from app.domain.states import JobStatus
from app.models import Chapter, GenerationJob, InspectionResult, Project, utcnow
from app.schemas import KeepSelectedCandidateRequest
from app.services import job_service
from app.services.ai_schemas import PageInspectionOutput
from app.services.page_completion import build_page_production_readiness
from app.services.worker_handlers import provider
from app.services.worker_handlers.inspection import _run_inspection
from test_quality_gates import _pass_all, _ready_page


@pytest.mark.parametrize("attempt_count", [0, 2])
def test_slot_deferral_uses_real_rq_validation_and_remaining_retries(
    db_session, monkeypatch, attempt_count
):
    project = Project(name="RQ slot regression")
    db_session.add(project)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="CHAPTER",
        target_id="offline-target",
        job_type="SOURCE_PARSE",
        status=JobStatus.WAITING,
        error_code="CONCURRENCY_LIMIT",
        attempt_count=attempt_count,
        max_attempts=3,
    )
    db_session.add(job)
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
    connection = Redis(host="127.0.0.1", port=1)
    current = SimpleNamespace(connection=connection, origin="review-queue")
    monkeypatch.setattr("rq.get_current_job", lambda: current)
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: connection)
    monkeypatch.setattr(connection, "ping", lambda: True)
    monkeypatch.setattr(
        job_service,
        "_submit_local",
        lambda job_id: pytest.fail("RQ must not use local fallback"),
    )
    scheduled = []

    def save_scheduled(queue, rq_job, when, pipeline=None):
        scheduled.append((queue, rq_job, when))
        return rq_job

    # Keep real enqueue_in -> enqueue_at -> create_job validation, mock only Redis persistence.
    monkeypatch.setattr(Queue, "schedule_job", save_scheduled)
    worker_tasks._defer_concurrency_wait(job.id)
    assert len(scheduled) == 1
    queue, deferred, when = scheduled[0]
    assert queue.name == current.origin
    assert queue.connection is connection
    assert ":" not in deferred.id
    assert deferred.id != job.id
    assert deferred.args == (job.id,)
    assert deferred.timeout == get_settings().job_timeout_seconds
    assert (deferred.retries_left or 0) == 2 - attempt_count
    assert when > utcnow()
    db_session.refresh(job)
    assert job.status == JobStatus.WAITING
    assert job.attempt_count == attempt_count
    connection.close()


def test_local_slot_wait_does_not_publish_to_redis(db_session, monkeypatch):
    monkeypatch.setattr("rq.get_current_job", lambda: None)
    monkeypatch.setattr(
        "redis.Redis.from_url",
        lambda *args, **kwargs: pytest.fail("LOCAL must stay local"),
    )
    monkeypatch.setattr(
        worker_tasks,
        "SessionLocal",
        lambda: pytest.fail("Local retry loop owns the wait"),
    )
    worker_tasks._defer_concurrency_wait("local-job")


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_worker_entrypoint_loads_dotenv_auth_and_queue(tmp_path, monkeypatch, platform):
    from app import worker

    env_file = tmp_path / "worker.env"
    env_file.write_text(
        "REDIS_URL=redis://:offline-test@127.0.0.1:16379/4\nQUEUE_NAME=offline-queue\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("QUEUE_NAME", raising=False)
    settings = Settings(_env_file=env_file)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker.sys, "platform", platform)
    calls = []

    def cli_main(*, args, prog_name):
        from rq.cli.helpers import CliConfig

        kwargs = CliConfig().connection.connection_pool.connection_kwargs
        calls.append((args, kwargs))

    monkeypatch.setattr(worker.rq_cli, "main", cli_main)
    # Track the environment assignment so monkeypatch restores it after the test.
    monkeypatch.setenv("REDIS_URL", "")
    worker.main()
    args, kwargs = calls[0]
    assert "--with-scheduler" in args
    assert args[-1] == "offline-queue"
    assert kwargs["password"] == "offline-test"
    assert kwargs["port"] == 16379
    assert kwargs["db"] == 4
    # rq's SpawnWorker horse crashes on Windows; the repository ships a
    # Windows-safe variant used on win32 only.
    assert ("app.rq_windows.WindowsSpawnWorker" in args) == (platform == "win32")


def _inspect(db, monkeypatch, page, candidate, categories, *, during_call=None):
    job = GenerationJob(
        project_id=db.get(Chapter, page.chapter_id).project_id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.PREPARING,
        request_parameters={"categories": categories},
        lease_owner="offline-owner",
        lease_expires_at=utcnow() + timedelta(minutes=5),
    )
    db.add(job)
    db.commit()
    db.info.update(job_id=job.id, job_lease_owner=job.lease_owner)
    output = PageInspectionOutput.model_validate(
        {
            "items": [
                {
                    "category": category,
                    "outcome": "PASS",
                    "details": {"expected": "offline", "observed": "offline"},
                }
                for category in categories
            ]
        }
    )

    def analyze(*args):
        if during_call:
            during_call()
        return output

    binding = SimpleNamespace(
        resolved=SimpleNamespace(model=SimpleNamespace(id=None)),
        adapter=SimpleNamespace(analyze_multimodal=analyze),
        selected_key=None,
    )
    monkeypatch.setattr(
        "app.services.worker_handlers.inspection.compile_page_prompt",
        lambda *args: ("", {"input": {}}),
    )
    monkeypatch.setattr(provider, "_binding", lambda *args, **kwargs: binding)
    monkeypatch.setattr(
        provider,
        "_asset_path",
        lambda asset: SimpleNamespace(read_bytes=lambda: b"offline"),
    )
    _run_inspection(db, job)


def test_new_storyboard_partial_check_cannot_reuse_old_categories(
    db_session, monkeypatch
):
    page, candidate = _ready_page(db_session)
    _pass_all(db_session, candidate.id)
    page.storyboard_version += 1
    db_session.commit()
    workflow.generation.keep_selected_candidate(
        page.id,
        KeepSelectedCandidateRequest(
            candidate_id=candidate.id,
            storyboard_version=page.storyboard_version,
            manual_text_confirmed=True,
        ),
        db_session,
    )
    _inspect(db_session, monkeypatch, page, candidate, ["CONTINUITY"])
    assert not build_page_production_readiness(db_session, page).ready
    assert candidate.status != "INSPECTED"
    assert page.continuity_status != "PASSED"
    _inspect(
        db_session,
        monkeypatch,
        page,
        candidate,
        ["SPEAKER", "CHARACTER", "OUTFIT", "PROP"],
    )
    assert build_page_production_readiness(db_session, page).ready


def test_legacy_unversioned_checks_require_reinspection(db_session):
    page, candidate = _ready_page(db_session)
    _pass_all(db_session, candidate.id)
    for row in db_session.scalars(select(InspectionResult)):
        row.storyboard_version = None
    db_session.commit()
    assert not build_page_production_readiness(db_session, page).ready


def test_storyboard_changed_during_inspection_does_not_get_passed(
    db_session, monkeypatch
):
    page, candidate = _ready_page(
        db_session, candidate_status="READY", continuity="NEEDS_REVIEW"
    )

    def change_storyboard():
        page.storyboard_version += 1
        db_session.commit()

    _inspect(
        db_session,
        monkeypatch,
        page,
        candidate,
        ["SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"],
        during_call=change_storyboard,
    )
    assert page.continuity_status != "PASSED"
    assert {
        row.storyboard_version for row in db_session.scalars(select(InspectionResult))
    } == {1}


@pytest.mark.parametrize("kind", ["asset", "source"])
def test_upload_processing_runs_off_event_loop(client, monkeypatch, tmp_path, kind):
    from test_upload_limits import _png_bytes

    project = client.post("/api/v1/projects", json={"name": "threadpool upload"}).json()
    monkeypatch.setattr(get_settings(), "upload_root", tmp_path)
    calls = []
    module, name = (
        (uploads, "create_thumbnails")
        if kind == "asset"
        else (sources, "import_source")
    )
    original = getattr(module, name)

    def guarded(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AssertionError(
                "Synchronous upload processing must run off the event loop"
            )
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, guarded)
    if kind == "asset":
        response = client.post(
            "/api/v1/assets/upload",
            data={"project_id": project["id"], "kind": "character"},
            files={"file": ("small.png", _png_bytes(), "image/png")},
        )
    else:
        response = client.post(
            f"/api/v1/projects/{project['id']}/sources/upload",
            files={
                "file": ("source.txt", "第一章\n一个测试故事。".encode(), "text/plain")
            },
        )
    assert response.status_code == 201, response.text
    assert calls == [True]
