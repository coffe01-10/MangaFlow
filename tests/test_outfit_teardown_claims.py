"""delete_outfit's scene/panel teardown claims each row with an atomic
conditional UPDATE (WHERE version = :read) and retries a bounded number of
times before surfacing 409. These tests defeat every claim attempt with an
in-transaction version bump — the deterministic stand-in for a concurrent
writer that wins the row between the teardown's read and its claim — so the
exhausted-retry 409 arm stays live: a truthy SQLAlchemy Result used to mask
the lost claims and let the teardown answer 204 while the scene/panel kept
the outfit it just deleted."""

from sqlalchemy import update as sa_update

from app.models import Chapter, Character, MangaPage, Outfit, Panel, Project, Scene


def _seed_assigned_outfit(db_session, name: str):
    project = Project(name=name)
    db_session.add(project)
    db_session.flush()
    character = Character(project_id=project.id, primary_name="林澈")
    db_session.add(character)
    db_session.flush()
    outfit = Outfit(project_id=project.id, character_id=character.id, name="制服")
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add_all([outfit, chapter])
    db_session.flush()
    return character, outfit, chapter


def _defeat_every_claim(db_session, model, table_name, survivor, monkeypatch):
    """Intercept UPDATEs on ``table_name``: before each one lands, a concurrent
    writer bumps version and writes ``survivor`` so the claim's WHERE misses.
    Returns the attempt counter."""
    real_execute = db_session.execute
    attempts = {"n": 0}

    def contended_execute(statement, *args, **kwargs):
        if statement.is_update and statement.table.name == table_name:
            attempts["n"] += 1
            real_execute(
                sa_update(model)
                .values(version=model.version + 1, **survivor)
                .execution_options(synchronize_session=False)
            )
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", contended_execute)
    return attempts


def test_delete_outfit_409s_when_scene_claim_never_lands(client, db_session, monkeypatch):
    character, outfit, chapter = _seed_assigned_outfit(db_session, "场景拆除竞态")
    scene = Scene(
        chapter_id=chapter.id,
        ordinal=1,
        outfit_assignments={character.id: outfit.id},
    )
    db_session.add(scene)
    db_session.commit()

    attempts = _defeat_every_claim(
        db_session, Scene, "scenes", {"location": "厨房"}, monkeypatch
    )

    response = client.delete(f"/api/v1/outfits/{outfit.id}")

    assert response.status_code == 409, response.text
    assert attempts["n"] == 3
    db_session.expire_all()
    # The 409 rollback discarded the whole teardown unit — including the
    # simulated writer's in-transaction bumps — so nothing landed at all:
    # the outfit survives and the stale assignment set was never overwritten.
    assert db_session.get(Outfit, outfit.id) is not None
    row = db_session.get(Scene, scene.id)
    assert row.version == 1
    assert row.outfit_assignments == {character.id: outfit.id}


def test_delete_outfit_409s_when_panel_claim_never_lands(client, db_session, monkeypatch):
    character, outfit, chapter = _seed_assigned_outfit(db_session, "分镜拆除竞态")
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db_session.add(page)
    db_session.flush()
    panel = Panel(
        page_id=page.id,
        reading_order=1,
        outfits={character.id: outfit.id},
    )
    db_session.add(panel)
    db_session.commit()

    attempts = _defeat_every_claim(
        db_session, Panel, "panels", {"shot_type": "extreme_wide_shot"}, monkeypatch
    )

    response = client.delete(f"/api/v1/outfits/{outfit.id}")

    assert response.status_code == 409, response.text
    assert attempts["n"] == 3
    db_session.expire_all()
    assert db_session.get(Outfit, outfit.id) is not None
    row = db_session.get(Panel, panel.id)
    assert row.version == 1
    assert row.outfits == {character.id: outfit.id}


def test_delete_outfit_cleans_after_losing_one_scene_claim(client, db_session, monkeypatch):
    """A single lost claim retries on the refreshed state: the cleanup still
    lands, with the concurrent writer's version folded in."""
    character, outfit, chapter = _seed_assigned_outfit(db_session, "场景拆除重试")
    scene = Scene(
        chapter_id=chapter.id,
        ordinal=1,
        outfit_assignments={character.id: outfit.id},
    )
    db_session.add(scene)
    db_session.commit()

    real_execute = db_session.execute
    attempts = {"n": 0}

    def contended_execute(statement, *args, **kwargs):
        if statement.is_update and statement.table.name == "scenes":
            attempts["n"] += 1
            if attempts["n"] == 1:
                real_execute(
                    sa_update(Scene)
                    .values(version=Scene.version + 1, location="厨房")
                    .execution_options(synchronize_session=False)
                )
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", contended_execute)

    response = client.delete(f"/api/v1/outfits/{outfit.id}")

    assert response.status_code == 204, response.text
    assert attempts["n"] == 2
    db_session.expire_all()
    assert db_session.get(Outfit, outfit.id) is None
    row = db_session.get(Scene, scene.id)
    assert row.location == "厨房"
    # The writer's bump (+1) then the successful claim (+1).
    assert row.version == 3
    assert row.outfit_assignments == {}
