"""Content-addressed duplicate resolution for asset rows.

``assets`` enforces uniqueness on ``(project_id, sha256)`` across live and
soft-deleted rows: a tombstone keeps occupying its slot, so byte-identical
regeneration after a delete must adopt that row instead of failing the
insert forever. Adoption only clears the tombstone — candidates swept when
the row was deleted keep their own ``deleted_at`` and stay deleted.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset


def live_duplicate(db: Session, *, project_id: str, sha256: str) -> Asset | None:
    """Return the live asset holding these bytes, if any."""

    return db.scalar(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.sha256 == sha256,
            Asset.deleted_at.is_(None),
        )
    )


def adopt_deleted_duplicate(
    db: Session, *, project_id: str, sha256: str
) -> Asset | None:
    """Revive the soft-deleted row holding the constraint slot, if any.

    Returns ``None`` when no tombstoned row exists so callers can re-raise
    the original integrity error.
    """

    row = db.scalar(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.sha256 == sha256,
            Asset.deleted_at.is_not(None),
        )
    )
    if row is None:
        return None
    row.deleted_at = None
    row.version += 1
    db.flush()
    return row
