"""Atomic storyboard geometry saves (V02-30 contract §10).

The whole-page PUT is a full-snapshot overwrite guarded by the page-level
``storyboard_version`` anchor. Idempotent replay (§10.2) is persisted as the
last command tuple ``(request_id, payload_hash, resulting_storyboard_version)``
on the ``MangaPage`` row itself — the contract forbids a command-history
table, so only the latest PUT per page replays, and it does so across process
restarts: a stored-hash match short-circuits before the version check and
never bumps ``storyboard_version`` a second time.

Reading-order renumbering shifts current orders out of the target range and
flushes before assigning the final 1..n values, which keeps the
``(page_id, reading_order)`` / ``(panel_id, reading_order)`` unique constraints
satisfied on both SQLite and PostgreSQL with immediate constraint checks.
"""

import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.states import ensure_unlocked
from app.domain.storyboard_layout import (
    canonical_bubble,
    canonical_panel_bounds,
    canonical_panel_geometry,
)
from app.models import Dialogue, MangaPage, Panel
from app.schemas import StoryboardGeometrySave
from app.services.editor import mark_pages_for_review, mark_storyboard_changed


def _payload_digest(payload: StoryboardGeometrySave) -> str:
    dump = payload.model_dump(mode="json", exclude={"request_id"})
    canonical = json.dumps(dump, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reorder_page_panels(db: Session, page: MangaPage, panel_ids: list[str]) -> None:
    """Renumber a page's whole reading order to 1..n in one transaction."""
    panels = {
        panel.id: panel
        for panel in db.scalars(select(Panel).where(Panel.page_id == page.id))
    }
    if len(set(panel_ids)) != len(panel_ids):
        raise HTTPException(status_code=422, detail="分镜格顺序存在重复 ID")
    if set(panel_ids) != set(panels):
        raise HTTPException(status_code=409, detail="顺序必须包含页面全部分镜格，请刷新后重试")
    try:
        offset = len(panels) + 10000
        for panel in panels.values():
            panel.reading_order += offset
        db.flush()
        for index, panel_id in enumerate(panel_ids, start=1):
            panels[panel_id].reading_order = index
        mark_storyboard_changed(page)
        mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
        db.commit()
    except Exception:
        db.rollback()
        raise


def save_storyboard_geometry(
    db: Session, page: MangaPage, payload: StoryboardGeometrySave
) -> None:
    """Apply the atomic whole-page geometry snapshot (idempotent per request_id).

    Replay resolves from the persisted tuple on the page row, so a retry after
    a lost response succeeds even though ``storyboard_version`` already
    incremented — including from a fresh process.
    """
    digest = _payload_digest(payload)
    stored = page.geometry_save_command
    if stored and stored.get("request_id") == payload.request_id:
        if stored.get("payload_hash") != digest:
            raise HTTPException(
                status_code=409, detail="request_id 已用于不同内容，请更换 request_id 后重试"
            )
        return
    try:
        _apply_storyboard_geometry(db, page, payload, digest)
    except Exception:
        db.rollback()
        raise


def _apply_storyboard_geometry(
    db: Session, page: MangaPage, payload: StoryboardGeometrySave, digest: str
) -> None:
    if payload.storyboard_version != page.storyboard_version:
        raise HTTPException(status_code=409, detail="分镜版本已变化，请刷新画布后重试")

    panels = {
        panel.id: panel
        for panel in db.scalars(select(Panel).where(Panel.page_id == page.id))
    }
    payload_panel_ids = [item.panel_id for item in payload.panels]
    if len(set(payload_panel_ids)) != len(payload_panel_ids):
        raise HTTPException(status_code=422, detail="载荷中分镜格 ID 重复")
    if set(payload_panel_ids) != set(panels):
        raise HTTPException(
            status_code=409,
            detail="载荷分镜格集合与当前页面不一致，请刷新画布后重试",
        )

    dialogues = {
        dialogue.id: dialogue
        for dialogue in db.scalars(
            select(Dialogue).where(Dialogue.panel_id.in_(panels.keys()))
        )
    }
    payload_dialogue_ids = [item.dialogue_id for item in payload.dialogues]
    if len(set(payload_dialogue_ids)) != len(payload_dialogue_ids):
        raise HTTPException(status_code=422, detail="载荷中对白 ID 重复")
    if set(payload_dialogue_ids) != set(dialogues):
        raise HTTPException(
            status_code=409,
            detail="载荷对白集合与当前页面不一致，请刷新画布后重试",
        )

    panel_writes = []
    for item in payload.panels:
        panel = panels[item.panel_id]
        bounds = canonical_panel_bounds(item.bounds.model_dump())
        if item.geometry is not None:
            geometry = canonical_panel_geometry(
                item.geometry.model_dump(), reading_order=item.reading_order
            )
            if geometry["type"] == "rect" and geometry["rect"] != bounds:
                raise HTTPException(status_code=422, detail="geometry.rect 与 bounds 不一致")
        else:
            geometry = None
        panel_writes.append((panel, bounds, geometry, item.reading_order))

    dialogue_writes = []
    for item in payload.dialogues:
        dialogue = dialogues[item.dialogue_id]
        bubble = canonical_bubble(item.bubble.model_dump()) if item.bubble is not None else None
        dialogue_writes.append((dialogue, bubble, item.reading_order))

    panel_orders = sorted(item.reading_order for item in payload.panels)
    if panel_orders != list(range(1, len(panel_orders) + 1)):
        raise HTTPException(status_code=422, detail="分镜格 reading_order 必须是 1..n 的排列")
    dialogue_orders_by_panel: dict[str, list[int]] = {}
    for item in payload.dialogues:
        dialogue_orders_by_panel.setdefault(
            dialogues[item.dialogue_id].panel_id, []
        ).append(item.reading_order)
    for orders in dialogue_orders_by_panel.values():
        if sorted(orders) != list(range(1, len(orders) + 1)):
            raise HTTPException(
                status_code=422, detail="同一分镜格内对白 reading_order 必须是 1..n 的排列"
            )

    for panel, bounds, geometry, _ in panel_writes:
        if bounds == panel.bounds and geometry == panel.geometry:
            continue
        try:
            ensure_unlocked(panel.locked_fields, ["bounds"])
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    panel_offset = len(panels) + 10000
    for panel in panels.values():
        panel.reading_order += panel_offset
    dialogue_offset = len(dialogues) + 10000
    for dialogue in dialogues.values():
        dialogue.reading_order += dialogue_offset
    db.flush()

    for panel, bounds, geometry, reading_order in panel_writes:
        if bounds != panel.bounds or geometry != panel.geometry:
            panel.version += 1
        panel.bounds = bounds
        panel.geometry = geometry
        panel.reading_order = reading_order
    for dialogue, bubble, reading_order in dialogue_writes:
        dialogue.bubble = bubble
        dialogue.reading_order = reading_order
    db.flush()

    mark_storyboard_changed(page)
    # §10.2: persist the last command tuple in the same transaction as the
    # save, so a lost-response retry replays from the row, not process memory.
    page.geometry_save_command = {
        "request_id": payload.request_id,
        "payload_hash": digest,
        "storyboard_version": page.storyboard_version,
    }
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    db.commit()


__all__ = [
    "reorder_page_panels",
    "save_storyboard_geometry",
]
