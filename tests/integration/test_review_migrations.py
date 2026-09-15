"""Live PostgreSQL regressions for shared enums and legacy style normalization."""

from alembic import command
from alembic.config import Config
from app.models import Project, StyleProfile
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

# Pinned independently of the migration's OWNED_ENUM_NAMES constant: a drift
# between the two is exactly what these assertions must catch.
EXPECTED_SHARED_ENUMS = {
    "resolution",
    "workflowmode",
    "assetstatus",
    "jobstatus",
    "stylestatus",
    "pagestatus",
}


def _migrate(engine, direction, target):
    # A fresh Config and transaction for every step models separate invocations.
    with engine.begin() as connection:
        config = Config("apps/api/alembic.ini")
        config.attributes["connection"] = connection
        getattr(command, direction)(config, target)


def _enum_names(connection, schema: str) -> set[str]:
    return set(
        connection.scalars(
            text(
                "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON t.typnamespace = n.oid "
                "WHERE n.nspname = :schema AND t.typtype = 'e'"
            ),
            {"schema": schema},
        )
    )


def test_pg_enum_lifecycle_across_split_upgrade_and_full_roundtrip(live_pg_isolated_schema):
    engine, schema = live_pg_isolated_schema
    _migrate(engine, "downgrade", "base")
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON t.typnamespace = n.oid "
                    "WHERE n.nspname = :schema AND t.typtype = 'e'"
                ),
                {"schema": schema},
            )
            == 0
        )
    _migrate(engine, "upgrade", "949d8856e6a4")
    _migrate(engine, "upgrade", "20260714_01")
    _migrate(engine, "upgrade", "head")
    with engine.connect() as connection:
        # The split upgrade path must end with the exact shared-enum set —
        # a skipped or duplicated CREATE TYPE only shows up as a positive
        # mismatch, never as a DuplicateObject-free silence.
        assert _enum_names(connection, schema) == EXPECTED_SHARED_ENUMS
    _migrate(engine, "downgrade", "base")
    with engine.connect() as connection:
        assert _enum_names(connection, schema) == set()
    _migrate(engine, "upgrade", "head")
    with engine.connect() as connection:
        assert _enum_names(connection, schema) == EXPECTED_SHARED_ENUMS
        assert "workflow_runs" in inspect(connection).get_table_names()


def test_pg_legacy_style_backfill_matches_upper_and_lower_case(live_pg_isolated_schema):
    engine, _schema = live_pg_isolated_schema
    with Session(engine) as db:
        project = Project(
            name="Migration fixture",
            text_model_alias="text.fast",
            image_model_alias="image.nano_banana_2",
        )
        db.add(project)
        db.flush()
        for prefix in ("B1", "b1"):
            db.add(
                StyleProfile(
                    project_id=project.id,
                    name=f"{prefix} 黑白稿",
                    color_mode="MONOCHROME",
                    profile={},
                )
            )
        db.commit()
    _migrate(engine, "downgrade", "20260715_08")
    _migrate(engine, "upgrade", "20260716_09")
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT name, color_mode, profile FROM style_profiles")
        ).all()
        assert len(rows) == 2
        assert {row.name for row in rows} == {"B1 彩色稿", "b1 彩色稿"}
        assert len({row.color_mode for row in rows}) == 1
        assert all(row.profile["palette_confirmed"] is False for row in rows)
        assert all(row.profile["test_image_approved"] is False for row in rows)
