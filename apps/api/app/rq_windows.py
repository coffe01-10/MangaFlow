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
import os
import subprocess
import sys
import time

from rq.exceptions import InvalidJobOperation
from rq.job import JobStatus
from rq.utils import now
from rq.worker import Worker

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
        env = dict(os.environ)
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
