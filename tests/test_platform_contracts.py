import pytest
from pydantic import ValidationError

from app.database import Base
from app.settings_schemas import RuntimeSettingsUpdate, VertexVerifyRequest
from app.workflow_schemas import WorkflowGraph


def test_platform_tables_are_registered():
    expected = {
        "workflow_definitions",
        "workflow_versions",
        "workflow_runs",
        "workflow_node_runs",
        "provider_health",
        "app_settings",
    }
    assert expected.issubset(Base.metadata.tables)


def test_workflow_graph_requires_unique_ids():
    with pytest.raises(ValidationError, match="工作流节点 ID 不能重复"):
        WorkflowGraph.model_validate(
            {
                "schema_version": 2,
                "nodes": [
                    {"id": "source", "type": "source.chapter", "name": "原作"},
                    {"id": "source", "type": "source.chapter", "name": "重复"},
                ],
                "edges": [],
            }
        )


def test_runtime_and_vertex_contracts_reject_unsafe_values():
    with pytest.raises(ValidationError):
        RuntimeSettingsUpdate(job_timeout_seconds=5, version=1)
    with pytest.raises(ValidationError):
        VertexVerifyRequest(level="IMAGE_MODEL", image_model_alias="image.unknown")
