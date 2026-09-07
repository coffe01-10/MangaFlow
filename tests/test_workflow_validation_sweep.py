"""Red Team issue-sweep regressions (#197 #223 #224 #225 #226 #237 #246).

One file per the sweep plan; each test pins one verified defect fixed in this
pass:

- #197 delete_workflow vs start-run TOCTOU (deleted_at re-check after the
  definition lock; delete's tombstone + version bump as one atomic claim);
- #223 the APPROVE barrier / inspection minting re-validate candidate
  currency (STALE_CANDIDATE_CONFIRMATION_REQUIRED semantics) and reconcile
  cancels runs of soft-deleted definitions instead of paying for them;
- #224 condition nodes: publish-time validation requires the comparison
  value, the executor answers False instead of raising TypeError, and the
  studio inspector exposes a value input (schema/TSX halves pinned here);
- #225 NaN/Infinity: wire-level 422, strict DB JSON serializer, prompt
  compiler allow_nan=False and recursive schema validators;
- #226 empty model-id strings are 422, whitespace-only names are 422,
  archive confirm compares stripped-to-stripped, optional version tokens on
  the update schemas;
- #237 delete_script re-checks active jobs after taking the chapter/page
  locks instead of orphaning jobs committed inside the old window;
- #246 RepairRequest carries the UpscaleRequest-style resolution floor.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.database import _strict_json_serializer
from app.domain.states import JobStatus, Resolution
from app.models import (
    Asset,
    Chapter,
    GenerationBatch,
    GenerationJob,
    InspectionResult,
    MangaPage,
    PageCandidate,
    Project,
    Scene,
    ScriptRevision,
    SourceRevision,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from app.request_limits import json_contains_non_finite_literals
from app.schemas import (
    AssetUpdate,
    DialogueCreate,
    FavoriteUpdate,
    OutfitCreate,
    PanelUpdate,
    RepairRequest,
    SceneOutfitUpdate,
    StyleProfileCreate,
)
from app.services.workflow_engine import (
    approve_node,
    create_workflow_run,
    default_graph,
    publish_workflow,
    reconcile_run,
    validate_graph,
)
from app.services.workflow_engine.execution import _condition_matches
from app.services.workflow_engine.reconciliation import (
    StaleCandidateError,
    _create_inspection_job,
)
from app.workflow_schemas import WorkflowGraph, WorkflowNodeConfig

# --------------------------------------------------------------------- #197


def _chapter_export_workflow(db, project_id: str, name: str) -> WorkflowDefinition:
    from app.services.workflow_engine import chapter_export_graph

    workflow = WorkflowDefinition(
        project_id=project_id, name=name, draft_graph=chapter_export_graph()
    )
    db.add(workflow)
    db.flush()
    publish_workflow(db, workflow)
    db.commit()
    return workflow


def test_start_run_rejects_soft_deleted_workflow_after_lock(db_session, monkeypatch):
    """#197: create_workflow_run re-reads deleted_at after lock_entity, so a
    delete committing between the route's read and the run insert is refused
    instead of minting paid jobs for a deleted definition."""

    monkeypatch.setattr(
        "app.services.workflow_engine.enqueue_job", lambda db, job: job
    )
    project = Project(name="启动删除竞态")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, ordinal=1, title="第一章")
    db_session.add(chapter)
    db_session.commit()
    workflow = _chapter_export_workflow(db_session, project.id, "导出流程")
    created = create_workflow_run(
        db_session,
        workflow,
        scope_type="CHAPTER",
        scope_id=chapter.id,
        start_node_ids=[],
        stop_node_ids=[],
    )
    assert created.status in {"RUNNING", "PAUSED", "COMPLETED"}

    # Simulate the race loser: the workflow was soft-deleted AFTER the caller
    # read it (the route's _workflow guard already passed on the stale row).
    workflow.deleted_at = utcnow()
    db_session.commit()
    with pytest.raises(ValueError, match="已删除"):
        create_workflow_run(
            db_session,
            workflow,
            scope_type="CHAPTER",
            scope_id=chapter.id,
            start_node_ids=[],
            stop_node_ids=[],
        )


def test_delete_workflow_tombstones_and_bumps_version_atomically(
    client, db_session
):
    """#197: the soft delete is a single conditional UPDATE — the version bump
    cannot be lost against a concurrent PATCH, and a repeated delete is a 404
    no-op instead of a second bump."""

    project = client.post("/api/v1/projects", json={"name": "删除原子性"}).json()
    workflow = client.post(
        f"/api/v1/projects/{project['id']}/workflows", json={"name": "流程"}
    ).json()

    first = client.delete(f"/api/v1/workflows/{workflow['id']}")
    assert first.status_code == 204, first.text

    db_session.expire_all()
    row = db_session.get(WorkflowDefinition, workflow["id"])
    assert row.deleted_at is not None
    assert row.is_active is False
    assert row.version == workflow["version"] + 1

    assert client.delete(f"/api/v1/workflows/{workflow['id']}").status_code == 404


def test_delete_workflow_refuses_while_run_active(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "删除运行中"}).json()
    workflow = client.post(
        f"/api/v1/projects/{project['id']}/workflows", json={"name": "流程"}
    ).json()
    version = WorkflowVersion(
        workflow_id=workflow["id"],
        revision=1,
        graph={},
        graph_checksum="e" * 64,
        validation_report={"valid": True},
    )
    db_session.add(version)
    db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow["id"],
        workflow_version_id=version.id,
        project_id=project["id"],
        scope_type="PROJECT",
        status="RUNNING",
    )
    db_session.add(run)
    db_session.commit()

    response = client.delete(f"/api/v1/workflows/{workflow['id']}")
    assert response.status_code == 409, response.text

    db_session.expire_all()
    assert db_session.get(WorkflowDefinition, workflow["id"]).deleted_at is None


# --------------------------------------------------------------------- #223


def _seed_page_with_candidate(
    db,
    *,
    storyboard_version: int,
    based_on: int | None,
    ack_version: int | None,
    name: str,
) -> dict:
    project = Project(name=name)
    db.add(project)
    db.flush()
    chapter = Chapter(
        project_id=project.id, ordinal=1, title="第一章", status="PAGES_PLANNED"
    )
    db.add(chapter)
    db.flush()
    batch = GenerationBatch(project_id=project.id, chapter_id=chapter.id, ordinal=1)
    db.add(batch)
    db.flush()
    asset = Asset(
        project_id=project.id,
        kind="PAGE_IMAGE",
        original_name="page.png",
        storage_key="generated/sweep-page.png",
        mime_type="image/png",
        byte_size=8,
        sha256="c" * 64,
    )
    db.add(asset)
    db.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=storyboard_version,
        selected_candidate_ack_version=ack_version,
    )
    db.add(page)
    db.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.STANDARD_2K,
        status="READY",
        asset_id=asset.id,
        is_selected=True,
        based_on_storyboard_version=based_on,
        prompt_snapshot={"reference_selections": {}},
    )
    db.add(candidate)
    db.flush()
    page.selected_candidate_id = candidate.id
    db.commit()
    return {"project": project, "page": page, "candidate": candidate}


def _run_at_barrier(
    db,
    project: Project,
    page: MangaPage,
    *,
    run_status: str = "PAUSED",
    generate_status: str = "WAITING_APPROVAL",
    name: str = "闸门运行",
) -> tuple[WorkflowRun, WorkflowNodeRun]:
    """A default-graph PAGE run whose adopt barrier is WAITING_APPROVAL."""

    workflow = WorkflowDefinition(
        project_id=project.id, name=name, draft_graph=default_graph()
    )
    db.add(workflow)
    db.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        revision=1,
        graph=default_graph(),
        graph_checksum="d" * 64,
        validation_report={"valid": True},
    )
    db.add(version)
    db.flush()
    workflow.published_version_id = version.id
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        project_id=project.id,
        scope_type="PAGE",
        scope_id=page.id,
        status=run_status,
    )
    db.add(run)
    db.flush()
    generate = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="generate",
        node_type="generator.page",
        status=generate_status,
    )
    adopt = WorkflowNodeRun(
        workflow_run_id=run.id,
        node_id="adopt",
        node_type="control.approval",
        status="WAITING_APPROVAL",
    )
    inspect = WorkflowNodeRun(
        workflow_run_id=run.id, node_id="inspect", node_type="quality.inspect"
    )
    db.add_all([generate, adopt, inspect])
    db.commit()
    return run, adopt


def test_approve_barrier_rejects_stale_candidate(db_session):
    """#223: a storyboard edit that nulled the ack marker must block the
    barrier with the manual path's STALE_CANDIDATE_CONFIRMATION_REQUIRED
    semantics instead of consuming the stale candidate."""

    seeded = _seed_page_with_candidate(
        db_session,
        storyboard_version=2,
        based_on=1,
        ack_version=None,
        name="过期采用",
    )
    run, adopt = _run_at_barrier(db_session, seeded["project"], seeded["page"])

    with pytest.raises(ValueError, match="STALE_CANDIDATE_CONFIRMATION_REQUIRED"):
        approve_node(db_session, run.id, "adopt")

    db_session.expire_all()
    assert db_session.get(WorkflowNodeRun, adopt.id).status == "WAITING_APPROVAL"
    assert db_session.get(WorkflowRun, run.id).status == "PAUSED"


def test_approve_barrier_rejects_unconfirmed_legacy_candidate(db_session):
    """#223: a candidate with unknown generation version and no explicit
    keep-selected acknowledgement cannot pass the barrier either."""

    seeded = _seed_page_with_candidate(
        db_session,
        storyboard_version=2,
        based_on=None,
        ack_version=None,
        name="旧版候选",
    )
    run, _ = _run_at_barrier(db_session, seeded["project"], seeded["page"])

    with pytest.raises(ValueError, match="STALE_CANDIDATE_CONFIRMATION_REQUIRED"):
        approve_node(db_session, run.id, "adopt")


def test_approve_barrier_rejects_severe_inspection_blocker(db_session):
    """#223: severity blockers gate the barrier like the manual adopt path."""

    seeded = _seed_page_with_candidate(
        db_session,
        storyboard_version=2,
        based_on=2,
        ack_version=2,
        name="严重问题",
    )
    run, _ = _run_at_barrier(db_session, seeded["project"], seeded["page"])
    db_session.add(
        InspectionResult(
            candidate_id=seeded["candidate"].id,
            storyboard_version=2,
            category="CHARACTER",
            outcome="MISMATCH",
            severity="CRITICAL",
        )
    )
    db_session.commit()

    with pytest.raises(ValueError, match="SEVERE_CHARACTER_ISSUE"):
        approve_node(db_session, run.id, "adopt")


@pytest.mark.parametrize(
    ("storyboard_version", "based_on", "ack_version"),
    [
        (2, 2, 2),  # CURRENT
        (2, 2, None),  # CURRENT without an explicit ack marker
        (2, 1, 2),  # STALE_ACCEPTED via keep-selected (manual_text_confirmed)
    ],
    ids=["current", "current-no-ack", "stale-acknowledged"],
)
def test_approve_barrier_accepts_confirmed_candidates(
    db_session, monkeypatch, storyboard_version, based_on, ack_version
):
    """#223: CURRENT and explicitly acknowledged stale candidates pass."""

    monkeypatch.setattr(
        "app.services.workflow_engine.enqueue_job", lambda db, job: job
    )
    seeded = _seed_page_with_candidate(
        db_session,
        storyboard_version=storyboard_version,
        based_on=based_on,
        ack_version=ack_version,
        name="合法采用",
    )
    run, adopt = _run_at_barrier(db_session, seeded["project"], seeded["page"])

    result = approve_node(db_session, run.id, "adopt")

    db_session.expire_all()
    node = db_session.get(WorkflowNodeRun, adopt.id)
    assert node.status == "COMPLETED"
    assert node.output_refs["candidate_id"] == seeded["candidate"].id
    assert result.status in {"PAUSED", "RUNNING"}


def _inspection_graph_and_run(db, seeded, *, generate_status="COMPLETED"):
    run, _ = _run_at_barrier(
        db,
        seeded["project"],
        seeded["page"],
        run_status="RUNNING",
        generate_status=generate_status,
        name="检查运行",
    )
    adopt = db.scalar(
        select(WorkflowNodeRun).where(
            WorkflowNodeRun.workflow_run_id == run.id,
            WorkflowNodeRun.node_id == "adopt",
        )
    )
    adopt.status = "COMPLETED"
    adopt.output_refs = {"candidate_id": seeded["candidate"].id}
    inspect_node = db.scalar(
        select(WorkflowNodeRun).where(
            WorkflowNodeRun.workflow_run_id == run.id,
            WorkflowNodeRun.node_id == "inspect",
        )
    )
    db.commit()
    graph = WorkflowGraph.model_validate(default_graph())
    node = next(item for item in graph.nodes if item.id == "inspect")
    node_runs = list(
        db.scalars(
            select(WorkflowNodeRun).where(WorkflowNodeRun.workflow_run_id == run.id)
        )
    )
    return run, graph, node, inspect_node, node_runs


def test_inspection_minting_refuses_stale_candidate(db_session):
    """#223: the paid PAGE_INSPECT is not minted for a stale candidate — the
    gate raises before create_job, so no spend can happen."""

    seeded = _seed_page_with_candidate(
        db_session,
        storyboard_version=3,
        based_on=2,
        ack_version=None,
        name="过期检查",
    )
    run, graph, node, inspect_node, node_runs = _inspection_graph_and_run(
        db_session, seeded
    )

    with pytest.raises(StaleCandidateError):
        _create_inspection_job(db_session, run, graph, node, inspect_node, node_runs)

    assert (
        db_session.scalar(
            select(GenerationJob.id).where(GenerationJob.job_type == "PAGE_INSPECT")
        )
        is None
    )


def test_reconcile_fails_inspect_node_terminal_on_stale_candidate(db_session):
    """#223: through reconcile_run the same gate fails the node terminally
    with the STALE_CANDIDATE error code instead of spending."""

    seeded = _seed_page_with_candidate(
        db_session,
        storyboard_version=3,
        based_on=2,
        ack_version=None,
        name="过期收敛",
    )
    run, _, _, inspect_node, _ = _inspection_graph_and_run(db_session, seeded)

    result = reconcile_run(db_session, run.id)

    assert result.status == "FAILED"
    db_session.expire_all()
    node = db_session.get(WorkflowNodeRun, inspect_node.id)
    assert node.status == "FAILED"
    assert node.error_code == "STALE_CANDIDATE"
    assert "STALE_CANDIDATE_CONFIRMATION_REQUIRED" in (node.error_message or "")
    assert (
        db_session.scalar(
            select(GenerationJob.id).where(GenerationJob.job_type == "PAGE_INSPECT")
        )
        is None
    )


def test_reconcile_cancels_run_of_deleted_workflow(db_session):
    """#211-7/#223: reconcile must skip-and-mark runs of soft-deleted
    definitions (CANCELLED claim + child sweep), not keep paying for them."""

    seeded = _seed_page_with_candidate(
        db_session,
        storyboard_version=2,
        based_on=2,
        ack_version=2,
        name="已删工作流",
    )
    run, _ = _run_at_barrier(
        db_session, seeded["project"], seeded["page"], run_status="RUNNING"
    )
    workflow = db_session.get(WorkflowDefinition, run.workflow_id)
    workflow.deleted_at = utcnow()
    db_session.commit()

    result = reconcile_run(db_session, run.id)

    assert result.status == "CANCELLED"
    db_session.expire_all()
    statuses = {
        item.node_id: item.status
        for item in db_session.scalars(
            select(WorkflowNodeRun).where(WorkflowNodeRun.workflow_run_id == run.id)
        )
    }
    assert set(statuses.values()) == {"CANCELLED"}


# --------------------------------------------------------------------- #224


def _condition_graph(condition: dict) -> WorkflowGraph:
    return WorkflowGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "src",
                    "type": "source.chapter",
                    "name": "原作",
                    "position": {"x": 0, "y": 0},
                    "inputs": [],
                    "outputs": [
                        {
                            "id": "source",
                            "label": "原始文本",
                            "data_type": "text",
                            "required": False,
                        }
                    ],
                    "config": {},
                },
                {
                    "id": "parse",
                    "type": "agent.parse",
                    "name": "解析",
                    "position": {"x": 300, "y": 0},
                    "inputs": [
                        {
                            "id": "source",
                            "label": "原始文本",
                            "data_type": "text",
                            "required": True,
                        }
                    ],
                    "outputs": [
                        {
                            "id": "story",
                            "label": "结构化剧情",
                            "data_type": "json",
                            "required": False,
                        }
                    ],
                    "config": {"model_alias": "auto"},
                },
                {
                    "id": "cond",
                    "type": "control.condition",
                    "name": "条件",
                    "position": {"x": 600, "y": 0},
                    "inputs": [
                        {
                            "id": "value",
                            "label": "待判断数据",
                            "data_type": "json",
                            "required": True,
                        }
                    ],
                    "outputs": [
                        {
                            "id": "true",
                            "label": "满足条件",
                            "data_type": "json",
                            "required": False,
                        },
                        {
                            "id": "false",
                            "label": "不满足条件",
                            "data_type": "json",
                            "required": False,
                        }
                    ],
                    "config": {"condition": condition},
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source_node": "src",
                    "source_port": "source",
                    "target_node": "parse",
                    "target_port": "source",
                },
                {
                    "id": "e2",
                    "source_node": "parse",
                    "source_port": "story",
                    "target_node": "cond",
                    "target_port": "value",
                },
            ],
        }
    )


@pytest.mark.parametrize(
    "operator", ["eq", "ne", "gt", "gte", "lt", "lte", "contains"]
)
def test_validation_requires_condition_value(operator):
    """#224: a value-requiring operator without "value" is a publish-time
    ERROR, so the broken graph never reaches a paid run."""

    report = validate_graph(_condition_graph({"path": "$.ready", "operator": operator}))
    codes = [issue.code for issue in report.issues if issue.severity == "ERROR"]
    assert "CONDITION_VALUE_REQUIRED" in codes
    assert not report.valid


@pytest.mark.parametrize(
    "condition",
    [
        {"path": "$.ready", "operator": "eq", "value": True},
        {"path": "$.ready", "operator": "contains", "value": "章"},
        {"path": "$.ready", "operator": "exists"},
    ],
    ids=["eq-with-value", "contains-with-value", "exists-valueless"],
)
def test_validation_accepts_conditions_with_value(condition):
    report = validate_graph(_condition_graph(condition))
    assert report.valid, [issue.model_dump() for issue in report.issues]


@pytest.mark.parametrize(
    ("value", "operator", "expected", "result"),
    [
        ("一段文本", "contains", None, False),  # None in str used to raise TypeError
        ("一段文本", "contains", "文本", True),
        ([1, 2], "contains", None, False),
        ({"a": 1}, "contains", ["unhashable"], False),  # TypeError guarded
        (5, "gt", None, False),
        (None, "gte", 3, False),
        (None, "lt", "a", False),
        (5, "gt", 3, True),
        (5, "lte", 5, True),
        ("b", "gt", "a", True),
    ],
)
def test_condition_matches_never_raises_on_type_mismatch(value, operator, expected, result):
    """#224: an incomparable expected-type answers False instead of crashing
    the worker."""

    assert _condition_matches(value, operator, expected) is result


def test_workflow_node_config_condition_rejects_non_finite():
    """#225 (workflow half): the published condition dict rejects NaN/Infinity
    recursively."""

    with pytest.raises(ValidationError):
        WorkflowNodeConfig(condition={"operator": "gt", "value": float("nan")})
    with pytest.raises(ValidationError):
        WorkflowNodeConfig(
            condition={"operator": "eq", "nested": {"v": float("inf")}}
        )
    assert WorkflowNodeConfig(condition={"operator": "eq", "value": 3}).condition[
        "value"
    ] == 3


# --------------------------------------------------------------------- #225


@pytest.mark.parametrize(
    "raw",
    [
        b'{"name": "x", "profile": NaN}',
        b'{"name": "x", "profile": Infinity}',
        b'{"name": "x", "profile": -Infinity}',
        b'{"items": [1, NaN]}',
    ],
    ids=["nan", "infinity", "negative-infinity", "in-list"],
)
def test_bare_non_finite_json_literal_is_422(client, raw):
    """#225: stdlib json would happily parse these; the middleware answers
    422 before the app is invoked."""

    response = client.post(
        "/api/v1/projects", content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422, response.text
    assert "NaN" in response.json()["detail"] or "Infinity" in response.json()["detail"]


def test_non_finite_text_inside_string_is_not_rejected(client):
    """The scan is lexical on value tokens only: literal "NaN" as content is
    legal JSON and must keep flowing."""

    response = client.post(
        "/api/v1/projects",
        content='{"name": "NaN 与 Infinity 用法说明"}'.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b'{"a": NaN}', True),
        (b'[Infinity]', True),
        (b'{"a": -Infinity}', True),
        (b'{"a": "NaN"}', False),
        ('{"a": "包含 Infinity 字样"}'.encode("utf-8"), False),
        (b'{"NaN": 1}', False),
        (b'{"a": 1e999}', False),  # overflow literal: schema validators own it
        (b'{}', False),
    ],
)
def test_non_finite_literal_scanner(raw, expected):
    assert json_contains_non_finite_literals(raw) is expected


def test_strict_json_serializer_rejects_non_finite():
    with pytest.raises(ValueError):
        _strict_json_serializer({"a": float("nan")})
    with pytest.raises(ValueError):
        _strict_json_serializer({"nested": [float("inf")]})
    assert _strict_json_serializer({"a": 1, "b": "中文"}) == '{"a":1,"b":"中文"}'


def test_prompt_compiler_bounding_raises_on_non_finite():
    from app.services.prompt_compiler import _bound_structured_block

    with pytest.raises(ValueError):
        _bound_structured_block({"score": float("nan")}, 100)


@pytest.mark.parametrize(
    "build",
    [
        lambda bad: StyleProfileCreate(name="风格", profile={"ink": bad}),
        lambda bad: OutfitCreate(
            character_id="c1", name="服装", components={"layers": bad}
        ),
        lambda bad: OutfitCreate(character_id="c1", name="服装", state_rules=bad),
        lambda bad: DialogueCreate(target_text="词", region={"box": bad}),
        lambda bad: PanelUpdate(actions={"hero": bad}, version=1),
        lambda bad: RepairRequest(
            inspection_result_id="i1",
            repair_type="PANEL",
            model_alias="image.nano_banana_2",
            resolution="2K",
            target_regions=[{"x": bad}],
        ),
    ],
    ids=[
        "style-profile",
        "outfit-components",
        "outfit-state-rules",
        "dialogue-region",
        "panel-actions",
        "repair-target-regions",
    ],
)
def test_free_form_fields_reject_non_finite_floats(build):
    with pytest.raises(ValidationError):
        build(float("nan"))
    with pytest.raises(ValidationError):
        build(float("inf"))


def test_free_form_fields_accept_finite_floats():
    assert StyleProfileCreate(name="风格", profile={"ink": 0.5}).profile == {
        "ink": 0.5
    }


# --------------------------------------------------------------------- #226


def test_empty_model_id_strings_are_422(client):
    project = client.post("/api/v1/projects", json={"name": "模型引用"}).json()
    patched = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"version": project["version"], "default_text_model_id": ""},
    )
    assert patched.status_code == 422, patched.text
    created = client.post(
        "/api/v1/projects", json={"name": "空模型2", "last_image_model_id": ""}
    )
    assert created.status_code == 422, created.text


@pytest.mark.parametrize(
    ("route", "payload"),
    [
        ("/api/v1/projects/{project_id}/characters", {"primary_name": " "}),
        ("/api/v1/projects/{project_id}/scene-assets", {"name": "\u3000"}),
    ],
    ids=["character-create", "scene-asset-create"],
)
def test_whitespace_only_names_are_422(client, route, payload):
    """#226: routes strip AFTER pydantic, so the schema must reject
    whitespace-only names before they store as empty strings."""

    project = client.post("/api/v1/projects", json={"name": "空白名称"}).json()
    response = client.post(route.format(project_id=project["id"]), json=payload)
    assert response.status_code == 422, response.text


def test_whitespace_only_name_updates_are_422(client):
    project = client.post("/api/v1/projects", json={"name": "空白更新"}).json()
    character = client.post(
        f"/api/v1/projects/{project['id']}/characters", json={"primary_name": "角色"}
    ).json()
    patched = client.patch(
        f"/api/v1/characters/{character['id']}",
        json={"primary_name": " ", "version": character["version"]},
    )
    assert patched.status_code == 422, patched.text

    asset = client.post(
        f"/api/v1/projects/{project['id']}/scene-assets", json={"name": "仓库"}
    ).json()
    patched_asset = client.patch(
        f"/api/v1/projects/{project['id']}/scene-assets/{asset['id']}",
        json={"name": " ", "version": asset["version"]},
    )
    assert patched_asset.status_code == 422, patched_asset.text


def test_archive_confirm_compares_stripped_to_stripped(client):
    """#226: names are stored unstripped, so a stored padded name must still
    be archivable by typing the trimmed name."""

    created = client.post("/api/v1/projects", json={"name": " 影子项目 "})
    assert created.status_code == 201, created.text
    project = created.json()
    response = client.delete(
        f"/api/v1/projects/{project['id']}", params={"confirm_name": "影子项目"}
    )
    assert response.status_code == 204, response.text


@pytest.mark.parametrize(
    "build",
    [
        lambda version: FavoriteUpdate(is_favorite=True, version=version),
        lambda version: AssetUpdate(display_name="素材名", version=version),
        lambda version: SceneOutfitUpdate(assignments={}, version=version),
    ],
    ids=["favorite", "asset", "scene-outfit"],
)
def test_update_schemas_accept_optional_version_token(build):
    """#226: the other agents' routes claim on these optional tokens; the
    schema half accepts omission (legacy callers) and valid tokens, and
    rejects out-of-range ones."""

    assert build(version=None).version is None
    assert build(version=5).version == 5
    with pytest.raises(ValidationError):
        build(version=0)


# --------------------------------------------------------------------- #237


def _script_chapter(db, project_id: str) -> dict:
    chapter = Chapter(project_id=project_id, ordinal=1, title="第一章")
    db.add(chapter)
    db.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db.add(page)
    db.flush()
    scene = Scene(chapter_id=chapter.id, ordinal=1, location="仓库")
    db.add(scene)
    db.flush()
    source_revision = SourceRevision(
        chapter_id=chapter.id,
        revision=1,
        source_type="TXT",
        original_text="正文第一段。",
        sha256="f" * 64,
        character_count=6,
    )
    db.add(source_revision)
    db.flush()
    revision = ScriptRevision(
        chapter_id=chapter.id,
        source_revision_id=source_revision.id,
        revision_no=1,
        status="DRAFT",
        coverage={},
    )
    db.add(revision)
    db.commit()
    return {"chapter": chapter, "page": page, "scene": scene, "revision": revision}


def test_delete_script_cascades_without_jobs(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "剧本删除级联"}).json()
    seeded = _script_chapter(db_session, project["id"])

    response = client.delete(f"/api/v1/chapters/{seeded['chapter'].id}/script")

    assert response.status_code == 204, response.text
    db_session.expire_all()
    assert db_session.get(MangaPage, seeded["page"].id) is None
    assert db_session.get(Scene, seeded["scene"].id) is None
    assert db_session.get(ScriptRevision, seeded["revision"].id) is None
    assert db_session.get(Chapter, seeded["chapter"].id).status == "IMPORTED"


def test_delete_script_rechecks_active_jobs_after_locking(client, db_session, monkeypatch):
    """#237: a job committed inside the old read-then-delete window used to be
    orphaned. The guard now re-reads AFTER the chapter/page locks, so a job
    landing at lock time produces a 409 and the pages survive."""

    from app.services.ordinal_allocator import lock_entity as real_lock_entity

    project = client.post("/api/v1/projects", json={"name": "剧本删除竞态"}).json()
    seeded = _script_chapter(db_session, project["id"])
    injected = {"done": False}

    def locking_lock(db, model_cls, entity_id):
        # A paid job commits exactly when the route takes the page lock —
        # inside the window the pre-fix plain-SELECT guard could never see.
        if model_cls is MangaPage and not injected["done"]:
            injected["done"] = True
            db.add(
                GenerationJob(
                    project_id=project["id"],
                    target_type="CHAPTER",
                    target_id=seeded["chapter"].id,
                    job_type="SOURCE_PARSE",
                    status=JobStatus.WAITING,
                )
            )
            db.commit()
        return real_lock_entity(db, model_cls, entity_id)

    monkeypatch.setattr(
        "app.services.ordinal_allocator.lock_entity", locking_lock
    )

    response = client.delete(f"/api/v1/chapters/{seeded['chapter'].id}/script")

    assert response.status_code == 409, response.text
    assert "任务正在执行" in response.json()["detail"]
    db_session.expire_all()
    assert db_session.get(MangaPage, seeded["page"].id) is not None


# --------------------------------------------------------------------- #246


def test_repair_request_rejects_draft_1k_resolution():
    """#246 schema half: the UpscaleRequest-style floor — a 1K repair can only
    equal or downgrade its parent, so it must 422 at the schema boundary."""

    base = {
        "inspection_result_id": "i1",
        "repair_type": "PANEL",
        "model_alias": "image.nano_banana_2",
    }
    with pytest.raises(ValidationError):
        RepairRequest(**base, resolution="1K")
    assert RepairRequest(**base, resolution="2K").resolution == Resolution.STANDARD_2K
    assert RepairRequest(**base, resolution="4K").resolution == Resolution.HIGH_4K
