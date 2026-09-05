"""Regression: duplicate style-analysis POSTs must not mint duplicate jobs.

``analyze_style`` and ``draft_style_palette`` bumped ``style.version``
before computing the idempotency key from the already-incremented value,
so every identical retry produced a fresh key and a fresh (paid)
STYLE_ANALYZE job. Sequential retries are now rejected with 409 while an
analysis job is still open (failure/cancellation keeps re-analysis
possible), and the key uses the pre-bump version to collapse true
concurrent duplicates. Palette keys also mix in the atmosphere digest so
distinct intents never collapse.
"""

from app.models import Asset, GenerationJob, Project, StyleProfile


def _style(db, name: str) -> StyleProfile:
    project = Project(name=name)
    db.add(project)
    db.flush()
    reference = Asset(
        project_id=project.id,
        kind="style_reference",
        original_name=f"{name}.png",
        storage_key=f"{name}.png",
        mime_type="image/png",
        byte_size=10,
        sha256=name.encode().hex().ljust(64, "0")[:64],
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    db.add(reference)
    db.flush()
    style = StyleProfile(
        project_id=project.id,
        name=name,
        color_mode="color",
        profile={"reference_asset_ids": [reference.id]},
        status="DRAFT",
    )
    db.add(style)
    db.commit()
    return style


def _style_jobs(db, style_id: str) -> list[str]:
    return [
        job.id
        for job in db.query(GenerationJob)
        .filter(
            GenerationJob.target_type == "STYLE",
            GenerationJob.target_id == style_id,
            GenerationJob.job_type == "STYLE_ANALYZE",
        )
        .all()
    ]


def test_duplicate_analyze_post_is_rejected_with_single_job(client, db_session):
    style = _style(db_session, "analyze-dedupe")

    first = client.post(f"/api/v1/styles/{style.id}/analyze")
    second = client.post(f"/api/v1/styles/{style.id}/analyze")

    assert first.status_code == 202
    assert second.status_code == 409
    assert len(_style_jobs(db_session, style.id)) == 1


def test_palette_retry_rejected_and_distinct_atmosphere_runs_after_completion(
    client, db_session
):
    style = _style(db_session, "palette-dedupe")

    first = client.post(
        f"/api/v1/styles/{style.id}/palette-draft",
        json={"atmosphere": "明亮"},
    )
    duplicate = client.post(
        f"/api/v1/styles/{style.id}/palette-draft",
        json={"atmosphere": "明亮"},
    )

    assert first.status_code == 202
    assert duplicate.status_code == 409

    job = (
        db_session.query(GenerationJob)
        .filter(GenerationJob.id == first.json()["id"])
        .one()
    )
    job.status = "COMPLETED"
    db_session.commit()

    other = client.post(
        f"/api/v1/styles/{style.id}/palette-draft",
        json={"atmosphere": "昏暗"},
    )
    assert other.status_code == 202
    assert other.json()["id"] != first.json()["id"]
    assert len(_style_jobs(db_session, style.id)) == 2
