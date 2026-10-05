"""P0-3 创作留痕证明包回归：导出产物内嵌脱敏制作元数据 + 平台披露模板。

规格：docs/market-research/功能计划建议.md P0-3；契约修订见
docs/pub-01b-webtoon-export-contract.md §2/§4。
"""

import json
import zipfile

import pytest
from app.config import get_settings
from app.domain.states import Resolution
from app.models import (
    Asset,
    Chapter,
    ExportBundle,
    GenerationBatch,
    GenerationJob,
    GenerationRecord,
    InspectionResult,
    MangaPage,
    PageCandidate,
    PageSourceSegment,
    Project,
    SourceRevision,
    SourceSegment,
)
from app.schemas import ExportRequest
from app.services.export_provenance import (
    build_disclosure_text,
    build_export_provenance,
    provenance_token_fragment,
)
from pydantic import ValidationError
from sqlalchemy import select

REQUIRED_QUALITY_CATEGORIES = ("SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY")


@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setattr(get_settings(), "storage_root", root)
    return root


def _seed_ready_chapter(db) -> dict:
    """一章一页、五类检查全过、达到生产通过门禁的最小数据集。"""
    project = Project(name="留痕项目")
    db.add(project)
    db.flush()
    chapter = Chapter(project_id=project.id, ordinal=1, title="第一章")
    db.add(chapter)
    db.flush()
    source_revision = SourceRevision(
        chapter_id=chapter.id,
        revision=1,
        source_type="PASTE",
        original_text="原作全文",
        sha256="a" * 64,
        character_count=4,
    )
    db.add(source_revision)
    db.flush()
    segment = SourceSegment(
        source_revision_id=source_revision.id,
        ordinal=1,
        text="原作全文",
        start_offset=0,
        end_offset=4,
        sha256="b" * 64,
    )
    db.add(segment)
    batch = GenerationBatch(project_id=project.id, chapter_id=chapter.id, ordinal=1)
    db.add(batch)
    db.flush()
    asset = Asset(
        project_id=project.id,
        kind="page_candidate",
        original_name="page-0001.png",
        storage_key="test/page-0001.png",
        mime_type="image/png",
        byte_size=1024,
        width=512,
        height=512,
        sha256="c" * 64,
        source="AI_GENERATED",
        status="GENERATED",
    )
    db.add(asset)
    db.flush()
    page = MangaPage(
        chapter_id=chapter.id,
        page_number=1,
        storyboard_version=3,
        source_coverage={"complete": True, "ranges": [{"start": 0, "end": 4}]},
    )
    db.add(page)
    db.flush()
    db.add(PageSourceSegment(page_id=page.id, source_segment_id=segment.id))
    candidate = PageCandidate(
        batch_id=batch.id,
        page_id=page.id,
        ordinal=1,
        model_alias="image.test_model",
        resolution=Resolution.DRAFT_1K,
        status="INSPECTED",
        asset_id=asset.id,
        is_selected=True,
        prompt_snapshot={
            "template": "page-v2.2.0",
            "checksum": "d" * 64,
            "operation": "PAGE_GENERATE",
            "prompt_preview": "敏感提示词原文不应出现在留痕中",
        },
    )
    db.add(candidate)
    db.flush()
    job = GenerationJob(
        project_id=project.id,
        target_type="PAGE",
        target_id=page.id,
        job_type="PAGE_GENERATE",
    )
    db.add(job)
    db.flush()
    record = GenerationRecord(
        job_id=job.id,
        provider="vertex",
        model_id="imagen-test",
        location="local",
        prompt_template="page-v2.2.0",
        prompt_version="page-v2.2.0",
        prompt_checksum="d" * 64,
    )
    db.add(record)
    db.flush()
    candidate.generation_record_id = record.id
    page.selected_candidate_id = candidate.id
    page.selected_candidate_ack_version = page.storyboard_version
    page.continuity_status = "PASSED"
    for category in REQUIRED_QUALITY_CATEGORIES:
        db.add(
            InspectionResult(
                candidate_id=candidate.id,
                storyboard_version=page.storyboard_version,
                category=category,
                outcome="PASS",
            )
        )
    db.commit()
    return {
        "project": project,
        "chapter": chapter,
        "page": page,
        "candidate": candidate,
        "segment": segment,
    }


# ---------- 聚合器单元层 ----------


def test_provenance_aggregates_adoption_review_inspections_source_and_model(db_session):
    seeded = _seed_ready_chapter(db_session)
    document = build_export_provenance(
        db_session,
        project=seeded["project"],
        chapter=seeded["chapter"],
        pages=[seeded["page"]],
        disclosure_platform=None,
    )
    assert document["schema_version"] == "1.0"
    page = document["pages"][0]
    assert page["page_number"] == 1
    # 人工校对确认：ack 版本与分镜版本一致 + 候选仍为采用态。
    assert page["human_review"]["manual_text_confirmed"] is True
    assert page["human_review"]["ack_current"] is True
    assert page["adoption"]["candidate_id"] == seeded["candidate"].id
    assert page["adoption"]["asset"]["sha256"] == "c" * 64
    # 五类检查全过；PRESENCE 缺类如实标注未完成（历史豁免）。
    assert page["inspections"]["SPEAKER"]["outcome"] == "PASS"
    assert page["inspections"]["PRESENCE"]["outcome"] == "NOT_RUN"
    assert page["inspections"]["PRESENCE"]["complete"] is False
    # 原作来源区间。
    assert page["source_segments"] == [
        {
            "source_segment_id": seeded["segment"].id,
            "sha256": "b" * 64,
            "start_offset": 0,
            "end_offset": 4,
        }
    ]
    # prompt_snapshot 只留版本/校验和/操作类型。
    assert page["prompt"] == {
        "prompt_version": "page-v2.2.0",
        "checksum": "d" * 64,
        "operation": "PAGE_GENERATE",
    }
    # 模型身份：候选别名 + GenerationRecord 四元组。
    assert page["model"]["model_alias"] == "image.test_model"
    assert page["model"]["generation_record"]["provider"] == "vertex"
    assert page["model"]["generation_record"]["prompt_checksum"] == "d" * 64


def test_provenance_marks_missing_checks_as_not_run(db_session):
    """前置条件：缺检类别如实标注「未完成」而非缺省通过。"""
    seeded = _seed_ready_chapter(db_session)
    db_session.execute(
        InspectionResult.__table__.delete().where(
            InspectionResult.category.in_(["PROP", "CONTINUITY"])
        )
    )
    db_session.commit()
    document = build_export_provenance(
        db_session,
        project=seeded["project"],
        chapter=seeded["chapter"],
        pages=[seeded["page"]],
        disclosure_platform=None,
    )
    page = document["pages"][0]
    assert page["inspections"]["PROP"]["outcome"] == "NOT_RUN"
    assert page["inspections"]["PROP"]["complete"] is False
    assert page["inspections"]["CONTINUITY"]["complete"] is False
    assert page["inspections"]["CHARACTER"]["outcome"] == "PASS"


def test_provenance_flags_stale_ack_as_unconfirmed(db_session):
    """分镜改版后 ack 版本不再一致，留痕不再宣称人工确认成立。"""
    seeded = _seed_ready_chapter(db_session)
    page = seeded["page"]
    page.storyboard_version = 4  # ack_version 仍是 3
    db_session.commit()
    document = build_export_provenance(
        db_session,
        project=seeded["project"],
        chapter=seeded["chapter"],
        pages=[page],
        disclosure_platform=None,
    )
    review = document["pages"][0]["human_review"]
    assert review["manual_text_confirmed"] is False
    assert review["ack_current"] is False


def test_provenance_sanitization_no_prompt_text_or_paths(db_session):
    """脱敏红线：prompt 原文、storage_key、本地路径不得出现在留痕中。"""
    seeded = _seed_ready_chapter(db_session)
    document = build_export_provenance(
        db_session,
        project=seeded["project"],
        chapter=seeded["chapter"],
        pages=[seeded["page"]],
        disclosure_platform=None,
    )
    text = json.dumps(document, ensure_ascii=False)
    assert "敏感提示词原文" not in text
    assert "test/page-0001.png" not in text
    assert "prompt_preview" not in text


def test_disclosure_templates_platform_clauses_and_endorsement():
    kdp = build_disclosure_text(
        "KDP", project_name="P", chapter_title="C", page_count=2, generated_at="t"
    )
    assert "kdp.amazon.com" in kdp
    assert "无平台背书" not in kdp
    steam = build_disclosure_text(
        "STEAM", project_name="P", chapter_title="C", page_count=2, generated_at="t"
    )
    assert "2024" in steam
    # 无公开专条平台：通用模板必须显式标注「无平台背书」。
    webtoon = build_disclosure_text(
        "WEBTOON", project_name="P", chapter_title="C", page_count=2, generated_at="t"
    )
    assert "无平台背书" in webtoon
    generic = build_disclosure_text(
        "GENERIC", project_name="P", chapter_title="C", page_count=2, generated_at="t"
    )
    assert "无平台背书" in generic


def test_provenance_token_fragment_distinguishes_parameters():
    """幂等 token 分量：开/关留痕与不同平台必须产生不同分量。"""
    assert provenance_token_fragment(False, None) != provenance_token_fragment(True, None)
    assert provenance_token_fragment(True, "KDP") != provenance_token_fragment(True, "STEAM")
    assert provenance_token_fragment(True, None) != provenance_token_fragment(True, "KDP")


# ---------- 路由层 ----------


def test_export_schema_rejects_unknown_disclosure_platform(db_session):
    with pytest.raises(ValidationError):
        ExportRequest(
            export_type="PNG", include_provenance=True, disclosure_platform="NOPE"
        )


def test_export_schema_requires_provenance_for_disclosure(db_session):
    with pytest.raises(ValidationError):
        ExportRequest(export_type="JSON", disclosure_platform="KDP")


def test_json_export_embeds_provenance(client, db_session, storage_root):
    seeded = _seed_ready_chapter(db_session)
    response = client.post(
        f"/api/v1/chapters/{seeded['chapter'].id}/exports",
        json={"export_type": "JSON", "include_provenance": True, "disclosure_platform": "KDP"},
    )
    assert response.status_code == 201, response.json()
    bundle = db_session.get(ExportBundle, response.json()["id"])
    document = json.loads((storage_root / bundle.storage_key).read_text(encoding="utf-8"))
    assert document["schema_version"] == "1.1"
    provenance = document["provenance"]
    assert provenance["pages"][0]["human_review"]["manual_text_confirmed"] is True
    assert provenance["disclosure"][0]["platform"] == "KDP"
    assert provenance["disclosure"][0]["platform_endorsed"] is True


def test_json_export_without_provenance_keeps_schema_10(client, db_session, storage_root):
    seeded = _seed_ready_chapter(db_session)
    response = client.post(
        f"/api/v1/chapters/{seeded['chapter'].id}/exports", json={"export_type": "JSON"}
    )
    assert response.status_code == 201
    bundle = db_session.get(ExportBundle, response.json()["id"])
    document = json.loads((storage_root / bundle.storage_key).read_text(encoding="utf-8"))
    assert document["schema_version"] == "1.0"
    assert "provenance" not in document


def test_png_zip_carries_provenance_and_disclosure_member(
    client, db_session, storage_root, tmp_path
):
    seeded = _seed_ready_chapter(db_session)
    # 真实图片文件供 zip 打包。
    from PIL import Image

    blob = tmp_path / "page-0001.png"
    Image.new("RGB", (512, 512), color="navy").save(blob, format="PNG")
    asset = db_session.scalars(select(Asset)).first()
    asset.storage_key = "page-0001.png"
    blob.rename(storage_root / "page-0001.png")
    db_session.commit()

    response = client.post(
        f"/api/v1/chapters/{seeded['chapter'].id}/exports",
        json={
            "export_type": "PNG",
            "include_provenance": True,
            "disclosure_platform": "WEBTOON",
        },
    )
    assert response.status_code == 201, response.json()
    bundle = db_session.get(ExportBundle, response.json()["id"])
    with zipfile.ZipFile(storage_root / bundle.storage_key) as archive:
        names = archive.namelist()
        assert "provenance.json" in names
        assert "disclosure-webtoon.txt" in names
        provenance = json.loads(archive.read("provenance.json"))
        disclosure = archive.read("disclosure-webtoon.txt").decode("utf-8")
    assert provenance["pages"][0]["inspections"]["SPEAKER"]["outcome"] == "PASS"
    assert "无平台背书" in disclosure


def test_pdf_export_writes_provenance_sidecar(client, db_session, storage_root, tmp_path):
    seeded = _seed_ready_chapter(db_session)
    from PIL import Image

    blob = tmp_path / "page-0001.png"
    Image.new("RGB", (512, 512), color="navy").save(blob, format="PNG")
    asset = db_session.scalars(select(Asset)).first()
    asset.storage_key = "page-0001.png"
    blob.rename(storage_root / "page-0001.png")
    db_session.commit()

    response = client.post(
        f"/api/v1/chapters/{seeded['chapter'].id}/exports",
        json={"export_type": "PDF", "include_provenance": True},
    )
    assert response.status_code == 201, response.json()
    bundle = db_session.get(ExportBundle, response.json()["id"])
    pdf_path = storage_root / bundle.storage_key
    assert pdf_path.is_file()
    sidecar = pdf_path.with_name(f"{pdf_path.stem}.provenance.json")
    assert sidecar.is_file()
    provenance = json.loads(sidecar.read_text(encoding="utf-8"))
    assert provenance["pages"][0]["model"]["model_alias"] == "image.test_model"


def test_pdf_download_repackages_provenance_into_zip(
    client, db_session, storage_root, tmp_path
):
    """P0-3 留痕证明包必须随下载到用户手上：PDF 导出带 sidecar 时下载返回
    PDF + provenance.json + disclosure-<platform>.txt 的 zip，而非裸 PDF。"""
    seeded = _seed_ready_chapter(db_session)
    from PIL import Image

    blob = tmp_path / "page-0001.png"
    Image.new("RGB", (512, 512), color="navy").save(blob, format="PNG")
    asset = db_session.scalars(select(Asset)).first()
    asset.storage_key = "page-0001.png"
    blob.rename(storage_root / "page-0001.png")
    db_session.commit()

    response = client.post(
        f"/api/v1/chapters/{seeded['chapter'].id}/exports",
        json={
            "export_type": "PDF",
            "include_provenance": True,
            "disclosure_platform": "KDP",
        },
    )
    assert response.status_code == 201, response.json()
    bundle = db_session.get(ExportBundle, response.json()["id"])
    pdf_path = storage_root / bundle.storage_key
    assert pdf_path.suffix == ".pdf"

    downloaded = client.get(f"/api/v1/exports/{bundle.id}/download")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["content-type"] == "application/zip"
    assert downloaded.headers["content-disposition"].endswith(
        f'filename="{pdf_path.stem}-with-provenance.zip"'
    )
    import io

    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        names = archive.namelist()
        assert pdf_path.name in names
        assert "provenance.json" in names
        assert "disclosure-kdp.txt" in names
        provenance = json.loads(archive.read("provenance.json"))
        disclosure = archive.read("disclosure-kdp.txt").decode("utf-8")
    assert provenance["pages"][0]["model"]["model_alias"] == "image.test_model"
    assert provenance["disclosure"][0]["platform"] == "KDP"
    assert "AI 使用披露声明" in disclosure
    # The server-side sidecar stays in place — the zip is a per-download repack.
    assert (pdf_path.with_name(f"{pdf_path.stem}.provenance.json")).is_file()


def test_pdf_download_without_provenance_serves_raw_pdf(
    client, db_session, storage_root, tmp_path
):
    """未开留痕的 PDF 导出仍按原样返回 application/pdf。"""
    seeded = _seed_ready_chapter(db_session)
    from PIL import Image

    blob = tmp_path / "page-0001.png"
    Image.new("RGB", (512, 512), color="navy").save(blob, format="PNG")
    asset = db_session.scalars(select(Asset)).first()
    asset.storage_key = "page-0001.png"
    blob.rename(storage_root / "page-0001.png")
    db_session.commit()

    response = client.post(
        f"/api/v1/chapters/{seeded['chapter'].id}/exports",
        json={"export_type": "PDF"},
    )
    assert response.status_code == 201, response.json()
    bundle = db_session.get(ExportBundle, response.json()["id"])

    downloaded = client.get(f"/api/v1/exports/{bundle.id}/download")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/pdf"
    assert downloaded.content[:5] == b"%PDF-"


def test_webtoon_manifest_embeds_provenance_schema_11(
    client, db_session, storage_root, tmp_path
):
    seeded = _seed_ready_chapter(db_session)
    from PIL import Image

    blob = tmp_path / "page-0001.png"
    Image.new("RGB", (800, 800), color="navy").save(blob, format="PNG")
    asset = db_session.scalars(select(Asset)).first()
    asset.storage_key = "page-0001.png"
    blob.rename(storage_root / "page-0001.png")
    db_session.commit()

    response = client.post(
        f"/api/v1/chapters/{seeded['chapter'].id}/exports",
        json={
            "export_type": "WEBTOON",
            "width": 800,
            "include_provenance": True,
            "disclosure_platform": "GENERIC",
        },
    )
    assert response.status_code == 201, response.json()
    bundle = db_session.get(ExportBundle, response.json()["id"])
    with zipfile.ZipFile(storage_root / bundle.storage_key) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["schema_version"] == "1.1"
    assert manifest["provenance"]["pages"][0]["human_review"]["ack_current"] is True
    assert manifest["provenance"]["disclosure"][0]["platform_endorsed"] is False


def test_provenance_parameters_change_export_token(client, db_session, storage_root):
    """幂等修订：同候选集不同留痕参数 → 不同 token → 不命中同一陈旧包。"""
    seeded = _seed_ready_chapter(db_session)
    plain = client.post(
        f"/api/v1/chapters/{seeded['chapter'].id}/exports", json={"export_type": "JSON"}
    )
    traced = client.post(
        f"/api/v1/chapters/{seeded['chapter'].id}/exports",
        json={"export_type": "JSON", "include_provenance": True},
    )
    assert plain.status_code == traced.status_code == 201
    plain_bundle = db_session.get(ExportBundle, plain.json()["id"])
    traced_bundle = db_session.get(ExportBundle, traced.json()["id"])
    assert plain_bundle.storage_key != traced_bundle.storage_key
    # token 前缀不同：worker 重执行 reuse_existing 不会返回无留痕陈旧包。
    plain_token = plain_bundle.storage_key.rsplit("/", 1)[-1].split("-")[0]
    traced_token = traced_bundle.storage_key.rsplit("/", 1)[-1].split("-")[0]
    assert plain_token != traced_token


def test_reuse_existing_respects_provenance_token(
    db_session, storage_root, monkeypatch
):
    """worker 侧 reuse_existing：开了留痕的重复执行返回留痕包，而非裸包。"""
    from app.api.routes.exports import create_export

    seeded = _seed_ready_chapter(db_session)
    traced = create_export(
        seeded["chapter"].id,
        ExportRequest(export_type="JSON", include_provenance=True),
        db_session,
        reuse_existing=True,
    )
    again = create_export(
        seeded["chapter"].id,
        ExportRequest(export_type="JSON", include_provenance=True),
        db_session,
        reuse_existing=True,
    )
    assert again.id == traced.id
    # 裸请求不复用留痕包（token 不同）。
    plain = create_export(
        seeded["chapter"].id,
        ExportRequest(export_type="JSON"),
        db_session,
        reuse_existing=True,
    )
    assert plain.id != traced.id
