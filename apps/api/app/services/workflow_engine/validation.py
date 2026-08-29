from __future__ import annotations

from collections import defaultdict, deque

from app.services.workflow_engine.catalog import CONDITION_OPERATORS, NODE_TYPE_MAP
from app.workflow_schemas import (
    WorkflowGraph,
    WorkflowValidationIssue,
    WorkflowValidationRead,
)


def validate_graph(graph_value: WorkflowGraph | dict) -> WorkflowValidationRead:
    graph = (
        graph_value
        if isinstance(graph_value, WorkflowGraph)
        else WorkflowGraph.model_validate(graph_value)
    )
    issues: list[WorkflowValidationIssue] = []
    nodes = {node.id: node for node in graph.nodes}
    inbound: dict[str, list] = defaultdict(list)
    outbound: dict[str, list] = defaultdict(list)
    indegree = {node.id: 0 for node in graph.nodes}
    seen_targets: set[tuple[str, str]] = set()

    if not nodes:
        issues.append(
            WorkflowValidationIssue(
                severity="ERROR", code="EMPTY_GRAPH", message="工作流至少需要一个节点"
            )
        )

    for node in graph.nodes:
        spec = NODE_TYPE_MAP.get(node.type)
        if not spec:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="UNKNOWN_NODE_TYPE",
                    message=f"不支持的节点类型：{node.type}",
                    node_id=node.id,
                )
            )
            continue
        declared_inputs = {item.id: item for item in node.inputs}
        declared_outputs = {item.id: item for item in node.outputs}
        expected_inputs = {item[0]: item[2] for item in spec.inputs}
        expected_outputs = {item[0]: item[2] for item in spec.outputs}
        if {key: item.data_type for key, item in declared_inputs.items()} != expected_inputs or {
            key: item.data_type for key, item in declared_outputs.items()
        } != expected_outputs:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="PORT_SCHEMA_MISMATCH",
                    message="节点端口与节点类型目录不一致",
                    node_id=node.id,
                )
            )
        alias = node.config.model_alias
        if spec.model_family == "text" and not alias:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="TEXT_MODEL_REQUIRED",
                    message="该节点必须选择文字模型",
                    node_id=node.id,
                )
            )
        if spec.model_family == "image" and node.config.resolution not in {"1K", "2K", "4K"}:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="RESOLUTION_REQUIRED",
                    message="图片节点必须选择 1K、2K 或 4K",
                    node_id=node.id,
                )
            )
        if node.type == "control.condition":
            condition = node.config.condition
            if condition.get("operator") not in CONDITION_OPERATORS or not isinstance(
                condition.get("path"), str
            ):
                issues.append(
                    WorkflowValidationIssue(
                        severity="ERROR",
                        code="INVALID_CONDITION",
                        message="条件仅支持安全 JSON 路径和预定义比较符",
                        node_id=node.id,
                    )
                )

    for edge in graph.edges:
        source = nodes.get(edge.source_node)
        target = nodes.get(edge.target_node)
        if not source or not target:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="DANGLING_EDGE",
                    message="连线引用了不存在的节点",
                    edge_id=edge.id,
                )
            )
            continue
        source_port = next((item for item in source.outputs if item.id == edge.source_port), None)
        target_port = next((item for item in target.inputs if item.id == edge.target_port), None)
        if not source_port or not target_port:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="UNKNOWN_PORT",
                    message="连线引用了不存在的端口",
                    edge_id=edge.id,
                )
            )
            continue
        if source_port.data_type != target_port.data_type:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="PORT_TYPE_MISMATCH",
                    message=f"端口类型不匹配：{source_port.data_type} → {target_port.data_type}",
                    edge_id=edge.id,
                )
            )
        target_key = (edge.target_node, edge.target_port)
        if target_key in seen_targets:
            issues.append(
                WorkflowValidationIssue(
                    severity="ERROR",
                    code="MULTIPLE_INPUTS",
                    message="同一输入端口只能连接一条边",
                    edge_id=edge.id,
                )
            )
        seen_targets.add(target_key)
        inbound[edge.target_node].append(edge)
        outbound[edge.source_node].append(edge)
        indegree[edge.target_node] += 1

    for node in graph.nodes:
        connected = {edge.target_port for edge in inbound[node.id]}
        for port in node.inputs:
            if port.required and port.id not in connected:
                issues.append(
                    WorkflowValidationIssue(
                        severity="ERROR",
                        code="MISSING_REQUIRED_INPUT",
                        message=f"必需输入“{port.label}”尚未连接",
                        node_id=node.id,
                    )
                )

    ready = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while ready:
        node_id = ready.popleft()
        order.append(node_id)
        for edge in outbound[node_id]:
            indegree[edge.target_node] -= 1
            if indegree[edge.target_node] == 0:
                ready.append(edge.target_node)
    if len(order) != len(nodes):
        issues.append(
            WorkflowValidationIssue(
                severity="ERROR", code="CYCLE_DETECTED", message="工作流不允许形成循环"
            )
        )
        order = []
    return WorkflowValidationRead(
        valid=not any(item.severity == "ERROR" for item in issues),
        issues=issues,
        topological_order=order,
    )
