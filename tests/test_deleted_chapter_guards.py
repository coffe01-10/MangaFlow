"""Regression: soft-deleted chapters stay inert on export and page reads.

``create_export`` and ``list_pages`` used to fetch the chapter with a bare
existence check, so a soft-deleted chapter could still produce downloadable
export bundles and serve its pages, unlike every other chapter entry point.
The export path additionally adopted deleted assets/candidates silently.
"""

from datetime import UTC, datetime

from app.models import Chapter, MangaPage, Project


def _soft_delete(row) -> None:
    row.deleted_at = datetime.now(UTC)


def test_export_rejects_soft_deleted_chapter(client, db_session):
    project = Project(name="export-gone")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="c1", ordinal=1)
    db_session.add(chapter)
    db_session.commit()
    _soft_delete(chapter)
    db_session.commit()

    response = client.post(
        f"/api/v1/chapters/{chapter.id}/exports",
        json={"export_type": "PNG"},
    )
    assert response.status_code == 404


def test_list_pages_rejects_soft_deleted_chapter(client, db_session):
    project = Project(name="pages-gone")
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(project_id=project.id, title="c1", ordinal=1)
    db_session.add(chapter)
    db_session.flush()
    page = MangaPage(chapter_id=chapter.id, page_number=1)
    db_session.add(page)
    db_session.commit()
    _soft_delete(chapter)
    db_session.commit()

    response = client.get(f"/api/v1/chapters/{chapter.id}/pages")
    assert response.status_code == 404
