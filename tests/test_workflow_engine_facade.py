"""Behavioral characterization of the workflow_engine facade and patch seams.

These tests pin the exact public surface of ``app.services.workflow_engine``
and prove — by actually monkeypatching facade attributes and driving the real
publish / planning / reconciliation / lifecycle paths — that the module-level
seams keep taking effect. Attribute existence or object identity alone is not
treated as proof of seam preservation.
"""

from sqlalchemy import select

import app.services.workflow_engine as workflow_engine
from app.models import (
    Asset,
    Chapter,
    Character,
    CharacterReference,
    MangaPage,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    WorkflowDefinition,
    WorkflowNodeRun,
    WorkflowVersion,
)
from app.services.workflow_engine import (
    approve_node,
    cancel_run,
    create_workflow_run,
    default_graph,
    publish_workflow,
)

APPROVED_FACADE_ALL = [
    "CONDITION_OPERATORS",
    "NODE_TYPES",
    "NODE_TYPE_MAP",
    "NodeTypeSpec",
    "PUBLISH_REVISION_MAX_ATTEMPTS",
    "PublishRevisionConflictError",
    "_next_revision",
    "approve_node",
    "blank_graph",
    "cancel_run",
    "canonical_graph",
    "chapter_export_graph",
    "create_workflow_run",
    "create_job",
    "default_graph",
    "enqueue_job",
    "execute_workflow_node",
    "get_run",
    "graph_checksum",
    "mark_job_cancelled",
    "node_type_catalog",
    "publish_workflow",
    "reconcile_run",
    "retry_run",
    "validate_graph",
]

SEAM_NAMES = ["create_job", "enqueue_job", "_next_revision", "mark_job_cancelled"]


def test_facade_all_matches_approved_surface_exactly():
    exported = list(workflow_engine.__all__)
    assert len(exported) == len(set(exported)), "facade __all__ contains duplicates"
    assert sorted(exported) == sorted(APPROVED_FACADE_ALL), (
        "facade __all__ drifted from the approved surface: "
        f"missing={sorted(set(APPROVED_FACADE_ALL) - set(exported))} "
        f"extra={sorted(set(exported) - set(APPROVED_FACADE_ALL))}"
    )
    assert len(exported) == 25

    for name in APPROVED_FACADE_ALL:
        assert hasattr(workflow_engine, name), f"facade lost export: {name}"
    for name in SEAM_NAMES:
        assert hasattr(workflow_engine, name), f"facade lost seam attribute: {name}"
    assert workflow_engine.PUBLISH_REVISION_MAX_ATTEMPTS == 3


def _seed_project(db) -> Project:
    project = Project(name="引擎接缝测试项目")
    db.add(project)
    db.flush()
    return project


def _seed_workflow(db, project_id: str, graph: dict) -> WorkflowDefinition:
    workflow = WorkflowDefinition(
        project_id=project_id,
        name="接缝测试工作流",
        draft_graph=graph,
        is_active=True,
    )
    db.add(workflow)
    db.commit()
    return workflow


def _single_source_graph() -> dict:
    return {
        "nodes": [
            {
                "id": "chapter",
                "type": "source.chapter",
                "name": "原作章节",
                "position": {"x": 0, "y": 0},
                "inputs": [],
                "outputs": [
                    {"id": "source", "label": "原始文本", "data_type": "text", "required": False}
                ],
                "config": {},
            }
        ],
        "edges": [],
    }


def _chapter_export_chain_graph() -> dict:
    return {
        "nodes": [
            {
                "id": "pages",
                "type": "source.approved_pages",
                "name": "成品页面",
                "position": {"x": 0, "y": 0},
                "inputs": [],
                "outputs": [
                    {"id": "pages", "label": "生产通过页面", "data_type": "asset", "required": False}
                ],
                "config": {},
            },
            {
                "id": "export",
                "type": "output.chapter_export",
                "name": "整章导出",
                "position": {"x": 300, "y": 0},
                "inputs": [
                    {"id": "pages", "label": "生产通过页面", "data_type": "asset", "required": True}
                ],
                "outputs": [
                    {"id": "files", "label": "导出文件", "data_type": "asset", "required": False}
                ],
                "config": {},
            },
        ],
        "edges": [
            {
                "id": "pages:pages-export:pages",
                "source_node": "pages",
                "source_port": "pages",
                "target_node": "export",
                "target_port": "pages",
            }
        ],
    }


def test_publish_workflow_hits_facade_next_revision_seam(db_session, monkeypatch):
    project = _seed_project(db_session)
    workflow = _seed_workflow(db_session, project.id, default_graph())
    publish_workflow(db_session, workflow)
    first_revision = (
        db_session.scalars(
            select(WorkflowVersion.revision).where(WorkflowVersion.workflow_id == workflow.id)
        )
    ).first()

    calls: list[str] = []
    real_next_revision = workflow_engine._next_revision

    def recorder(db, workflow_id):
        calls.append(workflow_id)
        return real_next_revision(db, workflow_id) + 40

    monkeypatch.setattr("app.services.workflow_engine._next_revision", recorder)

    version = publish_workflow(db_session, workflow)

    assert calls == [workflow.id], "publish_workflow 必须通过 facade 命中 _next_revision 接缝"
    assert version.revision == first_revision + 40 + 1


def test_create_workflow_run_hits_facade_create_job_seam(db_session, monkeypatch):
    project = _seed_project(db_session)
    workflow = _seed_workflow(db_session, project.id, _single_source_graph())
    publish_workflow(db_session, workflow)

    calls: list[str] = []
    real_create_job = workflow_engine.create_job

    def recorder(*args, **kwargs):
        calls.append(kwargs.get("job_type"))
        return real_create_job(*args, **kwargs)

    monkeypatch.setattr("app.services.workflow_engine.create_job", recorder)

    run = create_workflow_run(
        db_session,
        workflow,
        scope_type="PROJECT",
        scope_id=None,
        start_node_ids=[],
        stop_node_ids=[],
    )

    assert calls == ["WORKFLOW_NODE"], "planning 必须通过 facade 命中 create_job 接缝"
    assert run.status == "COMPLETED"
    node_run = db_session.scalar(
        select(WorkflowNodeRun).where(WorkflowNodeRun.workflow_run_id == run.id)
    )
    assert node_run.status == "COMPLETED"
    assert node_run.job_id


def test_reconcile_run_hits_facade_enqueue_job_seam(db_session, monkeypatch):
    project = _seed_project(db_session)
    chapter = Chapter(project_id=project.id, ordinal=1, title="第一章", status="DRAFT")
    db_session.add(chapter)
    db_session.commit()
    workflow = _seed_workflow(db_session, project.id, _chapter_export_chain_graph())
    publish_workflow(db_session, workflow)

    enqueue_calls: list[str] = []

    def recorder(db, job):
        # 只记录命中，不调用真实 enqueue：避免本地执行器在测试后派发后台任务。
        enqueue_calls.append(job.id)
        return job

    monkeypatch.setattr("app.services.workflow_engine.enqueue_job", recorder)

    run = create_workflow_run(
        db_session,
        workflow,
        scope_type="CHAPTER",
        scope_id=chapter.id,
        start_node_ids=[],
        stop_node_ids=[],
    )

    assert len(enqueue_calls) == 1, "reconciliation 必须通过 facade 命中 enqueue_job 接缝"
    assert run.status == "RUNNING"
    node_run = db_session.scalar(
        select(WorkflowNodeRun).where(
            WorkflowNodeRun.workflow_run_id == run.id, WorkflowNodeRun.node_id == "export"
        )
    )
    assert node_run.status == "RUNNING"
    assert node_run.job_id == enqueue_calls[0]


def _seed_page_hierarchy(db) -> dict:
    from app.config import get_settings
    from app.services.provider_presets import ensure_provider_presets

    ensure_provider_presets(db, get_settings(), auto_commit=True)
    project = Project(name="引擎审批接缝项目")
    db.add(project)
    db.flush()
    chapter = Chapter(
        project_id=project.id,
        ordinal=1,
        title="第一章",
        status="PAGES_PLANNED",
    )
    db.add(chapter)
    db.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=1,
        status="PLANNED",
        source_coverage={"complete": True},
        scene_ids=["scene-1"],
        beat_ids=["beat-1"],
    )
    db.add(page)
    db.flush()
    asset = Asset(
        project_id=project.id,
        kind="CHARACTER_REFERENCE",
        original_name="char.png",
        storage_key="test/char.png",
        mime_type="image/png",
        byte_size=1024,
        width=512,
        height=512,
        sha256="facade-char-hash",
    )
    db.add(asset)
    db.flush()
    character = Character(project_id=project.id, primary_name="接缝主角")
    db.add(character)
    db.flush()
    db.add(CharacterReference(character_id=character.id, asset_id=asset.id, is_canonical=True))
    outfit_asset = Asset(
        project_id=project.id,
        kind="OUTFIT_REFERENCE",
        original_name="outfit.png",
        storage_key="test/outfit.png",
        mime_type="image/png",
        byte_size=1024,
        width=512,
        height=512,
        sha256="facade-outfit-hash",
    )
    db.add(outfit_asset)
    db.flush()
    outfit = Outfit(
        project_id=project.id,
        character_id=character.id,
        name="校服",
        reference_asset_ids=[outfit_asset.id],
    )
    db.add(outfit)
    db.flush()
    db.add(
        Panel(
            page_id=page.id,
            reading_order=1,
            characters=[character.id],
            outfits={character.id: outfit.id},
        )
    )
    db.commit()
    return {
        "project_id": project.id,
        "chapter_id": chapter.id,
        "page_id": page.id,
        "character_id": character.id,
    }


def test_approve_node_hits_facade_create_job_seam(db_session, monkeypatch):
    seeded = _seed_page_hierarchy(db_session)
    workflow = _seed_workflow(db_session, seeded["project_id"], default_graph())
    publish_workflow(db_session, workflow)

    run = create_workflow_run(
        db_session,
        workflow,
        scope_type="PAGE",
        scope_id=seeded["page_id"],
        start_node_ids=["generate"],
        stop_node_ids=["generate"],
    )
    node_run = db_session.scalar(
        select(WorkflowNodeRun).where(
            WorkflowNodeRun.workflow_run_id == run.id,
            WorkflowNodeRun.status == "WAITING_APPROVAL",
        )
    )
    assert node_run is not None

    calls: list[str] = []
    real_create_job = workflow_engine.create_job

    def recorder(*args, **kwargs):
        calls.append(kwargs.get("job_type"))
        return real_create_job(*args, **kwargs)

    monkeypatch.setattr("app.services.workflow_engine.create_job", recorder)
    monkeypatch.setattr(
        "app.services.page_readiness.ensure_page_ready",
        lambda *_args, **_kwargs: None,
    )

    def no_enqueue(db, job):
        # 不调用真实 enqueue：避免本地执行器在测试后派发后台任务。
        return job

    monkeypatch.setattr("app.services.workflow_engine.enqueue_job", no_enqueue)

    approve_node(
        db_session,
        run.id,
        node_run.node_id,
        image_model_alias="image.nano_banana_2",
        resolution="1K",
    )

    assert calls == ["PAGE_GENERATE"], "approve_node 必须通过 facade 命中 create_job 接缝"
    candidate = db_session.scalar(
        select(PageCandidate).where(PageCandidate.page_id == seeded["page_id"])
    )
    assert candidate is not None
    assert candidate.job_id
    assert "scene_asset" in (candidate.prompt_snapshot or {})


def test_approve_node_after_cancel_does_not_create_generate_job(db_session, monkeypatch):
    seeded = _seed_page_hierarchy(db_session)
    workflow = _seed_workflow(db_session, seeded["project_id"], default_graph())
    publish_workflow(db_session, workflow)
    run = create_workflow_run(
        db_session,
        workflow,
        scope_type="PAGE",
        scope_id=seeded["page_id"],
        start_node_ids=["generate"],
        stop_node_ids=["generate"],
    )
    node_run = db_session.scalar(
        select(WorkflowNodeRun).where(
            WorkflowNodeRun.workflow_run_id == run.id,
            WorkflowNodeRun.status == "WAITING_APPROVAL",
        )
    )
    monkeypatch.setattr(
        "app.services.page_readiness.ensure_page_ready",
        lambda *_args, **_kwargs: None,
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        "app.services.workflow_engine.enqueue_job",
        lambda db, job: enqueued.append(job.id) or job,
    )

    cancel_run(db_session, run)
    try:
        approve_node(
            db_session,
            run.id,
            node_run.node_id,
            image_model_alias="image.nano_banana_2",
            resolution="1K",
        )
        raised = False
    except ValueError as error:
        raised = True
        assert "取消" in str(error) or "结束" in str(error) or "确认" in str(error)
    assert raised
    assert enqueued == []
    assert db_session.scalar(select(PageCandidate).where(PageCandidate.page_id == seeded["page_id"])) is None


def test_lazy_consumers_resolve_engine_functions_through_facade():
    """job_service / worker_tasks 的懒加载入口仍经由 facade 解析。"""

    from app import worker_tasks
    from app.services import job_service

    assert callable(job_service.create_job)
    assert callable(workflow_engine.reconcile_run)
    assert callable(workflow_engine.cancel_run)
    assert callable(workflow_engine.execute_workflow_node)
    assert callable(worker_tasks.execute_job)
