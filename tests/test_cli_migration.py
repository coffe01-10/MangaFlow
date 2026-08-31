"""SQLite migration guards for durable CLI execution state."""

import pytest
from alembic import command
from alembic.config import Config
from app.config import get_settings
from sqlalchemy import create_engine, inspect, text


def _config(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'cli-migration.db').as_posix()}"
    monkeypatch.setattr(get_settings(), "database_url", url)
    return url, Config("apps/api/alembic.ini")


def test_cli_run_migration_roundtrip_when_empty(tmp_path, monkeypatch):
    url, config = _config(tmp_path, monkeypatch)
    command.upgrade(config, "head")
    engine = create_engine(url)
    assert "cli_execution_runs" in inspect(engine).get_table_names()
    engine.dispose()
    command.downgrade(config, "20260831_21")
    engine = create_engine(url)
    assert "cli_execution_runs" not in inspect(engine).get_table_names()
    engine.dispose()
    command.upgrade(config, "head")


def test_cli_run_migration_refuses_audit_data_loss(tmp_path, monkeypatch):
    url, config = _config(tmp_path, monkeypatch)
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        connection.execute(
            text(
                """
                INSERT INTO cli_execution_runs (
                    id, job_id, model_call_attempt_id, connection_id,
                    catalog_model_id, run_token, relative_path, operation,
                    state, cleanup_state, lease_slot, request_checksum,
                    output_manifest, created_at, updated_at, version
                ) VALUES (
                    'run', 'job', 'attempt', NULL, NULL, 'token',
                    'cli_runs/run', 'image_generate', 'FAILED', 'RETAINED',
                    NULL, :checksum, '{}', CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP, 1
                )
                """
            ),
            {"checksum": "0" * 64},
        )
    engine.dispose()
    with pytest.raises(RuntimeError, match="refusing downgrade"):
        command.downgrade(config, "20260831_21")
    engine = create_engine(url)
    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM cli_execution_runs")
        ).scalar_one() == 1
        connection.execute(text("DELETE FROM cli_execution_runs"))
    engine.dispose()
    command.downgrade(config, "20260831_21")
    command.upgrade(config, "head")
