"""add director command journal tables

Revision ID: 20260903_27
Revises: 20260902_26
Create Date: 2026-09-03

Additive V02-40 journal. Commands never write the business tables from this
migration; rows start empty. Downgrade drops the two new tables only.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_27"
down_revision: str | None = "20260902_26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "director_command_groups",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("command_group_id", sa.String(length=36), nullable=False),
        sa.Column("page_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PROPOSED"),
        sa.Column("first_result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["manga_pages.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "project_id",
            "command_group_id",
            name="uq_director_command_groups_project_group",
        ),
    )
    op.create_index(
        "ix_director_command_groups_project_id",
        "director_command_groups",
        ["project_id"],
    )
    op.create_index(
        "ix_director_command_groups_page_id",
        "director_command_groups",
        ["page_id"],
    )
    op.create_table(
        "director_commands",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("command_id", sa.String(length=36), nullable=False),
        sa.Column("command_group_id", sa.String(length=36), nullable=False),
        sa.Column("retry_of_command_id", sa.String(length=36), nullable=True),
        sa.Column("inverse_of_command_id", sa.String(length=36), nullable=True),
        sa.Column("operation", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PROPOSED"),
        sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("expected_version", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("diff", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("inverse_payload", sa.JSON(), nullable=True),
        sa.Column("before_snapshot", sa.JSON(), nullable=True),
        sa.Column("storyboard_version_after", sa.Integer(), nullable=True),
        sa.Column("envelope_created_at", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["group_id"], ["director_command_groups.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "project_id",
            "command_id",
            name="uq_director_commands_project_command",
        ),
    )
    op.create_index(
        "ix_director_commands_project_id",
        "director_commands",
        ["project_id"],
    )
    op.create_index("ix_director_commands_group_id", "director_commands", ["group_id"])
    op.create_index(
        "ix_director_commands_command_group_id",
        "director_commands",
        ["command_group_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_director_commands_command_group_id", table_name="director_commands")
    op.drop_index("ix_director_commands_group_id", table_name="director_commands")
    op.drop_index("ix_director_commands_project_id", table_name="director_commands")
    op.drop_table("director_commands")
    op.drop_index("ix_director_command_groups_page_id", table_name="director_command_groups")
    op.drop_index(
        "ix_director_command_groups_project_id", table_name="director_command_groups"
    )
    op.drop_table("director_command_groups")
