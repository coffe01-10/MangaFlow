import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Offline suite must not read a developer .env or inherit live credential env.
os.environ["MANGAFLOW_DISABLE_DOTENV"] = "1"
for _live_name in (
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "MANGAFLOW_CREDENTIAL_MASTER_KEY",
    "MANGAFLOW_PROXY_URL",
):
    os.environ.pop(_live_name, None)

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

from app.config import get_settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

OFFLINE_PROVIDER_PROJECT = "test-project"


@pytest.fixture(autouse=True)
def _offline_configured_provider_premise(tmp_path_factory, monkeypatch):
    """Explicit placeholder provider premise for offline API tests.

    Supplies a non-secret configured Vertex path (empty JSON + test project)
    without reading `.env` or a real service-account file. This is not a live
    provider call and does not load developer credentials.
    """

    creds = tmp_path_factory.mktemp("offline-provider") / "placeholder.json"
    creds.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MANGAFLOW_DISABLE_DOTENV", "1")
    settings = get_settings()
    monkeypatch.setattr(settings, "google_cloud_project", OFFLINE_PROVIDER_PROJECT)
    monkeypatch.setattr(settings, "google_application_credentials", creds)
    monkeypatch.setattr(settings, "google_genai_use_vertexai", False)
    return creds


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    db = TestingSession()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _audit_session_isolated_to_test_database(db_session, monkeypatch):
    """Point the model-call-audit independent session at the test database.

    The audit service deliberately opens its own ``SessionLocal`` so ledger
    rows survive caller rollbacks; without this fixture that session would hit
    the configured development database instead of the per-test database.
    """

    from app.services.worker_handlers import model_call_audit

    audit_factory = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
    )
    monkeypatch.setattr(model_call_audit, "SessionLocal", audit_factory)


@pytest.fixture
def client(db_session):
    def override_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
