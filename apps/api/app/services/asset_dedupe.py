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


def live_duplicate(
    db: Session,
    *,
    project_id: str,
    sha256: str,
    source: str | None = None,
    kind: str | None = None,
) -> Asset | None:
    """Return the live asset holding these bytes, if any.

    ``source``/``kind`` narrow the match to the caller's own provenance: a
    byte-identical user upload (or another generation kind's row) must never
    be attached to a new paid candidate of a different kind.
    """

    filters = [
        Asset.project_id == project_id,
        Asset.sha256 == sha256,
        Asset.deleted_at.is_(None),
    ]
    if source is not None:
        filters.append(Asset.source == source)
    if kind is not None:
        filters.append(Asset.kind == kind)
    return db.scalar(select(Asset).where(*filters))


def adopt_deleted_duplicate(
    db: Session,
    *,
    project_id: str,
    sha256: str,
    source: str | None = None,
    kind: str | None = None,
) -> Asset | None:
    """Revive the soft-deleted row holding the constraint slot, if any.

    Returns ``None`` when no tombstoned row exists so callers can re-raise
    the original integrity error. ``source``/``kind`` keep the revival inside
    the caller's own provenance: a deleted user upload is never resurrected
    as a generated asset (and vice versa).
    """

    filters = [
        Asset.project_id == project_id,
        Asset.sha256 == sha256,
        Asset.deleted_at.is_not(None),
    ]
    if source is not None:
        filters.append(Asset.source == source)
    if kind is not None:
        filters.append(Asset.kind == kind)
    row = db.scalar(select(Asset).where(*filters))
    if row is None:
        return None
    row.deleted_at = None
    row.version += 1
    db.flush()
    return row
