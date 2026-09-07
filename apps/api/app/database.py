import json
import sqlite3
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


@event.listens_for(Engine, "connect")
def _enable_sqlite_integrity(dbapi_connection, _connection_record) -> None:
    """Make SQLite enforce the foreign keys already declared by the schema."""

    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _strict_json_serializer(value) -> str:
    """Serialize JSON columns without NaN/Infinity (#225).

    SQLAlchemy's default json.dumps emits bare ``NaN``/``Infinity`` tokens,
    which are not valid JSON: a poisoned free-form field would store a row no
    strict parser (provider APIs included) accepts. Failing the write loudly
    here is the last line of defense behind the wire-level 422 and the schema
    finiteness validators. No ``json_deserializer`` is configured: reads stay
    on stdlib defaults so legacy rows still load.
    """

    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    json_serializer=_strict_json_serializer,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
