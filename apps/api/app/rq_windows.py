"""Windows-compatible RQ worker: spawn horses without fork/os.setpgrp.

rq's SpawnWorker relies on POSIX-only APIs: the horse calls os.setpgrp(), the
monitor uses os.wait4() and os.killpg(), and unexpected-death reporting touches
os.WTERMSIG. None of these exist on Windows, so the shipped worker class crashes
on the very first job. This module provides a Worker subclass that spawns the
horse as a regular child process, monitors it through the process handle and
reports failures from the exit code. Connection credentials travel in the child
environment (RQ_HORSE_REDIS_KWARGS), never in the command line.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from rq.exceptions import InvalidJobOperation
from rq.job import JobStatus
from rq.utils import now
from rq.worker import Worker
from sqlalchemy import update

LOGGER = logging.getLogger("mangaflow.worker")

# Marker written when the monitor force-kills a horse past its RQ timeout.
# The spawned horse dies without running any application cleanup, so lease
# recovery must propagate this cause instead of reporting LEASE_EXPIRED.
JOB_TIMEOUT_ERROR_CODE = "JOB_TIMEOUT"
JOB_TIMEOUT_ERROR_MESSAGE = "生成超时，已由执行器强制终止"

# SpawnWorker's inline horse entry, minus the POSIX os.setpgrp() call.
_GENERIC_HORSE_CODE = """
import json, os, sys
from redis import Redis
from rq import Queue, Worker
from rq.executions import Execution
from rq.job import Job

redis = Redis(**json.loads(os.environ["RQ_HORSE_REDIS_KWARGS"]))
worker = Worker.find_by_key(os.environ["RQ_WORKER_KEY"], connection=redis)
if not worker:
    sys.exit(1)
job = Job.fetch(os.environ["RQ_JOB_ID"], connection=redis)
queue = Queue(os.environ["RQ_QUEUE_NAME"], connection=redis)
worker.execution = Execution.fetch(os.environ["RQ_EXECUTION_ID"], job.id, connection=redis)
worker._is_horse = True
worker.main_work_horse(job, queue)
"""


def horse_environment(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Base child environment for a spawn worker horse.

    The horse runs ``python -c`` with the worker's working directory (the repo
    root, which Settings relies on for its relative .env/./storage paths), so
    ``sys.path[0]`` is that directory, not the API root. rq's ``--path`` option
    only mutates the parent's ``sys.path``, so the horse needs the API root
    (the parent directory of the ``app`` package) in PYTHONPATH; without it
    every job dies on ``import app`` before ``execute_job`` can run and burns
    its retry budget.
    """
    env = dict(base_env) if base_env is not None else dict(os.environ)
    api_root = str(Path(__file__).resolve().parents[1])
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{api_root}{os.pathsep}{existing}" if existing else api_root
    return env


def _persist_timeout_marker(job_id: str) -> None:
    """Best-effort stamp the leased job row with the force-kill cause.

    Killing the horse skips every in-horse ``finally``/failure handler, so the
    parent monitor is the only witness of the timeout; without this marker the
    later lease recovery reports LEASE_EXPIRED and the timeout never surfaces.
    Must never raise: a database hiccup here cannot be allowed to break the
    kill path that follows.
    """
    # Function-local imports: this module must stay importable as a bare
    # rq worker class and the app graph is only needed on the kill path.
    from app.database import SessionLocal
    from app.models import GenerationJob
    from app.services.job_service import LEASED_JOB_STATUSES

    try:
        with SessionLocal() as db:
            db.execute(
                update(GenerationJob)
                .where(
                    GenerationJob.id == job_id,
                    GenerationJob.status.in_(LEASED_JOB_STATUSES),
                )
                .values(
                    error_code=JOB_TIMEOUT_ERROR_CODE,
                    error_message=JOB_TIMEOUT_ERROR_MESSAGE,
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
    except Exception:
        LOGGER.warning(
            "failed to persist the JOB_TIMEOUT marker for job %s", job_id, exc_info=True
        )


class WindowsSpawnWorker(Worker):
    """Worker whose horse is a plain child process, safe on Windows.

    The horse performs the job through rq's own main_work_horse (result,
    failure and retry bookkeeping included); the parent only spawns and
    supervises the process. Subclasses may override _horse_spawn_command to
    launch an application-specific horse entry.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._horse_popen: subprocess.Popen | None = None

    def _horse_spawn_command(self, job, queue) -> list[str]:
        return [sys.executable, "-c", _GENERIC_HORSE_CODE]

    def _horse_environment(self, queue) -> dict[str, str]:
        env = horse_environment()
        redis_kwargs = {
            key: value
            for key, value in self.connection.connection_pool.connection_kwargs.items()
            if key != "retry"
        }
        env["RQ_HORSE_REDIS_KWARGS"] = json.dumps(redis_kwargs, default=str)
        env["RQ_WORKER_KEY"] = self.key
        env["RQ_QUEUE_NAME"] = queue.name
        return env

    def fork_work_horse(self, job, queue):
        os.environ["RQ_WORKER_ID"] = self.name
        os.environ["RQ_JOB_ID"] = job.id
        os.environ["RQ_EXECUTION_ID"] = self.execution.id
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._horse_popen = subprocess.Popen(
            self._horse_spawn_command(job, queue),
            env=self._horse_environment(queue),
            creationflags=creationflags,
        )
        self._horse_pid = self._horse_popen.pid
        self.procline(f"Spawned {self._horse_popen.pid} at {time.time()}")

    def kill_horse(self, sig=None):
        horse = self._horse_popen
        if horse is None or horse.poll() is not None:
            return
        try:
            horse.kill()
        except OSError as error:
            if error.errno != errno.ESRCH:
                raise

    def wait_for_horse(self):
        horse = self._horse_popen
        if horse is None:
            return None, None, None
        return horse.pid, horse.wait(), None

    def monitor_work_horse(self, job, queue):
        """Poll the horse handle; rq's wait4/SIGALRM monitor is POSIX-only."""
        job.started_at = now()
        ret_val = None
        last_beat = time.monotonic()
        while True:
            ret_val = self._horse_popen.poll()
            if ret_val is not None:
                break
            if time.monotonic() - last_beat >= self.job_monitoring_interval:
                self.set_current_job_working_time((now() - job.started_at).total_seconds())
                if job.timeout != -1 and self.current_job_working_time > (job.timeout + 60):
                    self.heartbeat(self.job_monitoring_interval + 60)
                    # Record why the horse is about to die while the parent can
                    # still write it; the kill itself must happen regardless.
                    _persist_timeout_marker(job.id)
                    self.kill_horse()
                    self._horse_popen.wait()
                    ret_val = self._horse_popen.returncode
                    break
                self.maintain_heartbeats(job)
                last_beat = time.monotonic()
            time.sleep(0.05)
        self.set_current_job_working_time(0)
        self._horse_pid = 0

        if ret_val == 0:
            return
        try:
            job_status = job.get_status()
        except InvalidJobOperation:
            return  # Job completed and its ttl has expired
        if self._stopped_job_id == job.id:
            if job.stopped_callback:
                job.execute_stopped_callback(self.death_penalty_class)
            self.handle_job_failure(
                job, queue=queue, exc_string="Job stopped by user, work-horse terminated."
            )
        elif job_status not in [JobStatus.FINISHED, JobStatus.FAILED]:
            if not job.ended_at:
                job.ended_at = now()
            exc_string = f"Work-horse terminated unexpectedly; return code {ret_val}; "
            self.handle_work_horse_killed(job, self._horse_popen.pid, ret_val, None)
            self.handle_job_failure(job, queue=queue, exc_string=exc_string)
