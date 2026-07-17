from time import perf_counter
from uuid import uuid4

from sqlalchemy import event, insert

from app.domain.states import Resolution
from app.models import Chapter, GenerationBatch, MangaPage, PageCandidate


def test_library_uses_cursor_pagination_and_bulk_candidate_queries(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "大素材库"}).json()
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db_session.add(page)
    db_session.flush()
    for ordinal in range(1, 26):
        batch = GenerationBatch(
            project_id=project["id"],
            chapter_id=chapter.id,
            page_id=page.id,
            ordinal=ordinal,
        )
        db_session.add(batch)
        db_session.flush()
        db_session.add(
            PageCandidate(
                batch_id=batch.id,
                page_id=page.id,
                ordinal=1,
                model_alias=(
                    "image.nano_banana_2"
                    if ordinal % 2
                    else "image.nano_banana_pro"
                ),
                resolution=Resolution.DRAFT_1K,
                is_favorite=ordinal % 3 == 0,
            )
        )
    db_session.commit()

    select_count = 0

    def count_selects(_connection, _cursor, statement, *_args):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(db_session.bind, "before_cursor_execute", count_selects)
    try:
        first = client.get(
            f"/api/v1/projects/{project['id']}/library?group_by=batch&limit=10"
        )
    finally:
        event.remove(db_session.bind, "before_cursor_execute", count_selects)

    assert first.status_code == 200, first.text
    payload = first.json()
    assert len(payload["groups"]) == 10
    assert payload["limit"] == 10
    assert payload["next_cursor"]
    assert select_count <= 5

    second = client.get(
        f"/api/v1/projects/{project['id']}/library"
        f"?group_by=batch&limit=10&cursor={payload['next_cursor']}"
    )
    assert second.status_code == 200, second.text
    first_ids = {group["batch"]["id"] for group in payload["groups"]}
    second_ids = {group["batch"]["id"] for group in second.json()["groups"]}
    assert len(second_ids) == 10
    assert first_ids.isdisjoint(second_ids)


def test_library_rejects_invalid_cursor(client):
    project = client.post("/api/v1/projects", json={"name": "游标校验"}).json()
    response = client.get(
        f"/api/v1/projects/{project['id']}/library?cursor=not-a-cursor"
    )
    assert response.status_code == 422


def test_library_10000_candidate_hot_request_benchmark(client, db_session):
    project = client.post("/api/v1/projects", json={"name": "万级素材库"}).json()
    chapter = Chapter(project_id=project["id"], title="第一章", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db_session.add(page)
    db_session.flush()
    batch_rows = []
    candidate_rows = []
    for ordinal in range(1, 10_001):
        batch_id = str(uuid4())
        batch_rows.append(
            {
                "id": batch_id,
                "project_id": project["id"],
                "chapter_id": chapter.id,
                "page_id": page.id,
                "ordinal": ordinal,
            }
        )
        candidate_rows.append(
            {
                "id": str(uuid4()),
                "batch_id": batch_id,
                "page_id": page.id,
                "ordinal": 1,
                "model_alias": "image.nano_banana_2",
                "resolution": Resolution.DRAFT_1K,
            }
        )
    db_session.execute(insert(GenerationBatch), batch_rows)
    db_session.execute(insert(PageCandidate), candidate_rows)
    db_session.commit()

    url = f"/api/v1/projects/{project['id']}/library?group_by=batch&limit=30"
    assert client.get(url).status_code == 200
    elapsed_ms = []
    select_count = 0

    def count_selects(_connection, _cursor, statement, *_args):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(db_session.bind, "before_cursor_execute", count_selects)
    try:
        for _ in range(20):
            started = perf_counter()
            response = client.get(url)
            elapsed_ms.append((perf_counter() - started) * 1000)
            assert response.status_code == 200
            assert len(response.json()["groups"]) == 30
    finally:
        event.remove(db_session.bind, "before_cursor_execute", count_selects)

    p95_ms = sorted(elapsed_ms)[18]
    assert select_count <= 20 * 5
    assert p95_ms <= 500
