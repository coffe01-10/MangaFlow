"""Resource-scoped Redis/RQ cleanup for the acceptance harness.

This is not a Redis emulator. The live harness remains disabled until its process
supervisor and business scenarios are repaired and independently accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import uuid4

from rq import Queue
from rq.job import Job
from rq.registry import (
    CanceledJobRegistry,
    DeferredJobRegistry,
    FailedJobRegistry,
    FinishedJobRegistry,
    ScheduledJobRegistry,
    StartedJobRegistry,
)

REGISTRIES = (
    StartedJobRegistry,
    FinishedJobRegistry,
    FailedJobRegistry,
    DeferredJobRegistry,
    ScheduledJobRegistry,
    CanceledJobRegistry,
)


def _text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("Invalid acceptance resource identifier")
    return value


@dataclass
class RedisAcceptanceResources:
    client: object
    token: str = field(default_factory=lambda: uuid4().hex)
    queues: set[str] = field(default_factory=set)
    jobs: set[str] = field(default_factory=set)
    workers: set[str] = field(default_factory=set)
    # Spawned worker process trees (OwnedProcessTree), keyed by their
    # rq:worker:<name> key. Redis-side evidence can never prove a spawned
    # worker is dead — a Job-Object kill leaves no death field — so the
    # supervisor's own stopped state is the liveness proof for cleanup.
    spawned: dict[str, object] = field(default_factory=dict)
    cleaned: bool = False
    sealed: bool = False

    @property
    def prefix(self) -> str:
        return f"mangaflow:acceptance:{self.token}:"

    @property
    def owner_key(self) -> str:
        return self.prefix + "owner"

    def claim(self) -> None:
        if not re.fullmatch(r"[a-f0-9]{32}", self.token):
            raise ValueError("Invalid acceptance ownership token")
        options = self.client.connection_pool.connection_kwargs
        if (
            options.get("host") not in {"127.0.0.1", "localhost", "::1"}
            or options.get("port") != 56379
            or type(options.get("db")) is not int
            or not 1 <= options["db"] <= 15
        ):
            raise ValueError("Redis client does not target an isolated acceptance endpoint")
        if not self.client.set(self.owner_key, self.token, nx=True):
            raise RuntimeError("Acceptance resource ownership already exists")

    def _owner(self, reader) -> None:
        if _text(reader.get(self.owner_key)) != self.token:
            raise RuntimeError("Refusing cleanup: acceptance ownership changed or missing")

    def _can_register(self) -> None:
        if self.sealed:
            raise RuntimeError("Acceptance resource registration is closed")
        self._owner(self.client)

    def queue_name(self, label: str = "main") -> str:
        self._can_register()
        name = f"acceptance_{self.token}_{_identifier(label)}"
        if name not in self.queues:
            if self.client.exists(*self._queue_keys(name)) or self._scan(
                f"rq:queue:{name}:intermediate:first_seen:*"
            ):
                raise RuntimeError("Refusing to adopt a preexisting RQ queue")
            if self.client.sismember("rq:queues", f"rq:queue:{name}"):
                raise RuntimeError("Refusing to adopt a registered RQ queue")
            self.queues.add(name)
        return name

    def track_spawned_worker(self, label: str, tree) -> None:
        """Bind a spawned acceptance worker's process tree so cleanup can
        require verified tree.stop() before deleting its Redis state."""
        self._can_register()
        self.spawned[f"rq:worker:acceptance_{self.token}_{_identifier(label)}"] = tree

    def worker_name(self, label: str) -> str:
        self._can_register()
        name = f"acceptance_{self.token}_{_identifier(label)}"
        if (
            name in self.workers
            or self.client.exists(f"rq:worker:{name}")
            or self.client.sismember("rq:workers", f"rq:worker:{name}")
        ):
            raise RuntimeError("Refusing to adopt or reuse an RQ worker")
        self.workers.add(name)
        return name

    def track_job(self, job_id: str) -> None:
        self._can_register()
        _identifier(job_id)
        if job_id not in self.jobs:
            if (
                self.client.exists(*self._job_keys(job_id))
                or self._scan(f"rq:execution:{job_id}:*")
                or self._scan(f"rq:job:{job_id}-slot-*")
            ):
                raise RuntimeError("Refusing to adopt a preexisting RQ job")
            self.jobs.add(job_id)

    def app_key(self, label: str) -> str:
        self._can_register()
        return self.prefix + "app:" + _identifier(label)

    def _queue_keys(self, name: str) -> set[str]:
        queue = Queue(name, connection=self.client)
        return {
            queue.key,
            queue.intermediate_queue_key,
            queue.registry_cleaning_key,
            f"rq:workers:{name}",
            f"rq:scheduler:{name}",
            f"rq:scheduler-lock:{name}",
            *(registry.key_template.format(name) for registry in REGISTRIES),
        }

    def _job_keys(self, job_id: str) -> set[str]:
        job = Job(id=job_id, connection=self.client)
        return {
            job.key,
            job.dependents_key,
            job.dependencies_key,
            f"rq:results:{job_id}",
            f"rq:executions:{job_id}",
        }

    def _scan(self, pattern: str) -> set[str]:
        return {_text(key) for key in self.client.scan_iter(match=pattern, count=100)}

    def _collect(self) -> tuple[set[str], set[str]]:
        jobs = set(self.jobs)
        # Slot deferral creates RQ jobs with a new ID, outside the application
        # job table. Discover only descendants of explicitly tracked job IDs.
        for root in self.jobs:
            expression = re.compile(
                rf"(?:rq:job::?|rq:results:|rq:executions:|rq:execution:)"
                rf"({re.escape(root)}-slot-[a-f0-9]{{32}})(?::.*)?"
            )
            for prefix in ("rq:job:", "rq:job::", "rq:results:", "rq:executions:", "rq:execution:"):
                for key in self._scan(f"{prefix}{root}-slot-*"):
                    match = expression.fullmatch(key)
                    if match:
                        jobs.add(match.group(1))
        for name in self.queues:
            for job_id in self._listed_jobs(self.client, name):
                if any(
                    re.fullmatch(rf"{re.escape(root)}-slot-[a-f0-9]{{32}}", job_id)
                    for root in self.jobs
                ):
                    jobs.add(job_id)
        keys = set()
        for name in self.queues:
            keys.update(self._queue_keys(name))
            keys.update(self._scan(f"rq:queue:{name}:intermediate:first_seen:*"))
        for job_id in jobs:
            keys.update(self._job_keys(job_id))
            keys.update(self._scan(f"rq:execution:{job_id}:*"))
        keys.update(f"rq:worker:{name}" for name in self.workers)
        # Spawned acceptance workers are token-scoped by name even when they
        # were never registered via worker_name() — a Job-Object kill leaves
        # their worker keys behind.
        keys.update(self._scan(f"rq:worker:acceptance_{self.token}_*"))
        # A killed worker's hash can expire while its global rq:workers
        # membership lingers; the hash scan misses those names, so enumerate
        # the set itself for token-scoped members.
        keys.update(
            name
            for name in (
                _text(member) for member in self.client.smembers("rq:workers")
            )
            if name.startswith(f"rq:worker:acceptance_{self.token}_")
        )
        keys.update(self._scan(self.prefix + "app:*"))
        return keys, jobs

    def _listed_jobs(self, reader, name: str) -> set[str]:
        listed = reader.lrange(f"rq:queue:{name}", 0, -1)
        listed += reader.lrange(f"rq:queue:{name}:intermediate", 0, -1)
        for registry in REGISTRIES:
            listed += reader.zrange(registry.key_template.format(name), 0, -1)
        return {_text(item).split(":", 1)[0] for item in listed}

    def _validate(self, reader, jobs: set[str]) -> None:
        self._owner(reader)
        for key in ("rq:queues", "rq:workers"):
            if _text(reader.type(key)) not in {"none", "set"}:
                raise RuntimeError("Unexpected global RQ registry type")
        for job_id in jobs:
            origin = reader.hget(Job.key_for(job_id), "origin")
            if origin is not None and _text(origin) not in self.queues:
                raise RuntimeError("Refusing to delete a job belonging to another queue")
            if reader.hget(Job.key_for(job_id), "group_id"):
                raise RuntimeError("RQ groups are outside this acceptance cleanup scope")
        worker_keys = {f"rq:worker:{name}" for name in self.workers}
        for key in worker_keys:
            if not reader.hget(key, "death"):
                raise RuntimeError("Worker has not confirmed shutdown; stop it before cleanup")
        # A token-named worker key in Redis says nothing about liveness; only
        # the supervisor's stopped state proves a spawned worker is gone.
        for worker_key, tree in self.spawned.items():
            if tree.record.get("state") != "stopped":
                raise RuntimeError(
                    f"Spawned worker {worker_key} has not confirmed shutdown; "
                    "stop it before cleanup"
                )
        for name in self.queues:
            associated = {_text(key) for key in reader.smembers(f"rq:workers:{name}")}
            # Our own force-killed workers stay registered here (a Job Object
            # kill skips RQ's unregister); they are distinguished by the
            # ownership token and the whole key is cleaned below.
            token_workers = {
                key
                for key in associated
                if key.startswith(f"rq:worker:acceptance_{self.token}_")
            }
            foreign = {
                key for key in associated - worker_keys if key not in token_workers
            }
            if foreign:
                raise RuntimeError("Unowned worker is registered on the acceptance queue")
            # rq:scheduler-lock is PID-valued and self-expiring; a Job-killed
            # worker can never release it, so a leftover lock on a queue
            # served only by our own spawned workers is expected residue, not
            # a leak. A held lock on any other queue shape — including an
            # empty one — still means a live scheduler.
            if reader.get(f"rq:scheduler-lock:{name}") is not None and not (
                associated and associated == token_workers
            ):
                raise RuntimeError(
                    "Scheduler lock is still held; stop scheduler before cleanup"
                )
            # Never silently delete a queue/registry containing untracked jobs.
            if not self._listed_jobs(reader, name) <= jobs:
                raise RuntimeError("Untracked job found in acceptance queue or registry")

    def cleanup(self) -> None:
        if self.cleaned:
            return
        self.sealed = True
        keys, jobs = self._collect()
        worker_keys = {f"rq:worker:{name}" for name in self.workers}
        queue_keys = {f"rq:queue:{name}" for name in self.queues}
        # WATCH protects the ownership check and inspected resources. Only exact
        # members are removed from shared global sets; those sets are never DELed.
        with self.client.pipeline() as pipe:
            pipe.watch(self.owner_key, "rq:queues", "rq:workers", *sorted(keys))
            self._validate(pipe, jobs)
            pipe.multi()
            if queue_keys:
                pipe.srem("rq:queues", *sorted(queue_keys))
            if worker_keys:
                pipe.srem("rq:workers", *sorted(worker_keys))
            # Token-scoped spawned workers not registered via worker_name()
            # leave the same global-set membership; remove it as well.
            token_workers = {
                key
                for key in keys
                if key.startswith(f"rq:worker:acceptance_{self.token}_")
            }
            if token_workers - worker_keys:
                pipe.srem("rq:workers", *sorted(token_workers - worker_keys))
            if keys:
                pipe.delete(*sorted(keys))
            pipe.execute()
        # Keep ownership available after any failed cleanup so retry is possible.
        residual_keys, _ = self._collect()
        if (keys or residual_keys) and self.client.exists(*(keys | residual_keys)):
            raise RuntimeError("Acceptance Redis resources remain after cleanup")
        if queue_keys & {_text(value) for value in self.client.smembers("rq:queues")}:
            raise RuntimeError("Acceptance queue membership remains after cleanup")
        if worker_keys & {_text(value) for value in self.client.smembers("rq:workers")}:
            raise RuntimeError("Acceptance worker membership remains after cleanup")
        with self.client.pipeline() as pipe:
            pipe.watch(self.owner_key)
            self._owner(pipe)
            pipe.multi()
            pipe.delete(self.owner_key)
            if pipe.execute() != [1]:
                raise RuntimeError("Acceptance ownership marker was not removed")
        self.cleaned = True
