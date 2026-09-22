"""Editor presets. Execution still uses the published graph and existing barriers."""

from app.services.workflow_engine.catalog import default_graph
from app.workflow_schemas import WorkflowGraph


def studio_template(kind: str) -> dict:
    graph = default_graph()
    graph["groups"] = [
        {
            "id": "preparation",
            "name": "原作与分镜准备",
            "node_ids": ["chapter", "parse", "adapt", "storyboard", "assets"],
            "color": "#397b68",
            "notes": "从当前页面已有的分镜与参考资产继续。需要重新准备时，展开后选节点运行。",
            "collapsed": True,
        },
        {
            "id": "review",
            "name": "检查修复",
            "node_ids": ["adopt", "inspect", "complete"],
            "color": "#a8864a",
            "notes": "确认已有候选后检查。未通过时前往生成与修复页，修复并采用后重新运行检查。",
            "collapsed": False,
        },
    ]
    if kind == "check":
        graph["entry_node_ids"] = ["adopt"]
        graph["nodes"] = [
            {**node, "config": {**node["config"], "notes": graph["groups"][1]["notes"]}}
            if node["id"] == "inspect" else node
            for node in graph["nodes"]
        ]
    elif kind == "batch":
        graph["run_mode"] = "batch"
        graph["entry_node_ids"] = ["generate"]
    else:
        raise ValueError("未知流程模板")
    return WorkflowGraph.model_validate(graph).model_dump(mode="json")
