"""clean orphaned storyboard data before enabling sqlite foreign keys

Revision ID: 20260717_10
Revises: 20260716_09
Create Date: 2026-07-17
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_10"
down_revision: str | None = "20260716_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    return []


def _repair_page_references(connection: sa.Connection) -> None:
    beat_to_scene = {
        str(row[0]): str(row[1])
        for row in connection.execute(sa.text("SELECT id, scene_id FROM beats"))
    }
    pages = connection.execute(
        sa.text("SELECT id, beat_ids, scene_ids FROM manga_pages")
    ).mappings()
    for page in pages:
        valid_beats = [
            beat_id for beat_id in _json_list(page["beat_ids"]) if beat_id in beat_to_scene
        ]
        derived_scenes = list(dict.fromkeys(beat_to_scene[beat_id] for beat_id in valid_beats))
        references_are_valid = valid_beats == _json_list(page["beat_ids"]) and (
            derived_scenes == _json_list(page["scene_ids"])
        )
        if references_are_valid:
            continue
        connection.execute(
            sa.text(
                """
                UPDATE manga_pages
                SET beat_ids = :beat_ids,
                    scene_ids = :scene_ids,
                    continuity_status = 'NEEDS_REVIEW',
                    version = version + 1
                WHERE id = :page_id
                """
            ),
            {
                "beat_ids": json.dumps(valid_beats, ensure_ascii=False),
                "scene_ids": json.dumps(derived_scenes, ensure_ascii=False),
                "page_id": page["id"],
            },
        )


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            DELETE FROM dialogues
            WHERE panel_id IN (
                SELECT panels.id
                FROM panels
                LEFT JOIN manga_pages ON manga_pages.id = panels.page_id
                WHERE manga_pages.id IS NULL
            )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            DELETE FROM panels
            WHERE NOT EXISTS (
                SELECT 1 FROM manga_pages WHERE manga_pages.id = panels.page_id
            )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            DELETE FROM beats
            WHERE NOT EXISTS (
                SELECT 1 FROM scenes WHERE scenes.id = beats.scene_id
            )
            """
        )
    )
    _repair_page_references(connection)
    if connection.dialect.name == "sqlite":
        violations = list(connection.execute(sa.text("PRAGMA foreign_key_check")))
        if violations:
            raise RuntimeError(f"foreign key cleanup incomplete: {len(violations)} violation(s)")


def downgrade() -> None:
    # Deleted rows had no valid parent and cannot be restored safely. The release
    # procedure creates a byte-for-byte database backup before this migration.
    pass
