"""Red-team regression tests: script integrity, prompt budgets, presence gates.

Covers #124 (handler-level SOURCE_PARSE mutual exclusion), #152 (beat ordinal
resequencing), #159 (draft field length caps + pre-insert truncation), #160
(cast-first character bible with a prompt budget), and #164 (presence key
normalization, memorial marker matching, and the PRESENCE inspection gate).
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.domain.states import CharacterPresence, JobStatus, PageStatus
from app.model_adapters.base import ProviderAdapterError
from app.models import (
    Beat,
    Chapter,
    Character,
    GenerationJob,
    InspectionResult,
    MangaPage,
    Panel,
    Project,
    Scene,
    SourceSegment,
    utcnow,
)
from app.services.ai_schemas import (
    BeatDraft,
    CharacterDraft,
    InspectionItem,
    PageInspectionOutput,
    SceneDraft,
    StoryParseOutput,
)
from app.services.content_workflow import _resolve_panel_cast
from app.services.page_completion import build_page_production_readiness
from app.services.prompt_compiler import PROMPT_CHAR_BUDGET, compile_page_prompt
from app.services.worker_handlers.inspection import _run_inspection
from app.services.worker_handlers import provider
from app.services.worker_handlers.story_parse import (
    _merge_story_parse_outputs,
    _run_story_parse,
)
from test_quality_gates import _ready_page


def _unvalidated_beat(**overrides) -> BeatDraft:
    """Build a BeatDraft bypassing validation (#152/#159 test seam).

    Real adapters validate model JSON against the schema, so ordinals of 0 or
    overlong fields would fail there; ``model_construct`` simulates the
    emission that slipped past validation, which is exactly the layer the
    merge resequencing and the pre-insert sanitizer must defend.
    """

    template = BeatDraft(ordinal=1, action="动作", dialogue="台词")
    values = {name: getattr(template, name) for name in BeatDraft.model_fields}
    values.update(overrides)
    return BeatDraft.model_construct(**values)


def _unvalidated_character(**overrides) -> CharacterDraft:
    template = CharacterDraft(primary_name="角色")
    values = {name: getattr(template, name) for name in CharacterDraft.model_fields}
    values.update(overrides)
    return CharacterDraft.model_construct(**values)


def _unvalidated_scene(**overrides) -> SceneDraft:
    template = SceneDraft(ordinal=1, beats=[])
    values = {name: getattr(template, name) for name in SceneDraft.model_fields}
    values.update(overrides)
    return SceneDraft.model_construct(**values)


def _first_segment(db_session, chapter_id: str) -> SourceSegment:
    chapter = db_session.get(Chapter, chapter_id)
    return (
        db_session.query(SourceSegment)
        .filter(SourceSegment.source_revision_id == chapter.current_source_revision_id)
        .order_by(SourceSegment.ordinal)
        .first()
    )


# --- #152: beat ordinals are re-sequenced per scene -------------------------


def test_merge_resequences_duplicate_beat_ordinals_and_dedupes():
    output = StoryParseOutput(
        characters=[],
        scenes=[
            SceneDraft(
                ordinal=1,
                beats=[
                    BeatDraft(ordinal=2, action="第一拍", dialogue="一"),
                    BeatDraft(ordinal=2, action="第二拍", dialogue="二"),
                    BeatDraft(ordinal=7, action="第三拍", dialogue="三"),
                    BeatDraft(ordinal=5, action="第一拍", dialogue="重复拍"),
                ],
            )
        ],
    )
    merged = _merge_story_parse_outputs([output])
    beats = merged.scenes[0].beats
    assert [beat.ordinal for beat in beats] == [1, 2, 3]
    assert [beat.dialogue for beat in beats] == ["一", "二", "三"]


def test_story_parse_persists_consecutive_unique_beat_ordinals(
    client, db_session, monkeypatch
):
    """A model emission with duplicate/zero ordinals (escaping validation)
    must still land as consecutive unique ordinals with deterministic
    dialogue order (#152)."""

    project = client.post("/api/v1/projects", json={"name": "拍序修复"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "苏清白推开纸门。\n\n顾川站在走廊尽头。"},
    ).json()["chapters"][0]
    segments = (
        db_session.query(SourceSegment)
        .filter(SourceSegment.source_revision_id == imported["current_source_revision_id"])
        .order_by(SourceSegment.ordinal)
        .all()
    )

    class FakeTextAdapter:
        def generate_structured(self, request, schema):
            assert schema is StoryParseOutput
            return StoryParseOutput(
                characters=[],
                scenes=[
                    SceneDraft(
                        ordinal=1,
                        source_segment_ids=[item.id for item in segments],
                        beats=[
                            _unvalidated_beat(
                                ordinal=1,
                                action=segments[0].text,
                                dialogue="第一句",
                                source_segment_ids=[segments[0].id],
                            ),
                            _unvalidated_beat(
                                ordinal=0,
                                action=segments[1].text,
                                dialogue="第二句",
                                source_segment_ids=[segments[1].id],
                            ),
                            _unvalidated_beat(
                                ordinal=1,
                                action=segments[1].text,
                                dialogue="第二句",
                                source_segment_ids=[segments[1].id],
                            ),
                        ],
                    )
                ],
            )

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeTextAdapter())
    job = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=imported["id"],
        job_type="SOURCE_PARSE",
        status="PREPARING",
        model_alias="text.fast",
    )
    db_session.add(job)
    db_session.flush()
    _run_story_parse(db_session, job)
    db_session.commit()

    beats = (
        db_session.query(Beat)
        .join(Scene)
        .filter(Scene.chapter_id == imported["id"])
        .order_by(Beat.ordinal)
        .all()
    )
    assert [beat.ordinal for beat in beats] == [1, 2]
    assert [beat.dialogue for beat in beats] == ["第一句", "第二句"]


# --- #159: draft field caps + pre-insert truncation ------------------------


def test_story_parse_truncates_overlong_draft_fields_before_insert(
    client, db_session, monkeypatch
):
    """Overlong model emissions (200-char primary_name, 150-char emotion, 60
    aliases) must not fail the paid job at insert time; they are truncated to
    the DB column widths / API contract instead (#159)."""

    project = client.post("/api/v1/projects", json={"name": "超长字段截断"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "父亲留下长长的叹息。"},
    ).json()["chapters"][0]
    segment = _first_segment(db_session, imported["id"])

    class FakeTextAdapter:
        def generate_structured(self, request, schema):
            return StoryParseOutput(
                characters=[
                    _unvalidated_character(
                        primary_name="父" * 200,
                        aliases=[f"别名{index}" for index in range(60)],
                        description="悲" * 9000,
                    )
                ],
                scenes=[
                    _unvalidated_scene(
                        ordinal=1,
                        location="堂" * 250,
                        source_segment_ids=[segment.id],
                        beats=[
                            _unvalidated_beat(
                                ordinal=1,
                                action=segment.text,
                                emotion="恸" * 150,
                                speaker_name="父" * 200,
                                source_segment_ids=[segment.id],
                            )
                        ],
                    )
                ],
            )

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeTextAdapter())
    job = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=imported["id"],
        job_type="SOURCE_PARSE",
        status="PREPARING",
        model_alias="text.fast",
    )
    db_session.add(job)
    db_session.flush()
    _run_story_parse(db_session, job)
    db_session.commit()

    character = (
        db_session.query(Character).filter_by(project_id=project["id"]).one()
    )
    assert len(character.primary_name) == 120
    assert len(character.aliases) == 40
    assert len(character.canonical_description) == 8000
    scene = db_session.query(Scene).filter_by(chapter_id=imported["id"]).one()
    assert len(scene.location) == 200
    beat = db_session.query(Beat).filter_by(scene_id=scene.id).one()
    assert len(beat.emotion) == 120
    assert len(beat.speaker_name) == 120


def test_story_parse_schema_rejects_invalid_ordinal_and_overlong_name():
    """The first layer of #159/#152: schema validation turns clearly invalid
    emissions into structured-output failures before anything is persisted."""

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BeatDraft(ordinal=0, action="动作")
    with pytest.raises(ValidationError):
        CharacterDraft(primary_name="父" * 121)
    with pytest.raises(ValidationError):
        SceneDraft(ordinal=1, location="堂" * 201)


# --- #124: handler-level SOURCE_PARSE mutual exclusion ----------------------


def test_story_parse_handler_refuses_when_sibling_parse_is_active(
    client, db_session, monkeypatch
):
    project = client.post("/api/v1/projects", json={"name": "解析执行期互斥"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "苏清白推开纸门。"},
    ).json()["chapters"][0]
    first = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=imported["id"],
        job_type="SOURCE_PARSE",
        status=JobStatus.GENERATING,
    )
    second = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=imported["id"],
        job_type="SOURCE_PARSE",
        status=JobStatus.PREPARING,
    )
    db_session.add_all([first, second])
    db_session.commit()

    def forbid_paid_call(*_args, **_kwargs):
        raise AssertionError("重复解析不得发起付费模型调用")

    monkeypatch.setattr(
        "app.services.worker_handlers.story_parse.provider._binding",
        forbid_paid_call,
    )

    with pytest.raises(ProviderAdapterError) as excinfo:
        _run_story_parse(db_session, second)
    assert excinfo.value.code == "SOURCE_PARSE_CONFLICT"
    assert excinfo.value.retryable is False


def test_story_parse_handler_allows_run_after_sibling_terminal_failure(
    client, db_session, monkeypatch
):
    """Only ACTIVE sibling jobs block: a FAILED/CANCELLED predecessor must
    not wedge the chapter (#124 negative case)."""

    project = client.post("/api/v1/projects", json={"name": "终态不阻塞解析"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "苏清白推开纸门。"},
    ).json()["chapters"][0]
    finished = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=imported["id"],
        job_type="SOURCE_PARSE",
        status=JobStatus.FAILED,
        error_code="WORKER_ERROR",
    )
    fresh = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=imported["id"],
        job_type="SOURCE_PARSE",
        status=JobStatus.PREPARING,
        model_alias="text.fast",
    )
    db_session.add_all([finished, fresh])
    db_session.commit()
    segment = _first_segment(db_session, imported["id"])

    def fake_invoke(_db, _binding, _callback):
        return StoryParseOutput(
            characters=[],
            scenes=[
                SceneDraft(
                    ordinal=1,
                    source_segment_ids=[segment.id],
                    beats=[
                        BeatDraft(
                            ordinal=1,
                            action=segment.text,
                            source_segment_ids=[segment.id],
                        )
                    ],
                )
            ],
        )

    monkeypatch.setattr(
        "app.services.worker_handlers.story_parse.provider._binding",
        lambda *args, **kwargs: SimpleNamespace(
            resolved=SimpleNamespace(model=SimpleNamespace(id=None)),
        ),
    )
    monkeypatch.setattr(
        "app.services.worker_handlers.story_parse.provider._invoke_provider",
        fake_invoke,
    )
    _run_story_parse(db_session, fresh)
    db_session.commit()
    assert db_session.query(Beat).join(Scene).filter(
        Scene.chapter_id == imported["id"]
    ).count() == 1


# --- #160: cast-first bible with a prompt budget ----------------------------


def _budget_project(db_session) -> tuple[Project, MangaPage, Character, Character]:
    project = Project(name="长篇选角预算")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    cast = Character(
        project_id=project.id,
        primary_name="苏清白",
        canonical_description="她是本页唯一出场的主角。" + "主" * 2000,
    )
    db_session.add(cast)
    db_session.flush()
    for index in range(300):
        db_session.add(
            Character(
                project_id=project.id,
                primary_name=f"群演{index:03d}",
                canonical_description="无" * 2000,
            )
        )
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        panel_count=1,
        storyboard_version=1,
    )
    db_session.add(page)
    db_session.flush()
    db_session.add(
        Panel(
            page_id=page.id,
            reading_order=1,
            bounds={"x": 0, "y": 0, "width": 1, "height": 1},
            characters=[cast.id],
            character_presence={cast.id: CharacterPresence.VISIBLE.value},
        )
    )
    db_session.commit()
    return project, page, cast, db_session.query(Character).filter_by(
        primary_name="群演001"
    ).one()


def test_page_prompt_embeds_cast_only_under_budget(db_session):
    project, page, cast, extra = _budget_project(db_session)

    prompt, snapshot = compile_page_prompt(db_session, page, project)

    assert len(prompt) < PROMPT_CHAR_BUDGET
    # Cast member is embedded in full (complete description present).
    assert cast.canonical_description in prompt
    assert len(snapshot["input"]["characters"]) == 1
    assert snapshot["input"]["characters"][0]["primary_name"] == "苏清白"
    # Non-cast characters appear as a name-only roster, never with their
    # 2000-char descriptions.
    assert extra.canonical_description not in prompt
    assert "群演001" in prompt
    assert snapshot["input"]["other_characters"]["count"] == 300


def test_page_prompt_compresses_cast_descriptions_over_budget(db_session):
    project = Project(name="超预算压缩")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    cast_ids = []
    for index in range(10):
        member = Character(
            project_id=project.id,
            primary_name=f"主演{index}",
            canonical_description=f"标记{index}" + "详" * 7000,
        )
        db_session.add(member)
        db_session.flush()
        cast_ids.append(member.id)
    page = MangaPage(chapter_id=chapter.id, page_number=1, panel_count=1)
    db_session.add(page)
    db_session.flush()
    db_session.add(
        Panel(
            page_id=page.id,
            reading_order=1,
            bounds={"x": 0, "y": 0, "width": 1, "height": 1},
            characters=cast_ids,
            character_presence={item: CharacterPresence.VISIBLE.value for item in cast_ids},
        )
    )
    db_session.commit()

    prompt, _snapshot = compile_page_prompt(db_session, page, project)

    # 10 x 7000-char descriptions exceed the budget uncompressed; the rebuild
    # truncates cast descriptions to the compressed cap and fits.
    assert len(prompt) < PROMPT_CHAR_BUDGET
    assert "标记0" in prompt
    assert "详" * 2500 not in prompt


# --- #164 instance 1: presence resolution -----------------------------------


def test_structured_presence_key_resolves_via_normalization():
    """A structured key 「父 亲」 must resolve against the DB name 「父亲」
    through the shared normalizer instead of falling to text heuristics."""

    father = Character(project_id="p", primary_name="父亲", aliases=["爸爸"])
    page = MangaPage(page_number=2)
    beat = Beat(
        scene_id="s",
        ordinal=1,
        action="房间里安静得能听见钟表走动。",
        source_range={"character_presence": {"父 亲": CharacterPresence.VISIBLE.value}},
    )

    presence, _props = _resolve_panel_cast(
        page=page, text="", beat=beat, characters=[father]
    )
    assert presence == {father.id: CharacterPresence.VISIBLE.value}


def test_memorial_phrase_marks_character_mentioned_not_visible():
    """「灵牌上刻着爸爸的名字」 (marker and name non-adjacent, in any text
    field) must not produce a VISIBLE father (#164)."""

    father = Character(project_id="p", primary_name="父亲", aliases=["爸爸"])
    page = MangaPage(page_number=2)
    beat = Beat(
        scene_id="s",
        ordinal=1,
        action="我望着灵牌上刻着爸爸的名字，久久说不出话。",
        source_range={"segment_ids": []},
    )

    presence, props = _resolve_panel_cast(
        page=page, text="", beat=beat, characters=[father]
    )
    assert presence == {father.id: CharacterPresence.MENTIONED.value}
    assert "父亲的灵牌" in props

    # Control: a plain on-screen appearance still resolves VISIBLE.
    visible_beat = Beat(
        scene_id="s",
        ordinal=1,
        action="父亲走进房间，把伞递给我。",
        source_range={"segment_ids": []},
    )
    presence, _props = _resolve_panel_cast(
        page=page, text="", beat=visible_beat, characters=[father]
    )
    assert presence == {father.id: CharacterPresence.VISIBLE.value}


def test_story_parse_normalizes_presence_keys_at_persist(client, db_session, monkeypatch):
    project = client.post("/api/v1/projects", json={"name": "presence 键规范化"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "父亲坐在灯下。"},
    ).json()["chapters"][0]
    segment = _first_segment(db_session, imported["id"])

    class FakeTextAdapter:
        def generate_structured(self, request, schema):
            return StoryParseOutput(
                characters=[CharacterDraft(primary_name="父亲", aliases=["爸爸"])],
                scenes=[
                    SceneDraft(
                        ordinal=1,
                        source_segment_ids=[segment.id],
                        beats=[
                            _unvalidated_beat(
                                ordinal=1,
                                action=segment.text,
                                source_segment_ids=[segment.id],
                                character_presence={
                                    "爸 爸": CharacterPresence.VISIBLE,
                                    "": CharacterPresence.MENTIONED,
                                },
                            )
                        ],
                    )
                ],
            )

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeTextAdapter())
    job = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=imported["id"],
        job_type="SOURCE_PARSE",
        status="PREPARING",
        model_alias="text.fast",
    )
    db_session.add(job)
    db_session.flush()
    _run_story_parse(db_session, job)
    db_session.commit()

    beat = db_session.query(Beat).join(Scene).filter(
        Scene.chapter_id == imported["id"]
    ).one()
    stored = beat.source_range["character_presence"]
    assert stored == {"爸爸": CharacterPresence.VISIBLE.value}


# --- #164 instance 2: PRESENCE inspection gate ------------------------------


def _presence_page(db_session):
    page, candidate = _ready_page(
        db_session, candidate_status="READY", continuity="NOT_CHECKED"
    )
    page.status = PageStatus.FINAL_CHECKING
    db_session.commit()
    project_id = db_session.get(Chapter, page.chapter_id).project_id
    characters = []
    for name in ("苏清白", "顾川"):
        row = Character(project_id=project_id, primary_name=name)
        db_session.add(row)
        db_session.flush()
        characters.append(row)
    db_session.add(
        Panel(
            page_id=page.id,
            reading_order=1,
            bounds={"x": 0, "y": 0, "width": 1, "height": 1},
            characters=[item.id for item in characters],
            character_presence={
                item.id: CharacterPresence.VISIBLE.value for item in characters
            },
        )
    )
    db_session.commit()
    return page, candidate, characters


def _run_fake_inspection(db_session, monkeypatch, candidate, detected: list[str]):
    job = GenerationJob(
        project_id=_candidate_project_id(db_session, candidate),
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type="PAGE_INSPECT",
        status=JobStatus.CONSISTENCY_CHECKING,
        attempt_count=1,
    )
    db_session.add(job)
    db_session.commit()
    db_session.info.update(job_id=job.id, job_lease_owner="presence-owner")
    job.lease_owner = "presence-owner"
    job.lease_expires_at = utcnow() + timedelta(minutes=5)
    db_session.commit()

    categories = ["SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY", "PRESENCE"]
    output = PageInspectionOutput(
        items=[
            InspectionItem(
                category=category,
                outcome="PASS",
                score=0.99,
                severity="INFO",
                details={
                    "expected": "结构化目标",
                    "observed": "符合目标",
                    "detected_characters": detected if category == "PRESENCE" else [],
                },
            )
            for category in categories
        ]
    )

    monkeypatch.setattr(
        provider,
        "_binding",
        lambda *args, **kwargs: SimpleNamespace(
            resolved=SimpleNamespace(model=SimpleNamespace(id=None)),
        ),
    )
    monkeypatch.setattr(
        provider, "_invoke_provider", lambda *args, **kwargs: output
    )
    monkeypatch.setattr(
        provider,
        "_asset_path",
        lambda asset: SimpleNamespace(read_bytes=lambda: b"offline"),
    )
    _run_inspection(db_session, job)
    db_session.commit()
    return job


def _candidate_project_id(db_session, candidate) -> str:
    page = db_session.get(MangaPage, candidate.page_id)
    return db_session.get(Chapter, page.chapter_id).project_id


def test_inspection_presence_compliance_surfaces_missing_visible(
    db_session, monkeypatch
):
    """All-pass model verdict + a detected-characters list that omits one
    VISIBLE cast member must surface a deterministic compliance failure
    (#164)."""

    page, candidate, characters = _presence_page(db_session)

    _run_fake_inspection(
        db_session, monkeypatch, candidate, detected=[characters[0].primary_name]
    )

    presence_rows = (
        db_session.query(InspectionResult)
        .filter_by(candidate_id=candidate.id, category="PRESENCE")
        .all()
    )
    failing = [row for row in presence_rows if row.outcome == "MISSING"]
    assert failing, "缺一个 VISIBLE 角色时必须产生确定性 PRESENCE 失败行"
    assert characters[1].primary_name in failing[0].details["differences"][0]

    db_session.expire_all()
    candidate_row = db_session.get(type(candidate), candidate.id)
    assert candidate_row.status == "NEEDS_REVIEW"
    page_row = db_session.get(MangaPage, page.id)
    assert page_row.status == PageStatus.NEEDS_REPAIR
    assert page_row.continuity_status == "NEEDS_REVIEW"

    readiness = build_page_production_readiness(db_session, page_row)
    assert readiness.ready is False
    assert readiness.state == "NEEDS_REPAIR"
    assert any(
        blocker.code == "QUALITY_REVIEW_REQUIRED" for blocker in readiness.blockers
    )


def test_inspection_presence_compliance_passes_with_full_detection(
    db_session, monkeypatch
):
    page, candidate, characters = _presence_page(db_session)

    _run_fake_inspection(
        db_session,
        monkeypatch,
        candidate,
        detected=[item.primary_name for item in characters],
    )

    presence_rows = (
        db_session.query(InspectionResult)
        .filter_by(candidate_id=candidate.id, category="PRESENCE")
        .all()
    )
    assert [row.outcome for row in presence_rows] == ["PASS"]
    db_session.expire_all()
    assert db_session.get(MangaPage, page.id).status == PageStatus.FINAL_READY


def test_legacy_candidates_without_presence_row_stay_ready(db_session):
    """#164 backward compatibility: candidates inspected before the PRESENCE
    category existed keep passing the completion gate; once a PRESENCE row
    exists it must pass like any other category."""

    from test_quality_gates import _pass_all

    page, candidate = _ready_page(db_session)
    _pass_all(db_session, candidate.id)
    assert build_page_production_readiness(db_session, page).ready is True

    db_session.add(
        InspectionResult(
            candidate_id=candidate.id,
            storyboard_version=page.storyboard_version,
            category="PRESENCE",
            outcome="MISSING",
            severity="ERROR",
            details={"expected": "必须画出：苏清白", "observed": "画面中识别到：（无）"},
        )
    )
    db_session.commit()
    readiness = build_page_production_readiness(db_session, page)
    assert readiness.ready is False
    assert readiness.state == "NEEDS_REPAIR"
