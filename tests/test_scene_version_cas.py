"""Scene.version write discipline: PATCH /scenes claims the row with an atomic
conditional UPDATE; the sibling writers (bind-asset, scene outfits, outfit
teardown) used blind read-modify-write increments, so two concurrent writers
both computed N+1 from N — collapsing the CAS bump of whichever writer
committed in between and silently losing its field writes while every client
saw 200. The tests use the stale-identity-map technique (expire_on_commit=False
keeps the pre-bump copy) to stand in for a request that loaded the scene before
the concurrent writer committed."""

from sqlalchemy import select, update as sa_update

from app.models import Chapter, Character, Project, Scene


def _seed_scene(db_session, name: str) -> Scene:
    project = Project(name=name)
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="第一章", ordinal=1)
    character = Character(project_id=project.id, primary_name="林澈")
    db_session.add_all([chapter, character])
    db_session.flush()
    scene = Scene(
        chapter_id=chapter.id,
        ordinal=1,
        location="教室",
        weather="小雨",
        time_label="傍晚",
    )
    db_session.add(scene)
    db_session.commit()
    return scene


def _bump_concurrently(db_session, scene_id: str) -> int:
    """Commit a concurrent scene mutation: version +1 plus a field write the
    route under test must not clobber. Returns the post-bump version."""
    db_session.execute(
        sa_update(Scene)
        .where(Scene.id == scene_id)
        .values(version=Scene.version + 1, location="厨房")
        .execution_options(synchronize_session=False)
    )
    db_session.commit()
    return db_session.scalar(select(Scene.version).where(Scene.id == scene_id))


def test_bind_scene_asset_rejects_stale_scene_instead_of_clobbering(
    client, db_session
):
    scene = _seed_scene(db_session, "绑定竞态")
    version_after_bump = _bump_concurrently(db_session, scene.id)

    response = client.patch(
        f"/api/v1/scenes/{scene.id}/bind-asset",
        json={"scene_asset_id": None},
    )

    assert response.status_code == 409, response.text
    db_session.expire_all()
    row = db_session.get(Scene, scene.id)
    # The concurrent writer's field write survived.
    assert row.location == "厨房"
    assert row.version == version_after_bump


def test_assign_scene_outfits_rejects_stale_scene_instead_of_clobbering(
    client, db_session
):
    scene = _seed_scene(db_session, "服装竞态")
    chapter = db_session.get(Chapter, scene.chapter_id)
    character = (
        db_session.query(Character).filter(Character.project_id == chapter.project_id).first()
    )
    version_after_bump = _bump_concurrently(db_session, scene.id)

    response = client.patch(
        f"/api/v1/scenes/{scene.id}/outfits",
        json={"assignments": {character.id: ""}},
    )

    assert response.status_code == 409, response.text
    db_session.expire_all()
    row = db_session.get(Scene, scene.id)
    assert row.location == "厨房"
    assert row.version == version_after_bump
