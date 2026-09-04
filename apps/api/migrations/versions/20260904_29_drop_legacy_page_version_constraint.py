"""drop legacy manga_pages (chapter_id, page_number, version) unique

Revision ID: 20260904_29
Revises: 20260903_28
Create Date: 2026-09-04

The initial schema created ``UNIQUE (chapter_id, page_number, version)`` on
manga_pages, where ``version`` is the optimistic-lock counter. Migration
20260714_01 introduced the intended ``uq_manga_pages_revision`` constraint on
(chapter_id, page_number, revision_no) but the legacy constraint was never
dropped, so every Alembic-upgraded database still rejects two revisions of
the same page whose lock counters happen to be equal — a landmine for the
documented page-revision feature. ``app.models`` never declared the legacy
constraint, so create_all databases (tests) do not have it; this migration
restores parity.

Also drops ``ix_candidate_lineage_child_candidate_id`` (20260903_28), which
duplicates the unique constraint ``uq_candidate_lineage_child`` and is not
declared in the models.

SQLite cannot drop an unnamed inline table constraint, so manga_pages is
rebuilt through batch mode with an explicit ``copy_from`` matching the
models schema (without the legacy constraint). PostgreSQL drops the
auto-named constraint discovered from the catalog. Data is preserved; the
downgrade restores both artifacts. Real PostgreSQL upgrade/downgrade is
NOT RUN (no live PostgreSQL in this environment).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_29"
down_revision: str | None = "20260903_28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _manga_pages_target() -> sa.Table:
    """Authoritative manga_pages schema (models.py at this revision)."""

    metadata = sa.MetaData()
    return sa.Table(
        "manga_pages",
        metadata,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("chapter_id", sa.String(length=36), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("page_function", sa.String(length=32), nullable=False),
        sa.Column("panel_count", sa.Integer(), nullable=False),
        sa.Column("reading_direction", sa.String(length=8), nullable=False),
        sa.Column("resolution", sa.String(length=11), nullable=False),
        sa.Column("style_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=19), nullable=False),
        sa.Column("scene_ids", sa.JSON(), nullable=False),
        sa.Column("beat_ids", sa.JSON(), nullable=False),
        sa.Column("locked_fields", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("revision_no", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "estimated_text_chars", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("estimated_bubbles", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source_coverage", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("selected_candidate_id", sa.String(length=36), nullable=True),
        sa.Column(
            "continuity_status",
            sa.String(length=32),
            server_default="NOT_CHECKED",
            nullable=False,
        ),
        sa.Column(
            "storyboard_version", sa.Integer(), server_default="1", nullable=False
        ),
        sa.Column("selected_candidate_ack_version", sa.Integer(), nullable=True),
        sa.Column("canvas", sa.JSON(), nullable=True),
        sa.Column("geometry_save_command", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        # Standalone indexes, matching the artifacts earlier migrations
        # created; their downgrades call drop_index on these names, and the
        # batch rebuild must recreate everything the schema carried.
        sa.Index("ix_manga_pages_chapter_id", "chapter_id"),
        sa.Index(
            "uq_manga_pages_revision",
            "chapter_id",
            "page_number",
            "revision_no",
            unique=True,
        ),
    )


def _set_sqlite_foreign_keys(enabled: bool) -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        return
    # Same treatment as migration 20260901_23: SQLite ignores foreign_keys
    # changes inside a transaction, so toggle it on the physical connection
    # through an autocommit block around the batch table recreation.
    with op.get_context().autocommit_block():
        connection.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")
        expected = 1 if enabled else 0
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != expected:
            raise RuntimeError("could not change SQLite foreign-key mode")


def _legacy_constraint_names(bind, inspector) -> list[str]:
    """PostgreSQL unique constraints on manga_pages over exactly
    (chapter_id, page_number, version) — matched by definition, not by the
    default-generated name."""

    names: list[str] = []
    for constraint in inspector.get_unique_constraints("manga_pages"):
        columns = list(constraint.get("column_names") or [])
        if columns == ["chapter_id", "page_number", "version"]:
            names.append(constraint["name"])
    return [name for name in names if name]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        inspector = sa.inspect(bind)
        for name in _legacy_constraint_names(bind, inspector):
            op.drop_constraint(name, "manga_pages", type_="unique")
    else:
        _set_sqlite_foreign_keys(False)
        try:
            with op.batch_alter_table(
                "manga_pages", copy_from=_manga_pages_target(), recreate="always"
            ):
                pass
        finally:
            _set_sqlite_foreign_keys(True)
        # The rebuild dropped and recreated the table; verify its own outbound
        # references survived. Scoped to manga_pages: pre-existing orphans in
        # unrelated tables are owned by their own guarded migrations.
        violations = bind.exec_driver_sql(
            "PRAGMA foreign_key_check(manga_pages)"
        ).fetchall()
        if violations:
            raise RuntimeError(
                f"foreign_key_check failed after manga_pages rebuild: {violations[:3]}"
            )

    index_name = "ix_candidate_lineage_child_candidate_id"
    inspector = sa.inspect(bind)
    existing = {
        index["name"]
        for index in inspector.get_indexes("candidate_lineage")
        if index["name"]
    }
    if index_name in existing:
        op.drop_index(index_name, table_name="candidate_lineage")


def downgrade() -> None:
    bind = op.get_bind()
    index_name = "ix_candidate_lineage_child_candidate_id"
    inspector = sa.inspect(bind)
    existing = {
        index["name"]
        for index in inspector.get_indexes("candidate_lineage")
        if index["name"]
    }
    if index_name not in existing:
        op.create_index(
            index_name,
            "candidate_lineage",
            ["child_candidate_id"],
            unique=False,
        )

    if bind.dialect.name == "postgresql":
        op.create_unique_constraint(
            "manga_pages_chapter_id_page_number_version_key",
            "manga_pages",
            ["chapter_id", "page_number", "version"],
        )
    else:
        metadata = sa.MetaData()
        target = sa.Table(
            "manga_pages",
            metadata,
            *[
                column.copy() if hasattr(column, "copy") else column
                for column in _manga_pages_target().columns
            ],
            sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("chapter_id", "page_number", "version"),
            sa.Index("ix_manga_pages_chapter_id", "chapter_id"),
            sa.Index(
                "uq_manga_pages_revision",
                "chapter_id",
                "page_number",
                "revision_no",
                unique=True,
            ),
        )
        _set_sqlite_foreign_keys(False)
        try:
            with op.batch_alter_table("manga_pages", copy_from=target, recreate="always"):
                pass
        finally:
            _set_sqlite_foreign_keys(True)
        violations = bind.exec_driver_sql(
            "PRAGMA foreign_key_check(manga_pages)"
        ).fetchall()
        if violations:
            raise RuntimeError(
                "foreign_key_check failed after manga_pages downgrade rebuild: "
                f"{violations[:3]}"
            )
