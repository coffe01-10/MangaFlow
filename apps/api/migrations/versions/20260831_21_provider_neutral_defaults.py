"""Make legacy project model aliases optional.

Revision ID: 20260831_21
Revises: 20260830_20
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from alembic import op

revision = "20260831_21"
down_revision = "20260830_20"
branch_labels = None
depends_on = None


def _alter_alias_nullability(*, nullable: bool) -> None:
    connection = op.get_bind()
    sqlite = connection.dialect.name == "sqlite"
    if sqlite:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 0:
            raise RuntimeError(
                "could not suspend SQLite foreign keys for project alias migration"
            )
    with op.batch_alter_table("projects") as batch_op:
        batch_op.alter_column(
            "text_model_alias",
            existing_type=sa.String(length=64),
            nullable=nullable,
            server_default=None,
        )
        batch_op.alter_column(
            "image_model_alias",
            existing_type=sa.String(length=64),
            nullable=nullable,
            server_default=None,
        )
    if sqlite:
        violations = list(connection.exec_driver_sql("PRAGMA foreign_key_check"))
        if violations:
            raise RuntimeError(
                "project alias migration produced foreign-key violations: "
                f"{violations[:5]}"
            )
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def upgrade() -> None:
    _alter_alias_nullability(nullable=True)


def downgrade() -> None:
    null_count = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM projects "
            "WHERE text_model_alias IS NULL OR image_model_alias IS NULL"
        )
    ).scalar_one()
    if null_count:
        raise RuntimeError(
            "refusing downgrade: projects with NULL legacy model aliases exist; "
            "set both aliases explicitly before restoring NOT NULL"
        )
    _alter_alias_nullability(nullable=False)
