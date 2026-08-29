"""Aggregate workflow engine facade.

The concrete implementation lives in cohesive modules under this package; the
facade re-exports the historical public surface of the former monolithic
``workflow_engine.py`` so every caller and monkeypatch seam keeps working:

- application callers: ``api/routes/workflow_definitions.py`` (top-level),
  ``services/job_service.py`` and ``worker_tasks.py`` (lazy imports);
- test seams: ``create_job``, ``enqueue_job`` and ``_next_revision`` are
  resolved by the owning modules through this facade at call time, so
  patching these attributes keeps taking effect.
"""

from app.services.job_service import create_job, enqueue_job, mark_job_cancelled
from app.services.workflow_engine.catalog import (
    CONDITION_OPERATORS,
    NODE_TYPE_MAP,
    NODE_TYPES,
    NodeTypeSpec,
    blank_graph,
    canonical_graph,
    chapter_export_graph,
    default_graph,
    graph_checksum,
    node_type_catalog,
)
from app.services.workflow_engine.execution import execute_workflow_node
from app.services.workflow_engine.lifecycle import approve_node, cancel_run, retry_run
from app.services.workflow_engine.planning import create_workflow_run
from app.services.workflow_engine.publish import (
    PUBLISH_REVISION_MAX_ATTEMPTS,
    PublishRevisionConflictError,
    _next_revision,
    publish_workflow,
)
from app.services.workflow_engine.reconciliation import get_run, reconcile_run
from app.services.workflow_engine.validation import validate_graph

__all__ = [
    "CONDITION_OPERATORS",
    "NODE_TYPES",
    "NODE_TYPE_MAP",
    "NodeTypeSpec",
    "PUBLISH_REVISION_MAX_ATTEMPTS",
    "PublishRevisionConflictError",
    "_next_revision",
    "approve_node",
    "blank_graph",
    "cancel_run",
    "canonical_graph",
    "chapter_export_graph",
    "create_workflow_run",
    "create_job",
    "default_graph",
    "enqueue_job",
    "execute_workflow_node",
    "get_run",
    "graph_checksum",
    "mark_job_cancelled",
    "node_type_catalog",
    "publish_workflow",
    "reconcile_run",
    "retry_run",
    "validate_graph",
]
