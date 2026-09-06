"""Story-parse finalize must not clobber concurrent character edits.

The parse pre-reads project characters before finalizing and wrote
aliases/description with a blind ``character.version += 1``. A user
PATCH /characters/{id} committing after that pre-read (atomic CAS version+1
plus new aliases) was silently clobbered: the finalize rewrote the old alias
set and collapsed the version increment, so the successful PATCH vanished
with no 409. The finalize now claims the character version and re-merges
onto fresh state (bounded retries, log-and-continue on final loss) — the
billed ScriptRevision always lands.
"""

from sqlalchemy import select, update as sa_update

from app.domain.states import JobStatus
from app.models import (
    Character,
    Chapter,
    GenerationJob,
    ScriptRevision,
    SourceSegment,
)
from app.services.ai_schemas import (
    BeatDraft,
    CharacterDraft,
    SceneDraft,
    StoryParseOutput,
)
from app.services.worker_handlers.story_parse import _run_story_parse


def test_story_parse_merge_survives_concurrent_character_patch(
    client, db_session, monkeypatch
):
    project = client.post("/api/v1/projects", json={"name": "解析角色并发"}).json()
    imported = client.post(
        f"/api/v1/projects/{project['id']}/sources/import",
        json={"title": "第一章", "text": "顾川推开门。"},
    ).json()
    chapter_id = imported["chapters"][0]["id"]
    chapter = db_session.get(Chapter, chapter_id)
    segments = list(
        db_session.scalars(
            select(SourceSegment).where(
                SourceSegment.source_revision_id == chapter.current_source_revision_id
            )
        )
    )
    assert segments

    character = Character(
        project_id=project["id"], primary_name="顾川", aliases=["小川"]
    )
    db_session.add(character)
    db_session.commit()

    def fake_output():
        return StoryParseOutput(
            characters=[CharacterDraft(primary_name="顾川", aliases=["小川", "阿川"])],
            scenes=[
                SceneDraft(
                    ordinal=1,
                    location="教室",
                    purpose="顾川登场",
                    source_segment_ids=[segment.id for segment in segments],
                    beats=[
                        BeatDraft(
                            ordinal=index,
                            action=segment.text,
                            speaker_name="",
                            dialogue="",
                            source_segment_ids=[segment.id],
                        )
                        for index, segment in enumerate(segments, 1)
                    ],
                )
            ],
        )

    class FakeTextAdapter:
        def generate_structured(self, request, schema):
            return fake_output()

    monkeypatch.setattr("app.worker_tasks._adapter", lambda alias: FakeTextAdapter())

    real_match = None
    import app.services.worker_handlers.story_parse as story_parse_module

    real_match = story_parse_module._match_existing_character
    poison_calls = []

    def poison_then_match(existing, primary_name, aliases, claimed):
        result = real_match(existing, primary_name, aliases, claimed)
        print("MATCH:", result.id if result else None, "existing count:", len(existing))
        if not poison_calls:
            poison_calls.append(1)
            # Simulate PATCH /characters/{id} committing right after the
            # parse's pre-read snapshot: new alias + atomic version bump.
            db_session.execute(
                sa_update(Character)
                .where(Character.id == character.id)
                .values(
                    aliases=["小川", "顾队长"],
                    version=Character.version + 1,
                )
                .execution_options(synchronize_session=False)
            )
            db_session.commit()
        return result

    monkeypatch.setattr(
        story_parse_module, "_match_existing_character", poison_then_match
    )

    job = GenerationJob(
        project_id=project["id"],
        target_type="CHAPTER",
        target_id=chapter_id,
        job_type="SOURCE_PARSE",
        status=JobStatus.PREPARING,
        model_alias="text.fast",
    )
    db_session.add(job)
    db_session.commit()
    _run_story_parse(db_session, job)
    db_session.commit()

    assert poison_calls, "expected the parse to attempt a character merge"

    db_session.expire_all()
    row = db_session.get(Character, character.id)
    # The user's concurrent PATCH survived the finalize merge.
    assert "顾队长" in row.aliases
    # The parse's own new alias is also merged onto the fresh state.
    assert "阿川" in row.aliases
    # The parse still landed (script revision exists for the chapter).
    assert (
        db_session.query(ScriptRevision).filter_by(chapter_id=chapter_id).count() == 1
    )
    print("FINAL row:", row.id, row.aliases, row.version)
    print("ALL rows:", [(c.id, c.aliases, c.version) for c in db_session.query(Character).all()])
