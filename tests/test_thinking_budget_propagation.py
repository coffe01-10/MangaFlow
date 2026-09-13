"""Regression: declared thinking_budget must reach every paid text/multimodal entry.

The cost-saving intent (``metadata={"max_output_tokens": 8192,
"thinking_budget": 0}``) was only implemented in vertex.generate_structured.
The Google-native entries and vertex.analyze_multimodal silently dropped it,
so Gemini 2.5's default dynamic thinking billed extra tokens on every
SOURCE_PARSE chunk and PAGE_INSPECT dispatch (issue #644).
"""

from pathlib import Path

from pydantic import BaseModel

from app.config import Settings
from app.model_adapters.base import MultimodalRequest, StructuredRequest
from app.model_adapters.google import GoogleRuntime, GoogleTextAdapter
from app.model_adapters.vertex import VertexTextAdapter
from app.services.model_registry import build_registry


class SmokeReply(BaseModel):
    ok: bool


class FakeResponse:
    text = '{"ok": true}'


class FakeModels:
    def __init__(self):
        self.last_kwargs = None

    def generate_content(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeResponse()


class FakeClient:
    def __init__(self, models: FakeModels):
        self.models = models


def _google_adapter() -> tuple[GoogleTextAdapter, FakeModels]:
    models = FakeModels()
    client = FakeClient(models)
    adapter = GoogleTextAdapter(
        GoogleRuntime(api_key="key", model_id="gemini-x", display_name="Gemini X")
    )
    adapter._client = lambda: client
    return adapter, models


def _vertex_adapter() -> tuple[VertexTextAdapter, FakeModels]:
    settings = Settings(
        google_cloud_project="test-project",
        google_application_credentials=Path(__file__),
    )
    models = FakeModels()
    client = FakeClient(models)
    adapter = VertexTextAdapter(settings, build_registry(settings)["text.fast"])
    adapter._client = lambda: client
    return adapter, models


def test_google_structured_entry_sends_thinking_config():
    adapter, models = _google_adapter()
    output = adapter.generate_structured(
        StructuredRequest(
            prompt="return ok",
            temperature=0,
            metadata={"max_output_tokens": 64, "thinking_budget": 0},
        ),
        SmokeReply,
    )
    assert output.ok is True
    assert models.last_kwargs["config"].thinking_config.thinking_budget == 0


def test_google_multimodal_entry_sends_thinking_config():
    adapter, models = _google_adapter()
    output = adapter.analyze_multimodal(
        MultimodalRequest(
            prompt="inspect page",
            images=(b"png-bytes",),
            mime_types=("image/png",),
            metadata={"max_output_tokens": 64, "thinking_budget": 0},
        ),
        SmokeReply,
    )
    assert output.ok is True
    assert models.last_kwargs["config"].thinking_config.thinking_budget == 0


def test_google_structured_entry_without_budget_leaves_config_unset():
    adapter, models = _google_adapter()
    adapter.generate_structured(
        StructuredRequest(prompt="return ok", temperature=0),
        SmokeReply,
    )
    assert models.last_kwargs["config"].thinking_config is None


def test_vertex_multimodal_entry_sends_thinking_config():
    adapter, models = _vertex_adapter()
    output = adapter.analyze_multimodal(
        MultimodalRequest(
            prompt="inspect page",
            images=(b"png-bytes",),
            mime_types=("image/png",),
            metadata={"max_output_tokens": 64, "thinking_budget": 0},
        ),
        SmokeReply,
    )
    assert output.ok is True
    assert models.last_kwargs["config"].thinking_config.thinking_budget == 0
