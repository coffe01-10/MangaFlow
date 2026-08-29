from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.workflow_schemas import (
    WorkflowGraph,
    WorkflowNodeTypeRead,
    WorkflowPortDefinition,
)


@dataclass(frozen=True)
class NodeTypeSpec:
    type: str
    label: str
    category: str
    description: str
    inputs: tuple[tuple[str, str, str, bool], ...]
    outputs: tuple[tuple[str, str, str, bool], ...]
    configurable_fields: tuple[str, ...] = ()
    model_family: str | None = None
    barrier: str | None = None


NODE_TYPES: tuple[NodeTypeSpec, ...] = (
    NodeTypeSpec(
        "source.chapter",
        "原作章节",
        "INPUT",
        "读取项目中的章节原文与不可变修订。",
        (),
        (("source", "原始文本", "text", False),),
        ("notes",),
    ),
    NodeTypeSpec(
        "source.approved_pages",
        "成品页面",
        "INPUT",
        "读取章节中全部已经通过质量检查的页面。",
        (),
        (("pages", "生产通过页面", "asset", False),),
        ("notes",),
    ),
    NodeTypeSpec(
        "source.assets",
        "参考资产",
        "INPUT",
        "读取角色、服装与漫画风格参考资产。",
        (),
        (("assets", "资产包", "asset", False),),
        ("notes",),
    ),
    NodeTypeSpec(
        "agent.parse",
        "剧情解析",
        "AGENT",
        "识别场景、角色、事实与来源区间。",
        (("source", "原始文本", "text", True),),
        (("story", "结构化剧情", "json", False),),
        (
            "model_alias",
            "prompt_template",
            "temperature",
            "timeout_seconds",
            "max_attempts",
            "notes",
        ),
        "text",
    ),
    NodeTypeSpec(
        "agent.adapt",
        "漫画改编",
        "AGENT",
        "逐片段生成完整漫画剧本，不压缩原文。",
        (("story", "结构化剧情", "json", True),),
        (("script", "漫画剧本", "json", False),),
        (
            "model_alias",
            "prompt_template",
            "temperature",
            "timeout_seconds",
            "max_attempts",
            "notes",
        ),
        "text",
    ),
    NodeTypeSpec(
        "director.storyboard",
        "分页与分镜",
        "AGENT",
        "动态分页并生成右至左分镜数据。",
        (("script", "漫画剧本", "json", True),),
        (("panels", "分页分镜", "json", False),),
        (
            "model_alias",
            "prompt_template",
            "temperature",
            "timeout_seconds",
            "max_attempts",
            "notes",
        ),
        "text",
    ),
    NodeTypeSpec(
        "control.condition",
        "条件分支",
        "CONTROL",
        "按安全 JSON 路径和预定义比较符选择分支。",
        (("value", "待判断数据", "json", True),),
        (("true", "满足条件", "json", False), ("false", "不满足条件", "json", False)),
        ("condition", "notes"),
    ),
    NodeTypeSpec(
        "control.merge",
        "合并",
        "CONTROL",
        "合并两个结构化输入。",
        (("left", "输入 A", "json", True), ("right", "输入 B", "json", True)),
        (("merged", "合并结果", "json", False),),
        ("notes",),
    ),
    NodeTypeSpec(
        "generator.page",
        "单页生成",
        "OUTPUT",
        "显式确认后只生成当前页的一个候选。",
        (("panels", "分页分镜", "json", True), ("assets", "参考资产", "asset", True)),
        (("page", "页面候选", "image", False),),
        ("model_alias", "resolution", "timeout_seconds", "max_attempts", "notes"),
        "image",
        "GENERATE",
    ),
    NodeTypeSpec(
        "control.approval",
        "采用候选",
        "CONTROL",
        "人工确认当前页采用版本后再继续。",
        (("page", "页面候选", "image", True),),
        (("approved", "采用页面", "image", False),),
        ("notes",),
        None,
        "APPROVE",
    ),
    NodeTypeSpec(
        "quality.inspect",
        "质量检查",
        "AGENT",
        "检查说话人归属、角色、服装、道具与连续性；文字由人工校对。",
        (("page", "采用页面", "image", True),),
        (("report", "检查报告", "report", False), ("approved", "通过页面", "image", False)),
        ("model_alias", "timeout_seconds", "max_attempts", "notes"),
        "text",
    ),
    NodeTypeSpec(
        "output.page",
        "单页成品",
        "OUTPUT",
        "确认当前页面已经通过质量检查并结束单页生产流程。",
        (("page", "通过页面", "image", True),),
        (("asset", "单页成品", "asset", False),),
        ("notes",),
    ),
    NodeTypeSpec(
        "output.export",
        "连续导出（兼容）",
        "OUTPUT",
        "兼容旧版单页流程；新流程请使用单页成品或整章导出。",
        (("page", "通过页面", "image", True),),
        (("files", "导出文件", "asset", False),),
        ("notes",),
    ),
    NodeTypeSpec(
        "output.chapter_export",
        "整章导出",
        "OUTPUT",
        "全部页面生产通过后输出整章 PNG、PDF、JSON 与素材清单。",
        (("pages", "生产通过页面", "asset", True),),
        (("files", "导出文件", "asset", False),),
        ("notes",),
    ),
)

NODE_TYPE_MAP = {item.type: item for item in NODE_TYPES}
CONDITION_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "exists"}


def _ports(items: tuple[tuple[str, str, str, bool], ...]) -> list[WorkflowPortDefinition]:
    return [
        WorkflowPortDefinition(id=item[0], label=item[1], data_type=item[2], required=item[3])
        for item in items
    ]


def node_type_catalog() -> list[WorkflowNodeTypeRead]:
    return [
        WorkflowNodeTypeRead(
            type=item.type,
            label=item.label,
            category=item.category,
            description=item.description,
            inputs=_ports(item.inputs),
            outputs=_ports(item.outputs),
            configurable_fields=list(item.configurable_fields),
        )
        for item in NODE_TYPES
    ]


def _node(node_id: str, node_type: str, name: str, x: float, y: float, **config: Any) -> dict:
    spec = NODE_TYPE_MAP[node_type]
    return {
        "id": node_id,
        "type": node_type,
        "name": name,
        "position": {"x": x, "y": y},
        "inputs": [port.model_dump() for port in _ports(spec.inputs)],
        "outputs": [port.model_dump() for port in _ports(spec.outputs)],
        "config": config,
    }


def _edge(source: str, source_port: str, target: str, target_port: str) -> dict:
    return {
        "id": f"{source}:{source_port}-{target}:{target_port}",
        "source_node": source,
        "source_port": source_port,
        "target_node": target,
        "target_port": target_port,
    }


def default_graph() -> dict:
    nodes = [
        _node("chapter", "source.chapter", "原作章节", 40, 180, notes="当前章节不可变修订"),
        _node("assets", "source.assets", "参考资产", 610, 430, notes="人物、服装、风格"),
        _node("parse", "agent.parse", "剧情解析", 330, 160, model_alias="text.fast"),
        _node("adapt", "agent.adapt", "漫画改编", 610, 160, model_alias="text.fast"),
        _node("storyboard", "director.storyboard", "分页与分镜", 890, 160, model_alias="text.fast"),
        _node(
            "generate",
            "generator.page",
            "单页生成",
            1180,
            250,
            model_alias=None,
            resolution="1K",
            requires_approval=True,
        ),
        _node("adopt", "control.approval", "采用候选", 1470, 250, requires_approval=True),
        _node("inspect", "quality.inspect", "质量检查", 1760, 250, model_alias="text.fast"),
        _node("complete", "output.page", "单页成品", 2050, 250),
    ]
    edges = [
        _edge("chapter", "source", "parse", "source"),
        _edge("parse", "story", "adapt", "story"),
        _edge("adapt", "script", "storyboard", "script"),
        _edge("storyboard", "panels", "generate", "panels"),
        _edge("assets", "assets", "generate", "assets"),
        _edge("generate", "page", "adopt", "page"),
        _edge("adopt", "approved", "inspect", "page"),
        _edge("inspect", "approved", "complete", "page"),
    ]
    return WorkflowGraph(nodes=nodes, edges=edges).model_dump(mode="json")


def chapter_export_graph() -> dict:
    nodes = [
        _node("pages", "source.approved_pages", "成品页面", 120, 220),
        _node("export", "output.chapter_export", "整章导出", 470, 220),
    ]
    edges = [_edge("pages", "pages", "export", "pages")]
    return WorkflowGraph(nodes=nodes, edges=edges).model_dump(mode="json")


def blank_graph() -> dict:
    return WorkflowGraph().model_dump(mode="json")


def canonical_graph(graph: WorkflowGraph | dict) -> dict:
    value = graph if isinstance(graph, WorkflowGraph) else WorkflowGraph.model_validate(graph)
    return value.model_dump(mode="json")


def graph_checksum(graph: WorkflowGraph | dict) -> str:
    payload = json.dumps(
        canonical_graph(graph), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
