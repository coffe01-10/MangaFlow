"""Child-owned acceptance configuration and local image fixture.

Import this module BEFORE app.database/worker_tasks in a fresh process. The
offline probe is explicitly not RQ/PG acceptance. The RQ horse entry is prepared
for the supervised worker; no public live runner is enabled by this module.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from scripts.acceptance_safety import (
    validate_safe_acceptance_pg_url,
    validate_safe_acceptance_redis_url,
)
from tests.integration.process_resources import _validate_directory

ROOT = Path(__file__).resolve().parents[2]
_ID = re.compile(r"[a-zA-Z0-9_-]{1,100}")


def _safe_path(root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path.absolute():
        raise ValueError("Worker path must be absolute and must not traverse a link")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Worker path is outside the owned payload")
    return path


def write_worker_config(
    tree,
    scenarios: dict[str, str],
    *,
    pg_url: str | None = None,
    schema: str | None = None,
    redis_url: str | None = None,
    redis_token: str | None = None,
    queue_name: str | None = None,
    lease_seconds: int | None = None,
) -> Path:
    _validate_directory(tree.directory, tree.token)
    record = {
        "version": 1,
        "process_token": tree.token,
        "mode": "offline-probe",
        "scenarios": scenarios,
    }
    if any(value is not None for value in (pg_url, schema, redis_url, redis_token, queue_name)):
        record.update(
            mode="live-rq",
            pg_url=pg_url,
            schema=schema,
            redis_url=redis_url,
            redis_token=redis_token,
            queue_name=queue_name,
        )
    if lease_seconds is not None:
        record["lease_seconds"] = lease_seconds
    path = tree.payload / "worker.json"
    _validate_config(tree.directory, record)
    with path.open("x", encoding="utf-8") as file:
        json.dump(record, file)
    return path


def _validate_config(directory: Path, record: dict) -> None:
    if record.get("version") != 1:
        raise ValueError("Unsupported worker configuration")
    _validate_directory(directory, record["process_token"])
    scenarios = record.get("scenarios")
    if not isinstance(scenarios, dict) or not scenarios:
        raise ValueError("Worker fixture needs explicitly registered application jobs")
    for job_id, scenario in scenarios.items():
        if not _ID.fullmatch(job_id) or scenario not in {"ok", "retry_once", "terminal", "block"}:
            raise ValueError("Invalid local worker scenario")
    if record["mode"] == "offline-probe":
        if any(
            key in record for key in ("pg_url", "schema", "redis_url", "redis_token", "queue_name")
        ):
            raise ValueError("Offline probe must not include live endpoints")
    elif record["mode"] == "live-rq":
        validate_safe_acceptance_pg_url(record["pg_url"] or "")
        validate_safe_acceptance_redis_url(record["redis_url"] or "")
        if not re.fullmatch(r"acceptance_[0-9a-f]{32}", record["schema"] or ""):
            raise ValueError("Invalid owned PostgreSQL schema")
        token = record["redis_token"] or ""
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise ValueError("Invalid Redis ownership token")
        if not re.fullmatch(rf"acceptance_{token}_[a-zA-Z0-9_-]+", record["queue_name"] or ""):
            raise ValueError("Queue does not belong to the configured Redis owner")
        lease = record.get("lease_seconds")
        if lease is not None and (type(lease) is not int or not 1 <= lease <= 600):
            raise ValueError("Lease seconds must be an integer between 1 and 600")
    else:
        raise ValueError("Unsupported worker mode")


class LocalImageFixture:
    """No network adapter. Persist events across separate horse/retry processes."""

    def __init__(self, directory: Path, scenarios: dict[str, str], probe_job: str | None = None):
        self.directory, self.scenarios, self.probe_job = directory, scenarios, probe_job

    def _job_id(self) -> str:
        if self.probe_job is not None:
            job_id = self.probe_job
        else:
            from rq import get_current_job

            current = get_current_job()
            if current is None or len(current.args) != 1:
                raise RuntimeError("Local fixture requires an actual RQ application job")
            job_id = current.args[0]
        if job_id not in self.scenarios:
            raise RuntimeError("Local fixture refuses an unregistered job")
        return job_id

    def _event(self, job_id: str, event: str) -> str:
        event_id = uuid4().hex
        record = {"job_id": job_id, "event": event, "pid": os.getpid(), "id": event_id}
        path = self.directory / f"event-{event_id}.json"
        pending = path.with_suffix(".pending")
        with pending.open("x", encoding="utf-8") as file:
            json.dump(record, file)
            file.flush()
            os.fsync(file.fileno())
        pending.replace(path)
        return event_id

    def _image(self, request, operation):
        from app.model_adapters.base import ProviderAdapterError
        from app.model_adapters.fake_acceptance import FakeAcceptanceImageAdapter

        job_id = self._job_id()
        self._event(job_id, "entered")
        mode = self.scenarios[job_id]
        if mode == "terminal":
            self._event(job_id, "terminal")
            raise ProviderAdapterError("INVALID_PROMPT", "Local acceptance terminal failure")
        if mode == "retry_once":
            try:
                with (self.directory / f"retry-{job_id}").open("x"):
                    pass
            except FileExistsError:
                pass
            else:
                self._event(job_id, "retryable")
                raise ProviderAdapterError(
                    "RATE_LIMIT",
                    "Local acceptance retryable failure",
                    retryable=True,
                    retry_after_seconds=1,
                )
        if mode == "block":
            deadline = time.monotonic() + 15
            release = self.directory / f"release-{job_id}"
            while not release.exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Local fixture release timed out")
                time.sleep(0.02)
        response = getattr(FakeAcceptanceImageAdapter(), operation)(request)
        return replace(response, request_id="local-" + self._event(job_id, "returned"))

    def generate_page(self, request):
        return self._image(request, "generate_page")

    def generate_asset(self, request):
        return self._image(request, "generate_asset")

    def edit_region(self, request):
        return self._image(request, "edit_region")

    def capabilities(self):
        from app.services.model_capabilities import whole_image_reference_edit_capabilities

        return {
            "resolutions": ["1K", "2K", "4K"],
            "aspect_ratios": ["3:4", "16:9", "1:1"],
            # V02-44B honesty: local fixture edits are whole-image reference
            # calls with no mask surface.
            **whole_image_reference_edit_capabilities(),
        }

    def generate_structured(self, *args, **kwargs):
        raise RuntimeError("Text operations are not implemented by this local image fixture")

    def analyze_multimodal(self, request, output_schema):
        """Deterministic five-category inspection so PAGE_INSPECT can run locally."""
        from app.services.ai_schemas import InspectionDetails, InspectionItem, PageInspectionOutput

        job_id = self._job_id()
        self._event(job_id, "inspected")
        if output_schema is not PageInspectionOutput:
            raise RuntimeError("Local fixture only supports page inspection output")
        return output_schema.model_validate(
            {
                "items": [
                    InspectionItem(
                        category=category,
                        outcome="PASS",
                        score=1.0,
                        severity="INFO",
                        details=InspectionDetails(
                            expected="local acceptance baseline",
                            observed="local acceptance baseline",
                            differences=[],
                        ),
                        regions=[],
                    )
                    for category in ("SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY")
                ]
            }
        )


def configure_child(path: Path, *, probe_job: str | None = None):
    if "app.database" in sys.modules or "app.worker_tasks" in sys.modules:
        raise RuntimeError("Worker isolation must be configured before application imports")
    path = path.absolute()
    directory = path.parent.parent
    payload = directory / "payload"
    _validate_directory(directory, directory.name.removeprefix("mangaflow-process-"))
    _safe_path(payload, str(path))
    record = json.loads(path.read_text(encoding="utf-8"))
    _validate_config(directory, record)
    if _safe_path(payload, str(path)) != payload / "worker.json":
        raise ValueError("Unexpected worker configuration path")
    if probe_job is not None and (
        record["mode"] != "offline-probe" or probe_job not in record["scenarios"]
    ):
        raise ValueError("Direct execution is restricted to the explicit offline probe")

    from app import config

    class IsolatedSettings(config.Settings):
        @classmethod
        def settings_customise_sources(cls, settings_cls, init_settings, **kwargs):
            return (init_settings,)  # No environment, dotenv or secrets-directory source.

    db_path = _safe_path(payload, str(payload / "probe.sqlite"))
    settings_kwargs: dict = {}
    if record["mode"] == "live-rq" and "lease_seconds" in record:
        settings_kwargs["job_lease_seconds"] = record["lease_seconds"]
    settings = IsolatedSettings(
        _env_file=None,
        environment="development",
        database_url=(record["pg_url"] if record["mode"] == "live-rq" else f"sqlite:///{db_path}"),
        storage_root=_safe_path(payload, str(payload / "storage")),
        upload_root=_safe_path(payload, str(payload / "uploads")),
        queue_enabled=record["mode"] == "live-rq",
        queue_name=record.get("queue_name", "offline-probe"),
        redis_url=record.get("redis_url", "redis://127.0.0.1:56379/15"),
        google_cloud_project=None,
        google_application_credentials=None,
        mangaflow_credential_master_key=None,
        mangaflow_proxy_url=None,
        **settings_kwargs,
    )
    config.get_settings = lambda: settings
    from app import database
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = database.engine
    if record["mode"] == "live-rq":
        engine.dispose()
        schema = record["schema"]
        engine = create_engine(
            settings.database_url,
            connect_args={
                "options": (
                    f"-csearch_path={schema},pg_catalog "
                    "-clock_timeout=5000 -cstatement_timeout=30000"
                )
            },
        )
        try:
            with engine.connect() as connection:
                owner = connection.scalar(
                    text(
                        "SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace "
                        "WHERE nspname = :schema"
                    ),
                    {"schema": schema},
                )
                if owner != "mangaflow-acceptance:" + schema.removeprefix("acceptance_"):
                    raise RuntimeError("PostgreSQL ownership changed before worker startup")
                if connection.scalar(text("SELECT current_schema()")) != schema:
                    raise RuntimeError("Worker PostgreSQL search_path was not applied")
        except BaseException:
            engine.dispose()
            raise
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    events = _safe_path(payload, str(payload / "events"))
    events.mkdir(exist_ok=True)
    settings.ensure_directories()
    from app import worker_tasks

    adapter = LocalImageFixture(events, record["scenarios"], probe_job)
    worker_tasks._adapter = lambda _alias: adapter
    return record, settings, engine, adapter


def run_offline_application_probe(path: Path, job_id: str) -> None:
    """Actual application task in a child, explicitly no Redis/RQ scheduling."""
    import socket

    def offline_network_forbidden(*args, **kwargs):
        raise RuntimeError("Network is forbidden in the offline application probe")

    socket.socket.connect = offline_network_forbidden
    socket.socket.connect_ex = offline_network_forbidden
    socket.create_connection = offline_network_forbidden
    _record, _settings, engine, _adapter = configure_child(path, probe_job=job_id)
    try:
        from app.worker_tasks import execute_job

        execute_job(job_id)
    finally:
        engine.dispose()


def run_rq_horse(path: Path, worker_name: str, rq_job_id: str, execution_id: str) -> None:
    """Actual RQ perform_job in its own process; parent supervision still required."""
    record, _settings, engine, _adapter = configure_child(path)
    if record["mode"] != "live-rq":
        engine.dispose()
        raise RuntimeError("RQ horse requires the explicit owned live configuration")
    from redis import Redis
    from rq import Queue, Worker
    from rq.executions import Execution
    from rq.job import Job

    client = Redis.from_url(record["redis_url"], socket_connect_timeout=1, socket_timeout=1)
    try:
        token, queue_name = record["redis_token"], record["queue_name"]
        if client.get(f"mangaflow:acceptance:{token}:owner") != token.encode():
            raise RuntimeError("Redis ownership changed before horse startup")
        if not re.fullmatch(rf"acceptance_{token}_[a-zA-Z0-9_-]+", worker_name):
            raise ValueError("Unowned worker name")
        if not _ID.fullmatch(rq_job_id) or not _ID.fullmatch(execution_id):
            raise ValueError("Invalid RQ execution identity")
        if client.hget(f"rq:job:{rq_job_id}", "origin") != queue_name.encode():
            raise RuntimeError("RQ job belongs to another queue")
        worker = Worker.find_by_key(f"rq:worker:{worker_name}", connection=client)
        if worker is None or worker.queue_names() != [queue_name]:
            raise RuntimeError("RQ worker is missing or belongs to another queue")
        job = Job.fetch(rq_job_id, connection=client)
        if (
            job.func_name != "app.worker_tasks.execute_job"
            or len(job.args) != 1
            or job.args[0] not in record["scenarios"]
        ):
            raise RuntimeError("RQ horse refuses an unregistered application task")
        application_job_id = job.args[0]
        if job.kwargs or not (
            job.id == application_job_id
            or re.fullmatch(rf"{re.escape(application_job_id)}-slot-[a-f0-9]{{32}}", job.id)
        ):
            raise RuntimeError("RQ ID is not the registered job or its slot continuation")
        worker.execution = Execution.fetch(execution_id, job.id, connection=client)
        worker._is_horse = True
        worker.setup_work_horse_signals()
        # perform_job uses RQ's job stack, heartbeat, result, failure and retry handling.
        worker.perform_job(job, Queue(queue_name, connection=client))
    finally:
        client.close()
        engine.dispose()


ACCEPTANCE_HORSE_DRIVER = """
import sys

root, config_path, worker_name, rq_job_id, execution_id = sys.argv[1:6]
sys.path.insert(0, root)
sys.path.insert(0, root + "/apps/api")
from tests.integration.worker_runtime import run_rq_horse

run_rq_horse(config_path, worker_name, rq_job_id, execution_id)
"""


def _acceptance_worker_base():
    # Resolved lazily: importing app.rq_windows pulls in rq, which is safe but
    # should not happen merely by importing this module in a fresh child.
    from app.rq_windows import WindowsSpawnWorker

    return WindowsSpawnWorker


class AcceptanceWorker(_acceptance_worker_base()):
    """Windows-safe RQ worker whose horse runs the verified run_rq_horse entry."""

    def __init__(self, *args, config_path: Path, **kwargs):
        super().__init__(*args, **kwargs)
        self.config_path = config_path

    def _horse_spawn_command(self, job, queue) -> list[str]:
        return [
            sys.executable,
            "-c",
            ACCEPTANCE_HORSE_DRIVER,
            str(ROOT),
            str(self.config_path),
            self.name,
            job.id,
            self.execution.id,
        ]


def run_acceptance_worker(
    path: Path,
    worker_suffix: str,
    *,
    burst: bool = False,
    with_scheduler: bool = True,
) -> None:
    """Run a real RQ worker loop inside this supervised process (live mode only)."""
    record, _settings, engine, _adapter = configure_child(path)
    if record["mode"] != "live-rq":
        engine.dispose()
        raise RuntimeError("Acceptance worker requires the explicit owned live configuration")
    client = None
    try:
        from redis import Redis

        client = Redis.from_url(
            record["redis_url"], socket_connect_timeout=2, socket_timeout=10
        )
        token = record["redis_token"]
        if client.get(f"mangaflow:acceptance:{token}:owner") != token.encode():
            raise RuntimeError("Redis ownership changed before worker startup")
        if not re.fullmatch(r"acceptance_[a-zA-Z0-9_-]+", worker_suffix):
            raise ValueError("Unowned worker name suffix")
        worker_name = f"acceptance_{token}_{worker_suffix}"

        class _BoundAcceptanceWorker(AcceptanceWorker):
            def _horse_environment(self) -> dict[str, str]:
                # The horse entry derives everything from worker.json; keep the
                # environment minimal and free of connection credentials.
                return {
                    key: os.environ[key]
                    for key in ("SYSTEMROOT", "WINDIR", "PATH", "PATHEXT")
                    if key in os.environ
                }

        worker = _BoundAcceptanceWorker(
            [record["queue_name"]],
            name=worker_name,
            connection=client,
            config_path=path,
        )
        worker.work(burst=burst, with_scheduler=with_scheduler)
    finally:
        if client is not None:
            client.close()
        engine.dispose()
