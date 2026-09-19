"""G3 long-list memory fixture: extend the seeded DB with a Dataset-L style project.

Adds one project with 120 manga pages (1 panel each, storyboard geometry) and
600 page candidates (5 per page) each backed by a deterministic PNG asset, so
the library/grid renders a genuinely long list. Run AFTER seed_fixed_dataset
on the NATIVE user-data database only; the web side is not needed for the
memory measurement.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _path in (str(_REPO / "apps" / "api"), str(_REPO / "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.states import Resolution
from app.models import (
    Asset,
    Chapter,
    GenerationBatch,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Panel,
    Project,
)

PAGES = 120
CANDIDATES_PER_PAGE = 5
PNG_SIZE = (256, 340)


def _png(path: Path, size: tuple[int, int], rgb: tuple[int, int, int]) -> bytes:
    from PIL import Image

    Image.new("RGB", size, rgb).save(path, "PNG")
    return path.read_bytes()


def seed_long_list(db_url: str, storage_root: Path) -> dict[str, str]:
    storage_root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url)
    now = datetime.now(UTC)
    with Session(engine) as session:
        project = Project(name="NUI67 长列表内存项目")
        session.add(project)
        session.flush()
        chapter = Chapter(project_id=project.id, title=f"长列表 {PAGES} 页", ordinal=1)
        session.add(chapter)
        session.flush()

        for number in range(1, PAGES + 1):
            page = MangaPage(
                chapter_id=chapter.id,
                page_number=number,
                revision_no=1,
                panel_count=1,
                resolution=Resolution.DRAFT_1K,
                scene_ids=[],
                beat_ids=[],
                source_coverage={"complete": True},
                storyboard_version=1,
            )
            session.add(page)
            session.flush()
            session.add(
                Panel(
                    page_id=page.id,
                    reading_order=1,
                    bounds={"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
                )
            )
            batch = GenerationBatch(
                project_id=project.id, chapter_id=chapter.id, page_id=page.id,
                ordinal=number, generation_kind="PAGE", status="CLOSED",
            )
            session.add(batch)
            session.flush()
            job = GenerationJob(
                project_id=project.id, target_type="PAGE", target_id=page.id,
                job_type="PAGE_GENERATION", status="COMPLETED",
                idempotency_key=f"nui67-long-{number}",
                finished_at=now - timedelta(minutes=PAGES - number),
            )
            session.add(job)
            for ordinal in range(1, CANDIDATES_PER_PAGE + 1):
                rgb = ((number * 37 + ordinal * 53) % 256, (number * 91) % 256, (ordinal * 137) % 256)
                file_key = f"nui67-long-p{number:03d}-c{ordinal}.png"
                target = storage_root / file_key
                payload = _png(target, PNG_SIZE, rgb)
                asset = Asset(
                    project_id=project.id,
                    kind="PAGE_CANDIDATE",
                    original_name=file_key,
                    display_name=file_key,
                    storage_key=file_key,
                    mime_type="image/png",
                    byte_size=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    width=PNG_SIZE[0],
                    height=PNG_SIZE[1],
                    source="GENERATED",
                    status="UPLOADED",
                )
                session.add(asset)
                session.flush()
                from app.services.media import create_thumbnails

                thumbs = create_thumbnails(target, storage_root, asset.id)
                asset.thumbnail_320_key = thumbs[320]
                asset.thumbnail_640_key = thumbs[640]
                session.flush()
                session.add(
                    PageCandidate(
                        batch_id=batch.id, page_id=page.id, ordinal=ordinal,
                        model_alias="image.nano_banana_2", resolution=Resolution.DRAFT_1K,
                        status="COMPLETED", asset_id=asset.id,
                        based_on_storyboard_version=1, is_selected=ordinal == 1,
                    )
                )
            if number % 20 == 0:
                session.commit()
        session.commit()
        result = {"project": project.id, "pages": PAGES, "candidates": PAGES * CANDIDATES_PER_PAGE}
    engine.dispose()
    return result


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: nui67_seed_long_list.py <database_url> <storage_root>")
        return 2
    print(seed_long_list(sys.argv[1], Path(sys.argv[2])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
