"""add structured panel presence and props

Revision ID: 20260716_09
Revises: 20260715_08
Create Date: 2026-07-16
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_09"
down_revision: str | None = "20260715_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_value(value: object, fallback: object) -> object:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


def _normalize_existing_first_pages(connection: sa.Connection) -> None:
    rows = connection.execute(
        sa.text(
            """
            SELECT p.id AS panel_id, p.characters, p.props, mp.chapter_id, c.project_id
            FROM panels AS p
            JOIN manga_pages AS mp ON mp.id = p.page_id
            JOIN chapters AS c ON c.id = mp.chapter_id
            WHERE mp.page_number = 1
            """
        )
    ).mappings()
    for row in rows:
        names = {
            item["primary_name"]: item["id"]
            for item in connection.execute(
                sa.text(
                    "SELECT id, primary_name FROM characters WHERE project_id = :project_id"
                ),
                {"project_id": row["project_id"]},
            ).mappings()
        }
        if not {"我", "妈妈", "爸爸"}.issubset(names):
            continue
        presence = {
            names["我"]: "VISIBLE",
            names["妈妈"]: "MENTIONED",
        }
        props = list(_json_value(row["props"], []))
        if "爸爸的灵牌" not in props:
            props.append("爸爸的灵牌")
        connection.execute(
            sa.text(
                """
                UPDATE panels
                SET characters = :characters,
                    character_presence = :presence,
                    props = :props
                WHERE id = :panel_id
                """
            ),
            {
                "characters": json.dumps([names["我"]], ensure_ascii=False),
                "presence": json.dumps(presence, ensure_ascii=False),
                "props": json.dumps(props, ensure_ascii=False),
                "panel_id": row["panel_id"],
            },
        )

    style_rows = connection.execute(
        sa.text(
            """
            SELECT id, name, color_mode, profile
            FROM style_profiles
            WHERE name LIKE 'B1%'
            """
        )
    ).mappings()
    for style in style_rows:
        profile = dict(_json_value(style["profile"], {}))
        profile["palette_confirmed"] = False
        profile["test_image_approved"] = False
        name = str(style["name"])
        if "黑白" in name:
            name = name.replace("黑白", "彩色")
        elif "彩色" not in name:
            name = f"{name} · 彩色"
        connection.execute(
            sa.text(
                """
                UPDATE style_profiles
                SET name = :name,
                    color_mode = 'color',
                    status = 'DRAFT',
                    profile = :profile
                WHERE id = :style_id
                """
            ),
            {
                "name": name,
                "profile": json.dumps(profile, ensure_ascii=False),
                "style_id": style["id"],
            },
        )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("panels")}
    with op.batch_alter_table("panels") as batch:
        if "character_presence" not in columns:
            batch.add_column(
                sa.Column(
                    "character_presence",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                )
            )
        if "props" not in columns:
            batch.add_column(
                sa.Column(
                    "props",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'[]'"),
                )
            )
    _normalize_existing_first_pages(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("panels") as batch:
        batch.drop_column("props")
        batch.drop_column("character_presence")
