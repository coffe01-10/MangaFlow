import weakref
from pathlib import Path

import pytest
from app.config import Settings
from app.model_adapters.base import StructuredRequest
from app.model_adapters.vertex import VertexTextAdapter
from app.services.model_registry import build_registry
from app.services.vertex_credentials import VertexCredentialManager
from pydantic import BaseModel


class SmokeReply(BaseModel):
    ok: bool


class FakeResponse:
    text = '{"ok": true}'


class FakeModels:
    def __init__(self, client):
        self.client_ref = weakref.ref(client)
        self.last_kwargs = None

    def generate_content(self, **kwargs):
        client = self.client_ref()
        if client is None or client.closed:
            raise RuntimeError("client was closed before request completion")
        self.last_kwargs = kwargs
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
        StructuredRequest(
            prompt="return ok",
            temperature=0,
            metadata={"max_output_tokens": 64, "thinking_budget": 0},
        ),
        SmokeReply,
    )

    assert result.ok is True
    assert clients[0].models.last_kwargs["config"].thinking_config.thinking_budget == 0
    assert clients[0].closed is True


@pytest.mark.parametrize("failures", [0, 2, 3])
def test_vertex_adapter_exposes_every_dispatch_on_success_and_failure(monkeypatch, failures):
    from app.model_adapters.base import ProviderAdapterError

    settings = Settings(
        google_cloud_project="test-project",
        google_application_credentials=Path(__file__),
    )
    adapter = VertexTextAdapter(settings, build_registry(settings)["text.fast"])
    adapter.credential_manager = VertexCredentialManager(max_attempts=3, base_backoff_seconds=0)
    calls = []

    class Unavailable(Exception):
        status_code = 503

    def generate_content(_self, **_kwargs):
        calls.append(1)
        if len(calls) <= failures:
            raise Unavailable("service unavailable")
        return FakeResponse()

    monkeypatch.setattr(FakeModels, "generate_content", generate_content)
    monkeypatch.setattr(adapter, "_client", FakeClient)
    if failures == 3:
        with pytest.raises(ProviderAdapterError) as raised:
            adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
        usage = raised.value.usage
    else:
        result = adapter.generate_structured(StructuredRequest(prompt="x"), SmokeReply)
        usage = result.provider_usage
    assert usage["dispatch_count"] == len(calls) == min(failures + 1, 3)
