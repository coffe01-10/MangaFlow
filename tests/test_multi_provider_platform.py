import base64
import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.domain.states import JobStatus
from app.model_adapters.base import StructuredRequest
from app.model_adapters.compatible import (
    AnthropicCompatibleAdapter,
    CompatibleRuntime,
    OpenAICompatibleAdapter,
)
from app.models import (
    AIModel,
    Asset,
    GenerationJob,
    JobAssetReference,
    Project,
    ProviderConnection,
    ProviderKey,
    ProviderProfile,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
)
from app.services.credential_crypto import (
    decrypt_secret,
    mark_key_failure,
    select_provider_key,
)
from app.services.job_service import cancel_job
from app.services.model_router import resolve_model


class SmokeResult(BaseModel):
    ok: bool


def test_presets_seed_default_provider_catalog(client):
    response = client.get("/api/v1/providers")

    assert response.status_code == 200
    providers = response.json()
    keys = {item["preset_key"] for item in providers}
    assert {
        "vertex-ai",
        "openai",
        "anthropic",
        "deepseek",
        "openrouter",
        "opencode-zen",
        "siliconflow",
        "volcengine-ark",
        "zhipu",
        "dashscope",
    } <= keys
    assert len(providers) >= 20
    assert all(
        connection["protocol"] in {
            "OPENAI",
            "ANTHROPIC",
            "VERTEX_NATIVE",
            "GOOGLE_NATIVE",
        }
        for provider in providers
        for connection in provider["connections"]
    )


def test_custom_provider_key_is_encrypted_and_never_returned(
    client, db_session, monkeypatch
):
    settings = get_settings()
    master_key = base64.urlsafe_b64encode(b"m" * 32).decode("ascii")
    monkeypatch.setattr(settings, "mangaflow_credential_master_key", master_key)
    created = client.post(
        "/api/v1/providers",
        json={
            "name": "自建网关",
            "protocol": "OPENAI",
            "base_url": "https://gateway.example.com/v1",
            "use_responses_api": True,
        },
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["connections"][0]["id"]

    saved = client.put(
        f"/api/v1/providers/connections/{connection_id}/keys",
        json={"label": "primary", "api_key": "sk-provider-secret-value"},
    )

    assert saved.status_code == 201, saved.text
    assert "secret-value" not in saved.text
    row = db_session.query(ProviderKey).filter_by(connection_id=connection_id).one()
    assert "sk-provider-secret-value" not in row.encrypted_secret
    assert decrypt_secret(settings, row.encrypted_secret) == "sk-provider-secret-value"
    listed = client.get("/api/v1/providers")
    assert "sk-provider-secret-value" not in listed.text
    assert "encrypted_secret" not in listed.text


def test_multi_key_rotation_skips_rate_limited_key(client, db_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "mangaflow_credential_master_key",
        base64.urlsafe_b64encode(b"r" * 32).decode("ascii"),
    )
    created = client.post(
        "/api/v1/providers",
        json={
            "name": "轮换网关",
            "protocol": "ANTHROPIC",
            "base_url": "https://rotate.example.com/v1",
        },
    ).json()
    connection_id = created["connections"][0]["id"]
    for label in ("first", "second"):
        response = client.put(
            f"/api/v1/providers/connections/{connection_id}/keys",
            json={"label": label, "api_key": f"{label}-secret"},
        )
        assert response.status_code == 201

    first = select_provider_key(db_session, settings, connection_id)
    mark_key_failure(db_session, first.row, "RATE_LIMIT", retry_after_seconds=120)
    second = select_provider_key(db_session, settings, connection_id)

    assert second.row.id != first.row.id
    assert second.secret.endswith("-secret")


def test_custom_provider_rejects_insecure_public_http(client):
    response = client.post(
        "/api/v1/providers",
        json={
            "name": "不安全网关",
            "protocol": "OPENAI",
            "base_url": "http://gateway.example.com/v1",
        },
    )

    assert response.status_code == 422
    assert "HTTPS" in response.text


def test_openai_and_anthropic_protocol_adapters_emit_expected_requests():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                json={"content": [{"type": "text", "text": '{"ok": true}'}]},
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    openai = OpenAICompatibleAdapter(
        CompatibleRuntime(
            provider_name="OpenAI-compatible",
            protocol="OPENAI",
            base_url="https://openai.example.com/v1",
            api_key="openai-key",
            model_id="text-model",
            endpoint_templates={"chat": "/chat/completions"},
        ),
        client=client,
    )
    anthropic = AnthropicCompatibleAdapter(
        CompatibleRuntime(
            provider_name="Anthropic-compatible",
            protocol="ANTHROPIC",
            base_url="https://anthropic.example.com/v1",
            api_key="anthropic-key",
            model_id="claude-model",
            endpoint_templates={"messages": "/messages"},
        ),
        client=client,
    )

    assert openai.generate_structured(StructuredRequest(prompt="test"), SmokeResult).ok
    assert anthropic.generate_structured(StructuredRequest(prompt="test"), SmokeResult).ok
    openai_body = json.loads(requests[0].content)
    anthropic_body = json.loads(requests[1].content)
    assert openai_body["model"] == "text-model"
    assert requests[0].headers["authorization"] == "Bearer openai-key"
    assert anthropic_body["model"] == "claude-model"
    assert requests[1].headers["x-api-key"] == "anthropic-key"
    assert requests[1].headers["anthropic-version"] == "2023-06-01"
    client.close()


def test_auto_routing_only_uses_verified_models(db_session):
    profile = ProviderProfile(
        name="测试原生供应商",
        category="CUSTOM",
        enabled=True,
    )
    db_session.add(profile)
    db_session.flush()
    connection = ProviderConnection(
        provider_id=profile.id,
        name="测试连接",
        protocol="VERTEX_NATIVE",
        base_url="vertex://test",
        enabled=True,
        health_state="HEALTHY",
    )
    db_session.add(connection)
    db_session.flush()
    inferred = AIModel(
        connection_id=connection.id,
        provider_model_id="inferred-image",
        display_name="推断图片模型",
        model_type="IMAGE",
        input_modalities=["TEXT", "IMAGE"],
        output_modalities=["IMAGE"],
        operations=["image_generate", "image_edit"],
        confidence="INFERRED",
        priority=100,
    )
    verified = AIModel(
        connection_id=connection.id,
        provider_model_id="verified-image",
        display_name="已验证图片模型",
        model_type="IMAGE",
        input_modalities=["TEXT", "IMAGE"],
        output_modalities=["IMAGE"],
        operations=["image_generate", "image_edit"],
        confidence="VERIFIED",
        priority=10,
        last_verified_at=datetime.now(UTC),
    )
    db_session.add_all([inferred, verified])
    db_session.commit()

    resolved = resolve_model(
        db_session,
        get_settings(),
        operation="image_edit",
        explicit_reference="auto",
        task_kind="PAGE_GENERATE",
    )

    assert resolved.model.id == verified.id
    assert resolved.route_reason == "AUTO"


def test_model_availability_requires_an_enabled_provider_key(
    client, db_session, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "mangaflow_credential_master_key",
        base64.urlsafe_b64encode(b"a" * 32).decode("ascii"),
    )
    created = client.post(
        "/api/v1/providers",
        json={
            "name": "可用性网关",
            "protocol": "OPENAI",
            "base_url": "https://availability.example.com/v1",
        },
    ).json()
    connection_id = created["connections"][0]["id"]
    model = client.post(
        f"/api/v1/providers/connections/{connection_id}/models",
        json={
            "provider_model_id": "text-model",
            "model_type": "TEXT",
            "operations": ["structured_text"],
        },
    ).json()

    listed = client.get("/api/v1/models").json()
    assert next(item for item in listed if item["catalog_id"] == model["id"])[
        "enabled"
    ] is False

    saved = client.put(
        f"/api/v1/providers/connections/{connection_id}/keys",
        json={"label": "default", "api_key": "available-key"},
    )
    assert saved.status_code == 201
    listed = client.get("/api/v1/models").json()
    assert next(item for item in listed if item["catalog_id"] == model["id"])[
        "enabled"
    ] is True

    key = db_session.query(ProviderKey).filter_by(connection_id=connection_id).one()
    mark_key_failure(db_session, key, "AUTHENTICATION")
    with pytest.raises(HTTPException, match="API Key"):
        resolve_model(
            db_session,
            settings,
            operation="structured_text",
            explicit_reference=model["id"],
        )
    listed = client.get("/api/v1/models").json()
    assert next(item for item in listed if item["catalog_id"] == model["id"])[
        "enabled"
    ] is False


def test_custom_provider_deletion_blocks_retryable_jobs(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "供应商删除保护"}).json()
    created = client.post(
        "/api/v1/providers",
        json={
            "name": "待删除网关",
            "protocol": "ANTHROPIC",
            "base_url": "https://delete-guard.example.com/v1",
        },
    ).json()
    provider_id = created["id"]
    connection_id = created["connections"][0]["id"]
    model = AIModel(
        connection_id=connection_id,
        provider_model_id="retryable-model",
        display_name="Retryable Model",
        model_type="TEXT",
        operations=["structured_text"],
    )
    db_session.add(model)
    db_session.flush()
    job = GenerationJob(
        project_id=project["id"],
        target_type="PROJECT",
        target_id=project["id"],
        job_type="SCRIPT_PARSE",
        status=JobStatus.FAILED,
        catalog_model_id=model.id,
    )
    db_session.add(job)
    db_session.commit()

    blocked = client.delete(f"/api/v1/providers/{provider_id}")
    assert blocked.status_code == 409
    assert "可重试" in blocked.text

    job.status = JobStatus.CANCELLED
    db_session.commit()
    deleted = client.delete(f"/api/v1/providers/{provider_id}")
    assert deleted.status_code == 204


def test_active_job_reference_blocks_asset_deletion(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "引用租约"}).json()
    asset = Asset(
        project_id=project["id"],
        kind="STYLE_REFERENCE",
        original_name="style.png",
        storage_key="style.png",
        mime_type="image/png",
        byte_size=1,
        sha256="a" * 64,
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    db_session.add(asset)
    db_session.flush()
    job = GenerationJob(
        project_id=project["id"],
        target_type="STYLE",
        target_id="style-id",
        job_type="STYLE_ANALYZE",
        status=JobStatus.QUEUED,
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(JobAssetReference(job_id=job.id, asset_id=asset.id))
    db_session.commit()

    blocked = client.delete(f"/api/v1/assets/{asset.id}")
    assert blocked.status_code == 409
    assert "生成任务" in blocked.text

    job.status = JobStatus.COMPLETED
    db_session.commit()
    deleted = client.delete(f"/api/v1/assets/{asset.id}")
    assert deleted.status_code == 204


def test_cancelling_completed_workflow_job_does_not_cancel_run(db_session):
    project = Project(name="完成态保护")
    db_session.add(project)
    db_session.flush()
    workflow = WorkflowDefinition(
        project_id=project.id,
        name="完成态保护",
        draft_graph={"schema_version": 2, "nodes": [], "edges": []},
    )
    db_session.add(workflow)
    db_session.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=workflow.draft_graph,
        graph_checksum="b" * 64,
        validation_report={},
    )
    db_session.add(version)
    db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PROJECT",
        status="RUNNING",
    )
    db_session.add(run)
    db_session.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="WORKFLOW_NODE",
        target_id="node-run",
        job_type="WORKFLOW_NODE",
        status=JobStatus.COMPLETED,
    )
    db_session.add(job)
    db_session.flush()
    node_run = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="done",
        node_type="control.merge",
        status="COMPLETED",
        job_id=job.id,
    )
    db_session.add(node_run)
    db_session.commit()

    returned = cancel_job(db_session, job)

    assert returned.status == JobStatus.COMPLETED
    assert run.status == "RUNNING"
    assert node_run.status == "COMPLETED"
