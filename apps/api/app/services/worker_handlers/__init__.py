"""Business handlers split out of ``app.worker_tasks`` by job type.

The claim/lease/heartbeat/cancel/concurrency/retry state machine and the
unified dispatch stay in ``app.worker_tasks``.  Handlers only call the shared
execution primitives in :mod:`execution` and the provider-call helpers in
:mod:`provider`; they must never reimplement or bypass that state machine.
"""
