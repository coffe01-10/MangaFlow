from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PortDataType = Literal["text", "json", "image", "asset", "report", "boolean"]
WorkflowScope = Literal["PROJECT", "CHAPTER", "PAGE", "CANDIDATE"]


class WorkflowPortDefinition(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    label: str = Field(min_length=1, max_length=120)
    data_type: PortDataType
    required: bool = True


class WorkflowNodeConfig(BaseModel):
    model_alias: str | None = Field(default=None, max_length=64)
    prompt_template: str = Field(default="", max_length=100_000)
    system_instruction: str = Field(default="", max_length=40_000)
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_seconds: int = Field(default=900, ge=30, le=3600)
    max_attempts: int = Field(default=3, ge=1, le=10)
    concurrency: int = Field(default=1, ge=1, le=8)
    resolution: Literal["1K", "2K", "4K"] | None = None
    locked: bool = False
    notes: str = Field(default="", max_length=20_000)
    condition: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False


class WorkflowNodeDefinition(BaseModel):
    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_-]+$")
    type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})
    inputs: list[WorkflowPortDefinition] = Field(default_factory=list)
    outputs: list[WorkflowPortDefinition] = Field(default_factory=list)
    config: WorkflowNodeConfig = Field(default_factory=WorkflowNodeConfig)


class WorkflowEdgeDefinition(BaseModel):
    id: str = Field(min_length=1, max_length=240)
    source_node: str = Field(min_length=1, max_length=120)
    source_port: str = Field(min_length=1, max_length=80)
    target_node: str = Field(min_length=1, max_length=120)
    target_port: str = Field(min_length=1, max_length=80)


class WorkflowGraph(BaseModel):
    schema_version: int = Field(default=2, ge=2, le=2)
    nodes: list[WorkflowNodeDefinition] = Field(default_factory=list, max_length=1000)
    edges: list[WorkflowEdgeDefinition] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def unique_ids(self) -> "WorkflowGraph":
        node_ids = [node.id for node in self.nodes]
        edge_ids = [edge.id for edge in self.edges]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("工作流节点 ID 不能重复")
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("工作流连线 ID 不能重复")
        return self


class WorkflowCreate(BaseModel):
    name: str = Field(default="默认漫画工作流", min_length=1, max_length=160)
    description: str = Field(default="", max_length=10_000)
    template: str = Field(
        default="manga_default",
        pattern=r"^(manga_default|chapter_export|blank)$",
    )


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=10_000)
    draft_graph: WorkflowGraph | None = None
    is_active: bool | None = None
    version: int = Field(ge=1)


class WorkflowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    description: str
    draft_graph: dict[str, Any]
    draft_version: int
    published_version_id: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    version: int


class WorkflowValidationIssue(BaseModel):
    severity: Literal["ERROR", "WARNING"]
    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None


class WorkflowValidationRead(BaseModel):
    valid: bool
    issues: list[WorkflowValidationIssue]
    topological_order: list[str]


class WorkflowVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    revision: int
    graph: dict[str, Any]
    graph_checksum: str
    validation_report: dict[str, Any]
    published_at: datetime


class WorkflowRunCreate(BaseModel):
    scope_type: WorkflowScope = "PROJECT"
    scope_id: str | None = None
    start_node_ids: list[str] = Field(default_factory=list)
    stop_node_ids: list[str] = Field(default_factory=list)


class WorkflowRestoreRequest(BaseModel):
    version: int = Field(ge=1)


class WorkflowNodeApproveRequest(BaseModel):
    candidate_id: str | None = None
    image_model_alias: str | None = Field(default=None, max_length=200)
    resolution: Literal["1K", "2K", "4K"] | None = None


class WorkflowImportRequest(BaseModel):
    name: str = Field(default="导入的工作流", min_length=1, max_length=160)
    description: str = Field(default="", max_length=10_000)
    graph: WorkflowGraph


class WorkflowNodeRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_run_id: str
    node_id: str
    node_type: str
    status: str
    job_id: str | None
    input_snapshot: dict[str, Any]
    output_refs: dict[str, Any]
    attempt_count: int
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None


class WorkflowRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    workflow_version_id: str
    project_id: str
    scope_type: str
    scope_id: str | None
    status: str
    start_node_ids: list[str]
    stop_node_ids: list[str]
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    version: int
    node_runs: list[WorkflowNodeRunRead] = Field(default_factory=list)


class WorkflowNodeTypeRead(BaseModel):
    type: str
    label: str
    category: str
    description: str
    inputs: list[WorkflowPortDefinition]
    outputs: list[WorkflowPortDefinition]
    configurable_fields: list[str]
