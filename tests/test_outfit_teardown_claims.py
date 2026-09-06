"""delete_outfit's scene/panel teardown claims each row with an atomic
conditional UPDATE (WHERE version = :read) and retries a bounded number of
times before surfacing 409. These tests defeat claim attempts with a fake
``rowcount = 0`` result — the deterministic stand-in for a concurrent writer
winning the row between the teardown's read and its claim — so the
exhausted-retry 409 arm stays live: a truthy SQLAlchemy Result used to mask
the lost claims and let the teardown answer 204 while the scene/panel kept
the outfit it just deleted.

The concurrent writer's *field* writes surviving the rollback cannot be
observed on the in-memory SQLite harness (a separate connection cannot
commit into the route's transaction there); that aspect stays NOT RUN and is
covered on PostgreSQL by the row-lock/EPQ semantics of the conditional
claim.
"""

from types import SimpleNamespace

from app.models import Chapter, Character, MangaPage, Outfit, Panel, Project, Scene


def _defeat_scene_or_panel_claims(db_session, table_name, monkeypatch, *, lose_first=0):
    """Intercept UPDATEs on ``table_name``: the first ``lose_first`` claims are
    defeated (rowcount 0, like a concurrent writer winning the row); later
    claims execute for real. Returns the defeat counter."""
    real_execute = db_session.execute
    attempts = {"n": 0}

    def contended_execute(statement, *args, **kwargs):
        if (
            statement.is_update
            and statement.table.name == table_name
            and attempts["n"] < lose_first
        ):
            attempts["n"] += 1
            return SimpleNamespace(rowcount=0)
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", contended_execute)
    return attempts


def _seed_outfit_with_assignments(db_session, name: str):
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
    scene = Scene(
        chapter_id=chapter.id,
        ordinal=1,
        outfit_assignments={character.id: outfit.id},
    )
    db_session.add(scene)
    db_session.flush()
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
    return scene, panel, outfit


def test_delete_outfit_409s_when_scene_claims_never_land(client, db_session, monkeypatch):
    """Every scene claim defeated → the teardown must 409 after exhausting its
    retries, leaving the outfit and the scene assignment untouched (no partial
    teardown masked by a truthy Result)."""
    project = Project(name="场景拆除竞态")
    db_session.add(project)
    db_session.flush()
    character = Character(project_id=project.id, primary_name="林澈")
    db_session.add(character)
    db_session.flush()
    outfit = Outfit(project_id=project.id, character_id=character.id, name="制服")
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add_all([outfit, chapter])
    db_session.flush()
    scene = Scene(
        chapter_id=chapter.id,
        ordinal=1,
        outfit_assignments={character.id: outfit.id},
    )
    db_session.add(scene)
    db_session.commit()

    attempts = _defeat_scene_or_panel_claims(
        db_session, "scenes", monkeypatch, lose_first=99
    )

    response = client.delete(f"/api/v1/outfits/{outfit.id}")

    assert response.status_code == 409, response.text
    assert attempts["n"] == 3
    db_session.expire_all()
    # The rollback discarded the whole teardown unit: the outfit survives and
    # the scene assignment is untouched.
    assert db_session.get(Outfit, outfit.id) is not None
    row = db_session.get(Scene, scene.id)
    assert row.outfit_assignments == {character.id: outfit.id}


def test_delete_outfit_409s_when_panel_claims_never_land(client, db_session, monkeypatch):
    """Same exhausted-retry 409 for the panel half of the teardown."""
    project = Project(name="分镜拆除竞态")
    db_session.add(project)
    db_session.flush()
    character = Character(project_id=project.id, primary_name="林澈")
    db_session.add(character)
    db_session.flush()
    outfit = Outfit(project_id=project.id, character_id=character.id, name="制服")
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add_all([outfit, chapter])
    db_session.flush()
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

    attempts = _defeat_scene_or_panel_claims(
        db_session, "panels", monkeypatch, lose_first=99
    )

    response = client.delete(f"/api/v1/outfits/{outfit.id}")
    print("DBG status:", response.status_code)
    assert response.status_code == 409, response.text
    assert attempts["n"] == 3
    db_session.expire_all()
    assert db_session.get(Outfit, outfit.id) is not None
    row = db_session.get(Panel, panel.id)
    assert row.outfits == {character.id: outfit.id}
    assert row.version >= 1


def test_delete_outfit_cleans_after_losing_one_scene_claim(client, db_session, monkeypatch):
    """Losing a single scene claim must not fail the teardown: the retry
    re-reads fresh state and lands the cleanup (204, outfit deleted)."""
    project = Project(name="场景拆除重试")
    db_session.add(project)
    db_session.flush()
    character = Character(project_id=project.id, primary_name="林澈")
    db_session.add(character)
    db_session.flush()
    outfit = Outfit(project_id=project.id, character_id=character.id, name="制服")
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    db_session.add_all([outfit, chapter])
    db_session.flush()
    scene = Scene(
        chapter_id=chapter.id,
        ordinal=1,
        outfit_assignments={character.id: outfit.id},
    )
    db_session.add(scene)
    db_session.commit()

    attempts = _defeat_scene_or_panel_claims(
        db_session, "scenes", monkeypatch, lose_first=1
    )

    response = client.delete(f"/api/v1/outfits/{outfit.id}")

    assert response.status_code == 204, response.text
    assert attempts["n"] == 1
    db_session.expire_all()
    row = db_session.get(Scene, scene.id)
    assert row.outfit_assignments == {}
    assert db_session.get(Outfit, outfit.id) is None
