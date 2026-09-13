"""Reference-image order regressions for PAGE_GENERATE (#642).

The prompt's binding declaration ("本页人物与参考图绑定如下，必须逐项对应") is
built by iterating ``reference_selections`` in dict order, but ImageRequest
only carries image bytes — position is the model's only alignment channel.
The selection load used a set-driven ``Asset.id.in_(...)`` query with no
ORDER BY, so the paid image order followed database return order and the
declared mapping could be crossed (串脸/串服装). The loaded references must
be reordered to the declaration order (character reference first, outfit
reference second, per character; ids outside the selection list stably
last), and derived jobs must keep the original image at position 0.

Real provider calls stay NOT RUN: the paid dispatch is captured through the
offline fake adapter binding (the ``test_worker_handlers_sweep`` pattern).
"""

from datetime import timedelta
from types import SimpleNamespace

from app.config import get_settings
from app.domain.states import JobStatus, PageStatus, Resolution
from app.model_adapters.base import ModelResponse
from app.models import (
    Asset,
    Chapter,
    Character,
    CharacterReference,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    Outfit,
    PageCandidate,
    Panel,
    Project,
    StyleProfile,
    utcnow,
)


def _own_lease(db, job, owner="reference-order-owner") -> str:
    job.attempt_count = max(job.attempt_count or 0, 1)
    job.lease_owner = owner
    job.lease_expires_at = utcnow() + timedelta(minutes=5)
    db.info["job_id"] = job.id
    db.info["job_lease_owner"] = owner
    db.commit()
    return owner


def _capture_binding(monkeypatch) -> dict:
    """Offline paid dispatch: record the ImageRequest, return one fake image."""

    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), (5, 6, 7)).save(buffer, format="PNG")
    result_png = buffer.getvalue()

    captured: dict[str, object] = {}

    class CaptureImageAdapter:
        def generate_page(self, request):
            captured["request"] = request
            return ModelResponse(
                model_id="fake-image",
                request_id="fake-request",
                usage={"fake": True},
                images=(result_png,),
            )

    binding = SimpleNamespace(
        resolved=SimpleNamespace(
            model=SimpleNamespace(id=None, capabilities={}),
            provider=SimpleNamespace(preset_key=None, name="离线测试供应商"),
            connection=SimpleNamespace(id=None, nonsecret_config={}, protocol="HTTP_API"),
            route_reason="EXPLICIT",
            route_score=None,
        ),
        adapter=CaptureImageAdapter(),
        selected_key=None,
    )
    monkeypatch.setattr(
        "app.services.worker_handlers.page_generate.provider._binding",
        lambda *args, **kwargs: binding,
    )
    monkeypatch.setattr(
        "app.services.worker_handlers.page_generate.provider._invoke_provider",
        lambda db, invoke_binding, callback: callback(invoke_binding.adapter),
    )
    return captured


def _seed_two_character_selection_page(db, monkeypatch, tmp_path):
    """A STORYBOARDED page with two selected characters plus one style ref.

    The four selection assets are deliberately inserted in a database order
    that differs from the declaration order, so a missing reorder shows up
    as a crossed image/binding mapping rather than a coincidence.

    Declaration order: char-a, outfit-a, char-b, outfit-b (style not in the
    selection list → stable tail).
    Insertion order:   outfit-b, char-a, outfit-a, char-b, style.
    """

    settings = get_settings()
    monkeypatch.setattr(settings, "upload_root", tmp_path / "uploads")
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    upload_root = settings.upload_root

    project = Project(name="参考顺序")
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, ordinal=1, title="第一章", status="DRAFT")
    db.add(chapter)
    db.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=1,
        status=PageStatus.STORYBOARDED,
        source_coverage={"complete": True},
        scene_ids=["s1"],
        beat_ids=["b1"],
    )
    db.add(page)
    db.flush()
    character_a = Character(project_id=project.id, primary_name="立花", aliases=[])
    character_b = Character(project_id=project.id, primary_name="真岛", aliases=[])
    db.add_all([character_a, character_b])
    db.flush()
    db.add(Panel(page_id=page.id, reading_order=1, characters=[character_a.id]))
    db.add(Panel(page_id=page.id, reading_order=2, characters=[character_b.id]))
    db.flush()

    # Insertion order intentionally crossed against the declaration order.
    spec = {
        "outfit-b": ("outfit-b.png", "OUTFIT_REFERENCE", b"ref-outfit-b"),
        "char-a": ("char-a.png", "CHARACTER_REFERENCE", b"ref-char-a"),
        "outfit-a": ("outfit-a.png", "OUTFIT_REFERENCE", b"ref-outfit-a"),
        "char-b": ("char-b.png", "CHARACTER_REFERENCE", b"ref-char-b"),
        "style": ("style.png", "STYLE_REFERENCE", b"ref-style"),
    }
    assets: dict[str, Asset] = {}
    for index, (key, (name, kind, content)) in enumerate(spec.items()):
        asset = Asset(
            project_id=project.id,
            kind=kind,
            original_name=name,
            storage_key=f"order/{name}",
            mime_type="image/png",
            byte_size=len(content),
            sha256=f"{index:063d}9",
            source="USER_UPLOAD",
            status="UPLOADED",
        )
        db.add(asset)
        db.flush()
        blob = upload_root / asset.storage_key
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(content)
        assets[key] = asset
    db.add(
        CharacterReference(
            character_id=character_a.id, asset_id=assets["char-a"].id, is_canonical=True
        )
    )
    db.add(
        CharacterReference(
            character_id=character_b.id, asset_id=assets["char-b"].id, is_canonical=True
        )
    )
    outfit_a = Outfit(
        project_id=project.id,
        character_id=character_a.id,
        name="立花·常服",
        reference_asset_ids=[assets["outfit-a"].id],
    )
    outfit_b = Outfit(
        project_id=project.id,
        character_id=character_b.id,
        name="真岛·外套",
        reference_asset_ids=[assets["outfit-b"].id],
    )
    db.add_all([outfit_a, outfit_b])
    db.flush()
    style = StyleProfile(
        project_id=project.id,
        name="顺序风格",
        color_mode="color",
        profile={"reference_asset_ids": [assets["style"].id]},
    )
    db.add(style)
    db.flush()
    page.style_id = style.id
    reference_selections = {
        character_a.id: {
            "character_asset_id": assets["char-a"].id,
            "outfit_id": outfit_a.id,
            "outfit_asset_id": assets["outfit-a"].id,
        },
        character_b.id: {
            "character_asset_id": assets["char-b"].id,
            "outfit_id": outfit_b.id,
            "outfit_asset_id": assets["outfit-b"].id,
        },
    }
    db.commit()
    return project, chapter, page, reference_selections, assets


def _queue_job(
    db,
    page: MangaPage,
    *,
    job_type: str,
    request_parameters: dict | None = None,
    ordinal: int = 1,
):
    chapter = db.get(Chapter, page.chapter_id)
    batch = GenerationBatch(
        project_id=chapter.project_id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=ordinal,
        generation_kind="PAGE",
    )
    db.add(batch)
    db.flush()
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="QUEUED",
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={"reference_selections": {}},
    )
    db.add(candidate)
    db.flush()
    job = GenerationJob(
        project_id=chapter.project_id,
        target_type="PAGE_CANDIDATE",
        target_id=candidate.id,
        job_type=job_type,
        status=JobStatus.PREPARING,
        model_alias="image.nano_banana_2",
        request_parameters=request_parameters,
    )
    db.add(job)
    db.flush()
    candidate.job_id = job.id
    db.commit()
    return job, candidate


# --- #642: image order must match the binding declaration order ---------------


def test_reference_images_follow_binding_declaration_order(
    db_session, monkeypatch, tmp_path
):
    """Two characters, each with character + outfit references: the paid
    request's image bytes must arrive in reference_selections order (character
    first, outfit second per character), with the unselected style reference
    stably behind them — the order the prompt's binding JSON declares."""

    from app.services.worker_handlers.page_generate import _run_page_generate

    project, chapter, page, selections, assets = _seed_two_character_selection_page(
        db_session, monkeypatch, tmp_path
    )
    job, candidate = _queue_job(db_session, page, job_type="PAGE_GENERATE", ordinal=1)
    candidate.prompt_snapshot = {"reference_selections": selections}
    _own_lease(db_session, job)

    captured = _capture_binding(monkeypatch)
    _run_page_generate(db_session, job)
    db_session.commit()

    request = captured["request"]
    assert request.reference_images == (
        b"ref-char-a",
        b"ref-outfit-a",
        b"ref-char-b",
        b"ref-outfit-b",
        b"ref-style",
    )
    # The binding JSON the prompt embeds is the very same mapping: binding i
    # names the file whose bytes ride position i.
    db_session.expire_all()
    row = db_session.get(PageCandidate, candidate.id)
    bindings = row.prompt_snapshot["reference_bindings"]
    assert [entry["character_reference"] for entry in bindings] == [
        "char-a.png",
        "char-b.png",
    ]
    assert request.reference_images[0] == b"ref-char-a"
    assert bindings[0]["outfit_reference"] == "outfit-a.png"
    assert request.reference_images[1] == b"ref-outfit-a"


# --- #642: derived jobs keep the original image at position 0 ------------------


def test_derived_job_keeps_original_image_first(db_session, monkeypatch, tmp_path):
    """PAGE_UPSCALE on the same two-character page: the original page stays
    the first reference image (insert(0) contract), followed by the selected
    references in declaration order."""

    from app.services.worker_handlers.page_generate import _run_page_generate

    project, chapter, page, selections, assets = _seed_two_character_selection_page(
        db_session, monkeypatch, tmp_path
    )
    settings = get_settings()
    original_asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="original-page.png",
        storage_key="generated/original-page.png",
        mime_type="image/png",
        byte_size=len(b"original-page-image"),
        sha256="f" * 64,
        source="VERTEX_GENERATED",
        status="GENERATED",
    )
    db_session.add(original_asset)
    db_session.flush()
    blob = settings.storage_root / original_asset.storage_key
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"original-page-image")
    batch = GenerationBatch(
        project_id=project.id,
        chapter_id=chapter.id,
        page_id=page.id,
        ordinal=9,
        generation_kind="PAGE",
    )
    db_session.add(batch)
    db_session.flush()
    original = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        status="READY",
        asset_id=original_asset.id,
        based_on_storyboard_version=page.storyboard_version,
        prompt_snapshot={},
    )
    db_session.add(original)
    db_session.flush()
    job, candidate = _queue_job(
        db_session,
        page,
        job_type="PAGE_UPSCALE",
        request_parameters={"original_candidate_id": original.id},
        ordinal=10,
    )
    candidate.prompt_snapshot = {"reference_selections": selections}
    _own_lease(db_session, job)

    captured = _capture_binding(monkeypatch)
    _run_page_generate(db_session, job)
    db_session.commit()

    assert captured["request"].reference_images == (
        b"original-page-image",
        b"ref-char-a",
        b"ref-outfit-a",
        b"ref-char-b",
        b"ref-outfit-b",
        b"ref-style",
    )
