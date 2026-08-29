from __future__ import annotations

import sqlite3
from copy import deepcopy

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models import WorkflowDefinition, WorkflowVersion
from app.services.workflow_engine.catalog import canonical_graph, graph_checksum
from app.services.workflow_engine.validation import validate_graph


class PublishRevisionConflictError(Exception):
    """Raised when concurrent publishes cannot allocate a unique revision."""


PUBLISH_REVISION_MAX_ATTEMPTS = 3


def _lock_workflow(db: Session, workflow_id: str) -> WorkflowDefinition | None:
    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
    query = (
        select(WorkflowDefinition)
        .where(WorkflowDefinition.id == workflow_id)
        .execution_options(populate_existing=True)
    )
    if dialect_name == "postgresql":
        query = query.with_for_update()
    return db.scalar(query)


def _next_revision(db: Session, workflow_id: str) -> int:
    current = db.scalar(
        select(func.max(WorkflowVersion.revision)).where(WorkflowVersion.workflow_id == workflow_id)
    )
    return (current or 0) + 1


def publish_workflow(
    db: Session,
    workflow: WorkflowDefinition,
    *,
    max_attempts: int = PUBLISH_REVISION_MAX_ATTEMPTS,
) -> WorkflowVersion:
    # `_next_revision` 是模块级 monkeypatch 接缝（发布并发回归依赖它），必须
    # 在调用时经 facade 解析，保证对 facade 属性打补丁仍然生效。
    from app.services import workflow_engine as engine

    workflow_id = workflow.id
    last_error: BaseException | None = None
    for _attempt in range(max_attempts):
        try:
            with db.begin_nested():
                locked = _lock_workflow(db, workflow_id)
                if locked is None or locked.deleted_at is not None:
                    raise ValueError("工作流不存在")
                graph = canonical_graph(locked.draft_graph)
                report = validate_graph(graph)
                if not report.valid:
                    raise ValueError("工作流校验失败，不能发布")
                revision = engine._next_revision(db, locked.id)
                version = WorkflowVersion(
                    workflow_id=locked.id,
                    revision=revision,
                    graph=deepcopy(graph),
                    graph_checksum=graph_checksum(graph),
                    validation_report=report.model_dump(mode="json"),
                )
                db.add(version)
                db.flush()
                locked.published_version_id = version.id
                locked.version += 1
            db.commit()
        except IntegrityError as error:
            last_error = error
            db.rollback()
        except OperationalError as error:
            # SQLite readers can race while upgrading to a write transaction.
            # Retry from a fresh transaction, not the same stale read snapshot.
            code = getattr(error.orig, "sqlite_errorcode", None)
            db.rollback()
            if not isinstance(error.orig, sqlite3.OperationalError) or code is None:
                raise
            if code & 0xFF not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise
            last_error = error
        else:
            db.refresh(version)
            return version
    raise PublishRevisionConflictError("工作流正在被其他请求发布，请稍后重试") from last_error
