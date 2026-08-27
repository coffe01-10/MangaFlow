import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

from app.config import get_settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _placeholder_vertex_credentials(tmp_path_factory, monkeypatch):
    """Give tests a dummy Vertex file so model routes do not 409 offline."""

    creds = tmp_path_factory.mktemp("vertex") / "placeholder.json"
    creds.write_text("{}", encoding="utf-8")
    settings = get_settings()
    monkeypatch.setattr(settings, "google_cloud_project", "test-project")
    monkeypatch.setattr(settings, "google_application_credentials", creds)


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
