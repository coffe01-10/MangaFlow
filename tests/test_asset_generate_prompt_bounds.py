"""Paid-prompt bounds regressions for ASSET_GENERATE (#641).

The subject blocks the asset handler embeds into its paid prompt
(profile / components / state_rules / locked_fields / locked_features) are
structured JSON with no field-level API cap — FiniteJsonDict only rejects
non-finite floats and the request body tops out at 2MB — so a hostile or
degenerate blob could bill a megabyte-scale prompt on the image call. These
fields must pass through the page compiler's per-block budget
(``_bound_structured_block`` / ``STRUCTURED_BLOCK_MAX_CHARS``), the same
口径 the PAGE path already enforces inside ``prompt_compiler``.

Real provider calls stay NOT RUN: the paid dispatch is captured through the
offline fake adapter binding (the ``test_worker_handlers_sweep`` pattern),
and reference resolution keeps reading the RAW profile so bounding the
prompt copy never shrinks the reference list.
"""

import json
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace

from app.config import get_settings
from app.domain.states import JobStatus, Resolution
from app.model_adapters.base import ModelResponse
from app.models import (
    Asset,
    AssetCandidate,
    Character,
    CharacterReference,
    GenerationBatch,
    GenerationJob,
    Outfit,
    Project,
    StyleProfile,
    utcnow,
)
from app.services.prompt_compiler import STRUCTURED_BLOCK_MAX_CHARS
from app.services.worker_handlers.asset_generate import _run_asset_generate


def _png_bytes(color: tuple[int, int, int] = (7, 8, 9)) -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _own_lease(db, job, owner="asset-bounds-owner") -> str:
    job.attempt_count = max(job.attempt_count or 0, 1)
    job.lease_owner = owner
    job.lease_expires_at = utcnow() + timedelta(minutes=5)
    db.info["job_id"] = job.id
    db.info["job_lease_owner"] = owner
    db.commit()
    return owner


def _upload_asset(
    db,
    project_id: str,
    *,
    kind: str,
    name: str,
    sha: str,
    content: bytes | None = None,
) -> Asset:
    """A live USER_UPLOAD reference row with a real blob under upload_root."""

    settings = get_settings()
    asset = Asset(
        project_id=project_id,
        kind=kind,
        original_name=name,
        storage_key=f"bounds/{name}",
        mime_type="image/png",
        byte_size=len(content or _png_bytes()),
        sha256=sha,
        source="USER_UPLOAD",
        status="UPLOADED",
    )
    db.add(asset)
    db.flush()
    blob = settings.upload_root / asset.storage_key
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(content if content is not None else _png_bytes())
    return asset


def _capture_binding(monkeypatch) -> dict:
    """Offline paid dispatch: record the ImageRequest, return one fake image."""

    captured: dict[str, object] = {}

    class CaptureImageAdapter:
        def generate_asset(self, request):
            captured["request"] = request
            return ModelResponse(
                model_id="fake-image",
                request_id="fake-request",
                usage={"fake": True},
                images=(_png_bytes(),),
            )

    binding = SimpleNamespace(
        resolved=SimpleNamespace(
            # id=None keeps the catalog_model_id FK writes null (no ai_models
            # row is seeded for this offline dispatch).
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
        "app.services.worker_handlers.asset_generate.provider._binding",
        lambda *args, **kwargs: binding,
    )
    monkeypatch.setattr(
        "app.services.worker_handlers.asset_generate.provider._invoke_provider",
        lambda db, invoke_binding, callback: callback(invoke_binding.adapter),
    )
    return captured


def _seed_job(db, project: Project, batch: GenerationBatch, candidate: AssetCandidate):
    job = GenerationJob(
        project_id=project.id,
        target_type="ASSET_CANDIDATE",
        target_id=candidate.id,
        job_type="ASSET_GENERATE",
        status=JobStatus.PREPARING,
        model_alias="image.nano_banana_2",
    )
    db.add(job)
    db.flush()
    candidate.job_id = job.id
    _own_lease(db, job)
    return job


def _candidate_row(db, candidate_id: str) -> AssetCandidate:
    row = db.get(AssetCandidate, candidate_id)
    db.refresh(row, attribute_names=["prompt_snapshot", "status"])
    return row


def _serialized(block: object) -> str:
    return json.dumps(block, ensure_ascii=False, separators=(",", ":"))


# --- #641: STYLE profile is the issue's megabyte scenario ---------------------


def test_style_giant_profile_bounded_in_paid_prompt(db_session, monkeypatch, tmp_path):
    """A ~MB-scale style.profile must reach the paid prompt only through the
    per-block budget: prompt_preview stays small, the tail of the profile is
    deterministically cut, and reference loading still reads the raw ids."""

    settings = get_settings()
    monkeypatch.setattr(settings, "upload_root", tmp_path / "uploads")
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")

    project = Project(name="风格档案封顶")
    db_session.add(project)
    db_session.flush()
    reference = _upload_asset(
        db_session,
        project.id,
        kind="STYLE_REFERENCE",
        name="style-ref.png",
        sha="1" * 64,
        content=_png_bytes((80, 81, 82)),
    )
    giant_profile = {
        "reference_asset_ids": [reference.id],
        "头部标记": "保留键",
        "填充": "巨" * 400_000,
        "尾部标记": "应被截断",
    }
    style = StyleProfile(
        project_id=project.id,
        name="封顶风格",
        color_mode="color",
        profile=giant_profile,
        status="DRAFT",
    )
    db_session.add(style)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id,
        target_type="STYLE",
        target_id=style.id,
        generation_kind="STYLE_TEST",
        ordinal=1,
    )
    db_session.add(batch)
    db_session.flush()
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant="STYLE_TEST",
        status="QUEUED",
    )
    db_session.add(candidate)
    db_session.flush()
    job = _seed_job(db_session, project, batch, candidate)

    captured = _capture_binding(monkeypatch)
    _run_asset_generate(db_session, job)
    db_session.commit()

    prompt = captured["request"].prompt
    # The unbounded payload would be ~400k chars; the bounded prompt stays
    # within a small multiple of the per-block budget.
    assert len(prompt) < 4 * STRUCTURED_BLOCK_MAX_CHARS
    # Deterministic hard cut: the dict prefix survives, the tail key drops,
    # the filler never rides the paid call.
    assert "头部标记" in prompt
    assert "尾部标记" not in prompt
    assert "巨" * 100 not in prompt
    # The snapshot carries the same bounded text and bounded input block.
    row = _candidate_row(db_session, candidate.id)
    assert row.prompt_snapshot["prompt_preview"] == prompt
    bounded_profile = row.prompt_snapshot["input"]["subject"]["profile"]
    assert len(_serialized(bounded_profile)) <= STRUCTURED_BLOCK_MAX_CHARS
    # Reference resolution is untouched by the bounding: the raw
    # reference_asset_ids still drove the paid request's image list.
    assert captured["request"].reference_images == (_png_bytes((80, 81, 82)),)


# --- #641: OUTFIT components / state_rules / locked_fields -------------------


def test_outfit_structured_fields_bounded_in_paid_prompt(
    db_session, monkeypatch, tmp_path
):
    """OutfitCreate caps none of components/state_rules at field level; each
    embedded block must fit the compiler's per-block budget."""

    settings = get_settings()
    monkeypatch.setattr(settings, "upload_root", tmp_path / "uploads")
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")

    project = Project(name="服装结构封顶")
    db_session.add(project)
    db_session.flush()
    character = Character(project_id=project.id, primary_name="林澈", aliases=[])
    db_session.add(character)
    db_session.flush()
    character_reference = _upload_asset(
        db_session,
        project.id,
        kind="CHARACTER_REFERENCE",
        name="char-ref.png",
        sha="2" * 64,
        content=_png_bytes((83, 84, 85)),
    )
    db_session.add(
        CharacterReference(character_id=character.id, asset_id=character_reference.id)
    )
    outfit_reference = _upload_asset(
        db_session,
        project.id,
        kind="OUTFIT_REFERENCE",
        name="outfit-ref.png",
        sha="3" * 64,
        content=_png_bytes((86, 87, 88)),
    )
    db_session.flush()
    outfit = Outfit(
        project_id=project.id,
        character_id=character.id,
        name="礼服",
        components={"上衣": "衬" * 300_000},
        state_rules={"湿身": "湿" * 300_000},
        locked_fields=["锁" * 100_000],
        reference_asset_ids=[outfit_reference.id],
    )
    db_session.add(outfit)
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id,
        target_type="OUTFIT",
        target_id=outfit.id,
        generation_kind="OUTFIT",
        ordinal=1,
    )
    db_session.add(batch)
    db_session.flush()
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant="OUTFIT_SHEET",
        status="QUEUED",
    )
    db_session.add(candidate)
    db_session.flush()
    job = _seed_job(db_session, project, batch, candidate)

    captured = _capture_binding(monkeypatch)
    _run_asset_generate(db_session, job)
    db_session.commit()

    prompt = captured["request"].prompt
    assert len(prompt) < 4 * STRUCTURED_BLOCK_MAX_CHARS
    assert "衬" * 2_500 not in prompt
    assert "湿" * 2_500 not in prompt
    assert "锁" * 2_500 not in prompt
    row = _candidate_row(db_session, candidate.id)
    subject = row.prompt_snapshot["input"]["subject"]
    for field in ("components", "state_rules", "locked_fields"):
        assert len(_serialized(subject[field])) <= STRUCTURED_BLOCK_MAX_CHARS, field
    # Both reference groups still reached the paid call.
    assert captured["request"].reference_images == (
        _png_bytes((83, 84, 85)),
        _png_bytes((86, 87, 88)),
    )


# --- #641: CHARACTER locked_features ------------------------------------------


def test_character_locked_features_bounded_in_paid_prompt(
    db_session, monkeypatch, tmp_path
):
    """character.locked_features is a free-form JSON list; the embedded block
    must fit the per-block budget instead of riding the prompt unbounded."""

    settings = get_settings()
    monkeypatch.setattr(settings, "upload_root", tmp_path / "uploads")
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")

    project = Project(name="角色锁定封顶")
    db_session.add(project)
    db_session.flush()
    character = Character(
        project_id=project.id,
        primary_name="陈昊",
        aliases=[],
        canonical_description="常规长度描述",
        locked_features=["特" * 300_000, "另" * 50],
    )
    db_session.add(character)
    db_session.flush()
    character_reference = _upload_asset(
        db_session,
        project.id,
        kind="CHARACTER_REFERENCE",
        name="char-ref.png",
        sha="4" * 64,
        content=_png_bytes((90, 91, 92)),
    )
    db_session.add(
        CharacterReference(character_id=character.id, asset_id=character_reference.id)
    )
    db_session.flush()
    batch = GenerationBatch(
        project_id=project.id,
        target_type="CHARACTER",
        target_id=character.id,
        generation_kind="CHARACTER",
        ordinal=1,
    )
    db_session.add(batch)
    db_session.flush()
    candidate = AssetCandidate(
        batch_id=batch.id,
        ordinal=1,
        model_alias="image.nano_banana_2",
        resolution=Resolution.DRAFT_1K,
        variant="SHEET",
        status="QUEUED",
    )
    db_session.add(candidate)
    db_session.flush()
    job = _seed_job(db_session, project, batch, candidate)

    captured = _capture_binding(monkeypatch)
    _run_asset_generate(db_session, job)
    db_session.commit()

    prompt = captured["request"].prompt
    assert len(prompt) < 4 * STRUCTURED_BLOCK_MAX_CHARS
    assert "特" * 2_500 not in prompt
    row = _candidate_row(db_session, candidate.id)
    locked = row.prompt_snapshot["input"]["subject"]["locked_features"]
    assert len(_serialized(locked)) <= STRUCTURED_BLOCK_MAX_CHARS
    assert captured["request"].reference_images == (_png_bytes((90, 91, 92)),)
