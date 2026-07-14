import weakref
from pathlib import Path

from pydantic import BaseModel

from app.config import Settings
from app.model_adapters.base import StructuredRequest
from app.model_adapters.vertex import VertexTextAdapter
from app.services.model_registry import build_registry


class SmokeReply(BaseModel):
    ok: bool


class FakeResponse:
    text = '{"ok": true}'


class FakeModels:
    def __init__(self, client):
        self.client_ref = weakref.ref(client)

    def generate_content(self, **_kwargs):
        client = self.client_ref()
        if client is None or client.closed:
            raise RuntimeError("client was closed before request completion")
        return FakeResponse()


class FakeClient:
    def __init__(self):
        self.closed = False
        self.models = FakeModels(self)

    def close(self):
        self.closed = True


def test_vertex_client_stays_alive_for_entire_request(monkeypatch):
    settings = Settings(
        google_cloud_project="test-project",
        google_application_credentials=Path(__file__),
    )
    adapter = VertexTextAdapter(settings, build_registry(settings)["text.fast"])
    clients: list[FakeClient] = []

    def make_client():
        client = FakeClient()
        clients.append(client)
        return client

    monkeypatch.setattr(adapter, "_client", make_client)
    result = adapter.generate_structured(
        StructuredRequest(prompt="return ok", temperature=0), SmokeReply
    )

    assert result.ok is True
    assert clients[0].closed is True
