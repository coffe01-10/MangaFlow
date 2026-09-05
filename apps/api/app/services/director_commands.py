"""Director command journal, preview, accept/reject and undo (V02-40)."""

from __future__ import annotations

import copy
import json
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.director_commands import (
    PAYLOAD_MAX_BYTES,
    CommandEnvelope,
    CommandGroupStatus,
    CommandStatus,
)
from app.models import (
    CandidateLineage,
    Dialogue,
    DirectorCommand,
    DirectorCommandGroup,
    GenerationJob,
    MangaPage,
    PageCandidate,
    Panel,
    Project,
    Scene,
)
from app.services.candidate_lineage import create_region_regeneration
from app.services.content_workflow import apply_page_layout
from app.services.editor import project_id_for_page
from app.services.job_service import enqueue_job
from app.services.ordinal_allocator import lock_entity
from app.services.storyboard_edits import (
    DIALOGUE_RESTORE_FIELDS,
    PANEL_RESTORE_FIELDS,
    SCENE_RESTORE_FIELDS,
    apply_dialogue_fields,
    apply_panel_fields,
    apply_scene_fields,
    project_id_for_scene,
    restore_dialogue_snapshot,
    restore_panel_snapshot,
    restore_scene_snapshot,
    snapshot_fields,
)

TERMINAL_COMMAND = {
    CommandStatus.EXECUTED,
    CommandStatus.REJECTED,
    CommandStatus.DISCARDED,
    CommandStatus.SUPERSEDED,
    CommandStatus.FAILED,
}


def _http_422(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _http_409(detail: str | dict) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _payload_size(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _copy_json(value):
    return copy.deepcopy(value)


def _command_read(row: DirectorCommand) -> dict:
    return {
        "command_id": row.command_id,
        "command_group_id": row.command_group_id,
        "operation": row.operation,
        "status": row.status,
        "target": row.target,
        "expected_version": row.expected_version,
        "payload": row.payload,
        "source": row.source,
        "diff": row.diff,
        "error": row.error,
        "retry_of_command_id": row.retry_of_command_id,
        "inverse_of_command_id": row.inverse_of_command_id,
        "storyboard_version_after": row.storyboard_version_after,
        "version": row.version,
    }


def _group_read(db: Session, group: DirectorCommandGroup) -> dict:
    rows = list(
        db.scalars(
            select(DirectorCommand)
            .where(DirectorCommand.group_id == group.id)
            .order_by(DirectorCommand.created_at, DirectorCommand.id)
        )
    )
    return {
        "id": group.id,
        "project_id": group.project_id,
        "command_group_id": group.command_group_id,
        "page_id": group.page_id,
        "status": group.status,
        "idempotent_replay": False,
        "commands": [_command_read(row) for row in rows],
        "version": group.version,
    }


def _refresh_group_status(db: Session, group: DirectorCommandGroup) -> None:
    rows = list(
        db.scalars(select(DirectorCommand).where(DirectorCommand.group_id == group.id))
    )
    if not rows:
        group.status = CommandGroupStatus.PROPOSED.value
        return
    statuses = {CommandStatus(row.status) for row in rows}
    if group.status == CommandGroupStatus.DISCARDED.value:
        return
    if statuses <= {CommandStatus.REJECTED, CommandStatus.FAILED}:
        group.status = CommandGroupStatus.REJECTED.value
        return
    if CommandStatus.PROPOSED in statuses:
        group.status = CommandGroupStatus.PROPOSED.value
        return
    executed_like = statuses & {CommandStatus.ACCEPTED, CommandStatus.EXECUTED}
    pending = CommandStatus.PREVIEWED in statuses
    if pending and executed_like:
        group.status = CommandGroupStatus.PARTIALLY_ACCEPTED.value
        return
    if statuses <= TERMINAL_COMMAND:
        if CommandStatus.EXECUTED in statuses and (
            CommandStatus.REJECTED in statuses
            or CommandStatus.DISCARDED in statuses
            or CommandStatus.SUPERSEDED in statuses
            or CommandStatus.FAILED in statuses
        ):
            group.status = CommandGroupStatus.PARTIALLY_REJECTED.value
        elif statuses == {CommandStatus.EXECUTED} or CommandStatus.EXECUTED in statuses:
            group.status = CommandGroupStatus.COMMITTED.value
        else:
            group.status = CommandGroupStatus.PARTIALLY_REJECTED.value
        return
    group.status = CommandGroupStatus.PREVIEWED.value


def _owned_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _load_page(db: Session, project_id: str, page_id: str | None) -> MangaPage | None:
    if not page_id:
        return None
    page = db.get(MangaPage, page_id)
    if not page:
        raise _http_422("目标页不存在")
    if project_id_for_page(db, page) != project_id:
        raise _http_422("目标不属于当前项目")
    return page


def _current_version(entity, scope: str) -> int:
    if scope == "panel":
        return entity.version
    if scope == "page":
        return entity.version
    if scope == "storyboard":
        return entity.storyboard_version
    if scope == "scene":
        return entity.version
    raise _http_422("expected_version.scope 无效")


def _resolve_version_entity(db: Session, envelope: CommandEnvelope):
    scope = envelope.expected_version.scope
    target = envelope.target
    if scope == "panel":
        panel = db.get(Panel, target.panel_id)
        if not panel:
            raise _http_422("目标分镜格不存在")
        return panel
    if scope in {"page", "storyboard"}:
        page = db.get(MangaPage, target.page_id)
        if not page:
            raise _http_422("目标页不存在")
        return page
    scene = db.get(Scene, target.scene_id)
    if not scene:
        raise _http_422("目标场景不存在")
    return scene


def _validate_ownership(db: Session, envelope: CommandEnvelope) -> None:
    project_id = envelope.target.project_id
    page = _load_page(db, project_id, envelope.target.page_id)
    if envelope.target.panel_id:
        panel = db.get(Panel, envelope.target.panel_id)
        if not panel:
            raise _http_422("目标分镜格不存在")
        if page is None or panel.page_id != page.id:
            raise _http_422("分镜格不属于目标页")
    if envelope.target.dialogue_id:
        dialogue = db.get(Dialogue, envelope.target.dialogue_id)
        if not dialogue:
            raise _http_422("目标气泡不存在")
        if dialogue.panel_id != envelope.target.panel_id:
            raise _http_422("气泡不属于目标分镜格")
    if envelope.target.scene_id:
        scene = db.get(Scene, envelope.target.scene_id)
        if not scene:
            raise _http_422("目标场景不存在")
        if project_id_for_scene(db, scene) != project_id:
            raise _http_422("目标不属于当前项目")
        if page is not None and envelope.target.scene_id not in (page.scene_ids or []):
            raise _http_422("场景未被目标页引用")
    if envelope.target.asset_id:
        from app.models import Asset

        asset = db.get(Asset, envelope.target.asset_id)
        if not asset or asset.project_id != project_id:
            raise _http_422("目标素材不属于当前项目")


def _field_diff(before: dict, after: dict, extras: dict | None = None) -> dict:
    diff = {}
    keys = set(before) | set(after)
    for key in keys:
        if before.get(key) != after.get(key):
            diff[key] = {"before": before.get(key), "after": after.get(key)}
    if extras:
        diff.update(extras)
    return diff


def _preview_panel_diff(panel: Panel, payload: dict) -> dict:
    before = {key: _copy_json(getattr(panel, key)) for key in payload}
    return _field_diff(before, payload)


def _preview_dialogue_diff(db: Session, page: MangaPage, dialogue: Dialogue, payload: dict) -> dict:
    before = {key: _copy_json(getattr(dialogue, key)) for key in payload}
    extras = None
    if "target_text" in payload:
        panel_ids = list(db.scalars(select(Panel.id).where(Panel.page_id == page.id)))
        dialogues = list(db.scalars(select(Dialogue).where(Dialogue.panel_id.in_(panel_ids))))
        current_chars = sum(len("".join(item.target_text.split())) for item in dialogues)
        new_chars = current_chars - len("".join(dialogue.target_text.split()))
        new_chars += len("".join((payload["target_text"] or "").split()))
        extras = {
            "text_metrics": {
                "before": {"chars": current_chars, "bubbles": len(dialogues)},
                "after": {"chars": new_chars, "bubbles": len(dialogues)},
                "char_limit": 180,
                "bubble_limit": 8,
            }
        }
    return _field_diff(before, payload, extras)


class _PreviewAbort(Exception):
    """Abort a nested preview execute so the savepoint rolls back."""


def _preview_command(
    db: Session,
    envelope: CommandEnvelope,
    row: DirectorCommand | None = None,
) -> tuple[dict | None, dict | None]:
    _validate_ownership(db, envelope)
    entity = _resolve_version_entity(db, envelope)
    current = _current_version(entity, envelope.expected_version.scope)
    if current != envelope.expected_version.value:
        return None, {
            "code": "VERSION_CONFLICT",
            "message": "目标版本已过期，请刷新后重试",
            "scope": envelope.expected_version.scope,
            "current_version": current,
        }
    operation = envelope.operation
    payload = envelope.payload
    page = db.get(MangaPage, envelope.target.page_id) if envelope.target.page_id else None
    if operation in {"update_panel_shot", "update_panel_cast", "update_panel_layout"}:
        panel = db.get(Panel, envelope.target.panel_id)
        diff: dict | None = _preview_panel_diff(panel, payload)
    elif operation in {"update_dialogue", "move_dialogue"}:
        dialogue = db.get(Dialogue, envelope.target.dialogue_id)
        diff = _preview_dialogue_diff(db, page, dialogue, payload)
    elif operation == "update_scene_context":
        if "background" in payload and not envelope.target.panel_id:
            raise _http_422("update_scene_context.background 需要 target.panel_id")
        scene = db.get(Scene, envelope.target.scene_id)
        before = {
            key: _copy_json(getattr(scene, key))
            for key in payload
            if key != "background" and hasattr(scene, key)
        }
        after = {key: value for key, value in payload.items() if key != "background"}
        extras = None
        if "background" in payload:
            panel = db.get(Panel, envelope.target.panel_id)
            extras = {
                "background": {
                    "before": panel.background,
                    "after": payload["background"],
                }
            }
        diff = _field_diff(before, after, extras)
    elif operation == "update_page_layout":
        diff = {
            "panel_count": {
                "before": page.panel_count,
                "after": payload["panel_count"],
            },
            "layout_mode": {
                "before": (page.source_coverage or {}).get("layout_mode"),
                "after": payload["layout_mode"],
            },
        }
    elif operation == "regenerate_region":
        parent_id = page.selected_candidate_id if page else None
        diff = {
            "derived_candidate": {
                "before": None,
                "after": {
                    "parent_candidate_id": parent_id,
                    "instruction": payload.get("instruction"),
                    "model_alias": payload.get("model_alias"),
                    "resolution": payload.get("resolution"),
                    "mask_regions": len(
                        payload.get("mask") or payload.get("target_regions") or []
                    ),
                },
            }
        }
    else:
        raise _http_422("未知 operation")
    scratch = row or DirectorCommand(
        project_id=envelope.target.project_id,
        command_id=envelope.command_id,
        command_group_id=envelope.command_group_id,
        operation=envelope.operation,
        status=CommandStatus.PROPOSED.value,
        target=envelope.target.model_dump(),
        expected_version=envelope.expected_version.model_dump(),
        payload=envelope.payload,
        source=envelope.source.model_dump(),
    )
    try:
        with db.begin_nested():
            _execute_operation(db, scratch, envelope)
            raise _PreviewAbort()
    except HTTPException as exc:
        return diff, {
            "code": "VALIDATION",
            "message": exc.detail,
            "status": exc.status_code,
        }
    except _PreviewAbort:
        return diff, None


def _inverse_from_diff(diff: dict | None) -> dict:
    inverse = {}
    if not diff:
        return inverse
    for key, change in diff.items():
        if key in {"text_metrics", "derived_candidate"}:
            continue
        if isinstance(change, dict) and "before" in change:
            inverse[key] = change["before"]
    return inverse


def _page_snapshot(db: Session, page: MangaPage) -> dict:
    panels = list(db.scalars(select(Panel).where(Panel.page_id == page.id)))
    panel_ids = [panel.id for panel in panels]
    dialogues = (
        list(db.scalars(select(Dialogue).where(Dialogue.panel_id.in_(panel_ids))))
        if panel_ids
        else []
    )
    return {
        "panel_count": page.panel_count,
        "layout_mode": (page.source_coverage or {}).get("layout_mode"),
        "estimated_text_chars": page.estimated_text_chars,
        "estimated_bubbles": page.estimated_bubbles,
        "selected_candidate_ack_version": page.selected_candidate_ack_version,
        "geometry_save_command": _copy_json(page.geometry_save_command),
        "panels": [
            {
                "id": panel.id,
                "version": panel.version,
                "reading_order": panel.reading_order,
                "bounds": _copy_json(panel.bounds),
                "shot_type": panel.shot_type,
                "camera_angle": panel.camera_angle,
                "camera_height": panel.camera_height,
                "characters": _copy_json(panel.characters),
                "character_presence": _copy_json(panel.character_presence),
                "props": _copy_json(panel.props),
                "outfits": _copy_json(panel.outfits),
                "actions": _copy_json(panel.actions),
                "expressions": _copy_json(panel.expressions),
                "background": panel.background,
                "sound_effects": _copy_json(panel.sound_effects),
                "bleed": panel.bleed,
                "borderless": panel.borderless,
                "locked_fields": _copy_json(panel.locked_fields),
                "geometry": _copy_json(panel.geometry),
                "bubble_regions": _copy_json(panel.bubble_regions),
            }
            for panel in panels
        ],
        "dialogues": [
            {
                "id": item.id,
                "panel_id": item.panel_id,
                "speaker_character_id": item.speaker_character_id,
                "target_text": item.target_text,
                "reading_order": item.reading_order,
                "text_direction": item.text_direction,
                "region": _copy_json(item.region),
                "rewrite_forbidden": item.rewrite_forbidden,
                "bubble": _copy_json(item.bubble),
            }
            for item in dialogues
        ],
    }


def _restore_page_snapshot(db: Session, page: MangaPage, snapshot: dict) -> None:
    from sqlalchemy import delete

    panel_ids = list(db.scalars(select(Panel.id).where(Panel.page_id == page.id)))
    if panel_ids:
        db.execute(delete(Dialogue).where(Dialogue.panel_id.in_(panel_ids)))
        db.execute(delete(Panel).where(Panel.id.in_(panel_ids)))
    db.flush()
    page.panel_count = snapshot["panel_count"]
    page.source_coverage = {
        **(page.source_coverage or {}),
        "layout_mode": snapshot.get("layout_mode"),
    }
    restored_panels = {}
    for item in snapshot["panels"]:
        panel = Panel(
            id=item["id"],
            page_id=page.id,
            reading_order=item["reading_order"],
            bounds=item["bounds"] or {},
            shot_type=item["shot_type"],
            camera_angle=item["camera_angle"],
            camera_height=item["camera_height"],
            characters=item["characters"] or [],
            character_presence=item["character_presence"] or {},
            props=item["props"] or [],
            outfits=item["outfits"] or {},
            actions=item["actions"] or {},
            expressions=item["expressions"] or {},
            background=item["background"] or "",
            sound_effects=item["sound_effects"] or [],
            bleed=item["bleed"],
            borderless=item["borderless"],
            locked_fields=item["locked_fields"] or [],
            geometry=item.get("geometry"),
            bubble_regions=item.get("bubble_regions") or [],
        )
        panel.version = item.get("version") or 1
        db.add(panel)
        restored_panels[panel.id] = panel
    db.flush()
    for item in snapshot["dialogues"]:
        db.add(
            Dialogue(
                id=item["id"],
                panel_id=item["panel_id"],
                speaker_character_id=item["speaker_character_id"],
                target_text=item["target_text"],
                reading_order=item["reading_order"],
                text_direction=item["text_direction"],
                region=item["region"] or {},
                rewrite_forbidden=item["rewrite_forbidden"],
                bubble=item.get("bubble"),
            )
        )
    from app.services.editor import (
        mark_pages_for_review,
        mark_storyboard_changed,
        refresh_page_text_metrics,
    )

    if "geometry_save_command" in snapshot:
        page.geometry_save_command = snapshot.get("geometry_save_command")
    mark_storyboard_changed(page)
    mark_pages_for_review(db, page.chapter_id, from_page_number=page.page_number)
    refresh_page_text_metrics(db, page)
    db.flush()


def _execute_regenerate(db: Session, row: DirectorCommand, envelope: CommandEnvelope) -> None:
    """V02-42B: accept creates a derived candidate, never overwrites the parent.

    All failures here happen before any Job or paid call exists. The job is
    only enqueued after the accept transaction commits (see accept_command).
    """

    payload = envelope.payload
    mask = payload.get("mask") or payload.get("target_regions")
    if not mask:
        raise _http_422("局部重抽卡缺少 mask，已在调用前拒绝")
    page = db.get(MangaPage, envelope.target.page_id)
    frozen_parent = None
    if isinstance(row.diff, dict):
        frozen_parent = (
            ((row.diff.get("derived_candidate") or {}).get("after") or {}).get(
                "parent_candidate_id"
            )
        )
    selected_parent = page.selected_candidate_id if page else None
    if frozen_parent and selected_parent and frozen_parent != selected_parent:
        raise _http_409("采用候选已变化，请重新预览局部重绘")
    parent_id = frozen_parent or selected_parent
    if not parent_id:
        raise _http_422("局部重抽卡缺少父候选")
    parent = db.get(PageCandidate, parent_id)
    if not parent or parent.deleted_at is not None:
        raise _http_422("父候选不存在或已删除，已在调用前拒绝")
    if parent.page_id != envelope.target.page_id:
        raise _http_422("父候选不属于目标页")
    create_region_regeneration(db, row=row, envelope=envelope, page=page, parent=parent)


def _execute_operation(db: Session, row: DirectorCommand, envelope: CommandEnvelope) -> None:
    operation = envelope.operation
    payload = dict(envelope.payload)
    page = _load_page(db, envelope.target.project_id, envelope.target.page_id)
    if operation == "regenerate_region":
        _execute_regenerate(db, row, envelope)
        return
    if operation == "update_page_layout":
        row.before_snapshot = _page_snapshot(db, page)
        apply_page_layout(
            db,
            page,
            panel_count=payload["panel_count"],
            layout_mode=payload["layout_mode"],
        )
        return
    if operation in {"update_panel_shot", "update_panel_cast", "update_panel_layout"}:
        panel = db.get(Panel, envelope.target.panel_id)
        row.inverse_payload = snapshot_fields(panel, PANEL_RESTORE_FIELDS)
        apply_panel_fields(db, panel, page, envelope.target.project_id, payload)
        return
    if operation in {"update_dialogue", "move_dialogue"}:
        dialogue = db.get(Dialogue, envelope.target.dialogue_id)
        panel = db.get(Panel, envelope.target.panel_id)
        row.inverse_payload = snapshot_fields(dialogue, DIALOGUE_RESTORE_FIELDS)
        apply_dialogue_fields(
            db, dialogue, panel, page, envelope.target.project_id, payload
        )
        return
    if operation == "update_scene_context":
        if "background" in payload and not envelope.target.panel_id:
            raise _http_422("update_scene_context.background 需要 target.panel_id")
        scene = db.get(Scene, envelope.target.scene_id)
        inverse = snapshot_fields(scene, SCENE_RESTORE_FIELDS)
        if envelope.target.panel_id:
            panel = db.get(Panel, envelope.target.panel_id)
            inverse["background"] = panel.background
        row.inverse_payload = inverse
        scene_values = {k: v for k, v in payload.items() if k != "background"}
        apply_scene_fields(db, scene, scene_values, bump_storyboard=True)
        if "background" in payload:
            panel = db.get(Panel, envelope.target.panel_id)
            apply_panel_fields(
                db,
                panel,
                page,
                envelope.target.project_id,
                {"background": payload["background"]},
            )
        return
    raise _http_422("未知 operation")


def _parse_envelope(raw: dict, project_id: str, group_id: str) -> CommandEnvelope:
    if _payload_size(raw.get("payload") or {}) > PAYLOAD_MAX_BYTES:
        raise _http_422("payload 超过 16KB")
    try:
        envelope = CommandEnvelope.model_validate(raw)
    except ValidationError as error:
        raise _http_422(error.errors()[0].get("msg", "命令 envelope 无效")) from error
    if envelope.target.project_id != project_id:
        raise _http_422("target.project_id 与路径项目不一致")
    if envelope.command_group_id != group_id:
        raise _http_422("command_group_id 与命令组不一致")
    return envelope


def _existing_group(db: Session, project_id: str, command_group_id: str):
    return db.scalar(
        select(DirectorCommandGroup).where(
            DirectorCommandGroup.project_id == project_id,
            DirectorCommandGroup.command_group_id == command_group_id,
        )
    )


def _replay_group(db: Session, group: DirectorCommandGroup) -> dict:
    payload = (
        copy.deepcopy(group.first_result) if group.first_result else _group_read(db, group)
    )
    payload["idempotent_replay"] = True
    return payload


def propose_command_group(db: Session, project_id: str, body: dict) -> dict:
    _owned_project(db, project_id)
    group_id = body.get("command_group_id")
    commands = body.get("commands")
    if not isinstance(group_id, str):
        raise _http_422("command_group_id 必须是 uuid")
    try:
        UUID(group_id)
    except ValueError as error:
        raise _http_422("command_group_id 必须是 uuid") from error
    if not isinstance(commands, list) or not commands:
        raise _http_422("commands 不能为空")
    existing = _existing_group(db, project_id, group_id)
    if existing is not None:
        return _replay_group(db, existing)
    page_id = None
    parsed: list[CommandEnvelope] = []
    for raw in commands:
        if not isinstance(raw, dict):
            raise _http_422("command envelope 必须是对象")
        if not raw.get("command_id"):
            raise _http_422("command_id 缺失")
        envelope = _parse_envelope(raw, project_id, group_id)
        parsed.append(envelope)
        page_id = page_id or envelope.target.page_id
    for envelope in parsed:
        duplicate = db.scalar(
            select(DirectorCommand).where(
                DirectorCommand.project_id == project_id,
                DirectorCommand.command_id == envelope.command_id,
            )
        )
        if duplicate is not None:
            original = db.get(DirectorCommandGroup, duplicate.group_id)
            if original is None:
                raise _http_409("command_id 已存在")
            return _replay_group(db, original)
    group = DirectorCommandGroup(
        project_id=project_id,
        command_group_id=group_id,
        page_id=page_id,
        status=CommandGroupStatus.PROPOSED.value,
    )
    try:
        with db.begin_nested():
            db.add(group)
            db.flush()
    except IntegrityError as error:
        replay = _existing_group(db, project_id, group_id)
        if replay is None:
            raise _http_409("命令组写入冲突") from error
        return _replay_group(db, replay)
    for envelope in parsed:
        row = DirectorCommand(
            project_id=project_id,
            group_id=group.id,
            command_id=envelope.command_id,
            command_group_id=group_id,
            retry_of_command_id=envelope.retry_of_command_id,
            operation=envelope.operation,
            status=CommandStatus.PROPOSED.value,
            target=envelope.target.model_dump(),
            expected_version=envelope.expected_version.model_dump(),
            payload=envelope.payload,
            source=envelope.source.model_dump(),
            envelope_created_at=envelope.created_at,
        )
        try:
            diff, error = _preview_command(db, envelope, row)
            row.diff = diff
            row.error = error
            row.inverse_payload = _inverse_from_diff(diff)
            row.status = (
                CommandStatus.PREVIEWED.value
                if error is None
                else CommandStatus.REJECTED.value
            )
        except HTTPException as exc:
            row.status = CommandStatus.REJECTED.value
            row.error = {"code": "VALIDATION", "message": exc.detail, "status": exc.status_code}
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError as error:
            original_row = db.scalar(
                select(DirectorCommand).where(
                    DirectorCommand.project_id == project_id,
                    DirectorCommand.command_id == envelope.command_id,
                )
            )
            if original_row is None:
                raise _http_409("command_id 已存在") from error
            original = db.get(DirectorCommandGroup, original_row.group_id)
            return _replay_group(db, original)
    _refresh_group_status(db, group)
    result = _group_read(db, group)
    group.first_result = result
    db.commit()
    db.refresh(group)
    return result


def get_command_group(db: Session, project_id: str, command_group_id: str) -> dict:
    _owned_project(db, project_id)
    group = _existing_group(db, project_id, command_group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="命令组不存在")
    return _group_read(db, group)


def list_command_groups(db: Session, project_id: str, page_id: str | None) -> list[dict]:
    _owned_project(db, project_id)
    query = select(DirectorCommandGroup).where(DirectorCommandGroup.project_id == project_id)
    if page_id:
        query = query.where(DirectorCommandGroup.page_id == page_id)
    query = query.order_by(DirectorCommandGroup.created_at.desc())
    return [_group_read(db, group) for group in db.scalars(query)]


def _load_command(db: Session, project_id: str, command_id: str) -> DirectorCommand:
    row = db.scalar(
        select(DirectorCommand).where(
            DirectorCommand.project_id == project_id,
            DirectorCommand.command_id == command_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="命令不存在")
    return row


def _envelope_from_row(row: DirectorCommand) -> CommandEnvelope:
    return CommandEnvelope.model_validate(
        {
            "schema_version": 1,
            "command_id": row.command_id,
            "command_group_id": row.command_group_id,
            "created_at": row.envelope_created_at or "1970-01-01T00:00:00Z",
            "target": row.target,
            "expected_version": row.expected_version,
            "retry_of_command_id": row.retry_of_command_id,
            "operation": row.operation,
            "payload": row.payload,
            "source": row.source,
        }
    )


def accept_command(db: Session, project_id: str, command_id: str) -> dict:
    _owned_project(db, project_id)
    row = _load_command(db, project_id, command_id)
    group = db.get(DirectorCommandGroup, row.group_id)
    if row.status == CommandStatus.EXECUTED.value:
        result = _group_read(db, group)
        result["idempotent_replay"] = True
        _enqueue_region_job(db, row)
        return result
    if row.status == CommandStatus.FAILED.value:
        raise HTTPException(
            status_code=int((row.error or {}).get("status") or 409),
            detail=(row.error or {}).get("message") or "命令已失败，请使用新的 command_id 重试",
        )
    if row.status != CommandStatus.PREVIEWED.value:
        raise _http_409(f"命令状态 {row.status} 不能接受")
    envelope = _envelope_from_row(row)
    if envelope.target.page_id:
        lock_entity(db, MangaPage, envelope.target.page_id)
    if envelope.target.panel_id:
        lock_entity(db, Panel, envelope.target.panel_id)
    entity = _resolve_version_entity(db, envelope)
    current = _current_version(entity, envelope.expected_version.scope)
    if current != envelope.expected_version.value:
        row.error = {
            "code": "VERSION_CONFLICT",
            "message": "目标版本已过期，请刷新后重试",
            "scope": envelope.expected_version.scope,
            "current_version": current,
        }
        db.commit()
        raise _http_409(row.error)
    if envelope.operation == "update_scene_context" and "background" in envelope.payload:
        # §6.3: execution writes scene fields AND panel.background, but the
        # scene.version gate above cannot see a concurrent panel background
        # PATCH (it moves panel.version and the storyboard counter, never
        # scene.version — manual scene edits do not bump the storyboard
        # either). Re-check the panel half against the propose-time diff. The
        # panel is already locked above: background payloads require
        # target.panel_id, so accept's page→panel lock block covers this read.
        panel = (
            db.get(Panel, envelope.target.panel_id) if envelope.target.panel_id else None
        )
        background_before = ((row.diff or {}).get("background") or {}).get("before")
        if (
            panel is None
            or background_before is None
            or panel.background != background_before
        ):
            conflict = {
                "code": "VERSION_CONFLICT",
                "message": "分镜背景已在预览后被更新，请刷新后重试",
                "scope": "panel",
                "current_version": None if panel is None else panel.version,
            }
            row.error = conflict
            db.commit()
            raise _http_409(conflict)
    claimed = db.execute(
        update(DirectorCommand)
        .where(
            DirectorCommand.id == row.id,
            DirectorCommand.status == CommandStatus.PREVIEWED.value,
        )
        .values(status=CommandStatus.ACCEPTED.value)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.refresh(row)
        if row.status == CommandStatus.EXECUTED.value:
            result = _group_read(db, group)
            result["idempotent_replay"] = True
            _enqueue_region_job(db, row)
            return result
        raise _http_409(f"命令状态 {row.status} 不能接受")
    db.refresh(row)
    try:
        with db.begin_nested():
            _execute_operation(db, row, envelope)
    except HTTPException as exc:
        row.error = {
            "code": "EXECUTION",
            "message": exc.detail,
            "status": exc.status_code,
        }
        row.status = CommandStatus.FAILED.value
        _refresh_group_status(db, group)
        db.commit()
        raise
    page = _load_page(db, project_id, envelope.target.page_id)
    row.status = CommandStatus.EXECUTED.value
    row.storyboard_version_after = page.storyboard_version if page else None
    row.error = None
    _refresh_group_status(db, group)
    db.commit()
    _enqueue_region_job(db, row)
    return _group_read(db, group)


def _enqueue_region_job(db: Session, row: DirectorCommand) -> None:
    """Enqueue the derived-candidate job only after the accept commit (§6-5).

    Preview execution must never reach the queue, so enqueue is deliberately
    not part of the operation itself.
    """

    if row.operation != "regenerate_region":
        return
    lineage = db.scalar(
        select(CandidateLineage).where(
            CandidateLineage.source_command_id == row.command_id
        )
    )
    if lineage is None:
        return
    child = db.get(PageCandidate, lineage.child_candidate_id)
    if child is None or not child.job_id:
        return
    job = db.get(GenerationJob, child.job_id)
    if job is None:
        return
    enqueue_job(db, job)


def reject_command(db: Session, project_id: str, command_id: str) -> dict:
    _owned_project(db, project_id)
    row = _load_command(db, project_id, command_id)
    group = db.get(DirectorCommandGroup, row.group_id)
    if row.status in TERMINAL_COMMAND:
        result = _group_read(db, group)
        result["idempotent_replay"] = True
        return result
    if row.status not in {CommandStatus.PREVIEWED.value, CommandStatus.PROPOSED.value}:
        raise _http_409(f"命令状态 {row.status} 不能拒绝")
    claimed = db.execute(
        update(DirectorCommand)
        .where(
            DirectorCommand.id == row.id,
            DirectorCommand.status.in_(
                [CommandStatus.PREVIEWED.value, CommandStatus.PROPOSED.value]
            ),
        )
        .values(status=CommandStatus.REJECTED.value)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.refresh(row)
        result = _group_read(db, group)
        result["idempotent_replay"] = True
        return result
    db.refresh(row)
    _refresh_group_status(db, group)
    db.commit()
    return _group_read(db, group)


def discard_group(db: Session, project_id: str, command_group_id: str) -> dict:
    _owned_project(db, project_id)
    group = _existing_group(db, project_id, command_group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="命令组不存在")
    rows = list(db.scalars(select(DirectorCommand).where(DirectorCommand.group_id == group.id)))
    for row in rows:
        # Conditional claim mirrors accept_command/reject_command: only rows
        # still PREVIEWED/PROPOSED may flip to DISCARDED. A rowcount of 0
        # means the row was concurrently accepted/executed or rejected
        # between our read and this claim (accept_command defends the same
        # race from its side). Leave its real status untouched so
        # undo_command can still revert EXECUTED rows; refresh so the journal
        # read below reports current state. The group itself still ends up
        # DISCARDED (existing forced-discard behavior), but executed rows stay
        # undoable.
        db.execute(
            update(DirectorCommand)
            .where(
                DirectorCommand.id == row.id,
                DirectorCommand.status.in_(
                    [CommandStatus.PREVIEWED.value, CommandStatus.PROPOSED.value]
                ),
            )
            .values(status=CommandStatus.DISCARDED.value)
            .execution_options(synchronize_session=False)
        )
        db.refresh(row)
    group.status = CommandGroupStatus.DISCARDED.value
    _refresh_group_status(db, group)
    group.status = CommandGroupStatus.DISCARDED.value
    db.commit()
    return _group_read(db, group)


def undo_command(db: Session, project_id: str, command_id: str) -> dict:
    _owned_project(db, project_id)
    row = _load_command(db, project_id, command_id)
    if row.status != CommandStatus.EXECUTED.value:
        raise _http_409("只能撤销已执行的命令")
    page = _load_page(db, project_id, (row.target or {}).get("page_id"))
    if page is None:
        row.status = CommandStatus.SUPERSEDED.value
        group = db.get(DirectorCommandGroup, row.group_id)
        _refresh_group_status(db, group)
        db.commit()
        raise _http_409(
            {
                "code": "SUPERSEDED",
                "message": "分镜已在撤销前被更新，请刷新",
                "current_version": None,
            }
        )
    # Same lock set and order as accept_command: page first, then the row's
    # target panel for panel/dialogue-scoped operations (update_scene_context
    # with a background carries target.panel_id too). populate_existing also
    # replaces any stale identity-map snapshot with the locked current state,
    # so the storyboard version below is the one the lock protects.
    lock_entity(db, MangaPage, page.id)
    if (row.target or {}).get("panel_id"):
        lock_entity(db, Panel, row.target["panel_id"])
    current_storyboard_version = page.storyboard_version
    # Conditional claim mirrors accept_command/reject_command/discard_group:
    # the row must still be EXECUTED and the storyboard must still be at the
    # version the executed command recorded. Winning the claim flips the row
    # to SUPERSEDED inside the same transaction that inserts the undo row and
    # restores the snapshot, so a concurrent undo/redo (redo delegates here)
    # can no longer pass the bare check-then-write gates and double-apply the
    # inverse against an already-reverted page. On loss the whole unit rolls
    # back below, leaving the row EXECUTED and the page untouched.
    claimed = db.execute(
        update(DirectorCommand)
        .where(
            DirectorCommand.id == row.id,
            DirectorCommand.status == CommandStatus.EXECUTED.value,
            DirectorCommand.storyboard_version_after == current_storyboard_version,
        )
        .values(status=CommandStatus.SUPERSEDED.value)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        db.refresh(row)
        if row.status == CommandStatus.EXECUTED.value:
            # Still EXECUTED after losing the claim means the storyboard moved
            # on after our read: keep the designed destructive SUPERSEDED flip
            # (the sbv is the reconciliation anchor; SUPERSEDED tells the user
            # to refresh).
            group = db.get(DirectorCommandGroup, row.group_id)
            row.status = CommandStatus.SUPERSEDED.value
            _refresh_group_status(db, group)
            db.commit()
        raise _http_409(
            {
                "code": "SUPERSEDED",
                "message": "分镜已在撤销前被更新，请刷新",
                "current_version": current_storyboard_version,
            }
        )
    db.refresh(row)
    existing = _existing_group(db, project_id, row.command_group_id)
    undo_id = str(uuid4())
    redo_snapshot = None
    redo_inverse = None
    if row.operation == "update_page_layout":
        redo_snapshot = _page_snapshot(db, page)
    elif row.operation in {"update_panel_shot", "update_panel_cast", "update_panel_layout"}:
        redo_inverse = snapshot_fields(
            db.get(Panel, row.target["panel_id"]), PANEL_RESTORE_FIELDS
        )
    elif row.operation in {"update_dialogue", "move_dialogue"}:
        redo_inverse = snapshot_fields(
            db.get(Dialogue, row.target["dialogue_id"]), DIALOGUE_RESTORE_FIELDS
        )
    elif row.operation == "update_scene_context":
        redo_inverse = snapshot_fields(
            db.get(Scene, row.target["scene_id"]), SCENE_RESTORE_FIELDS
        )
        if row.target.get("panel_id"):
            redo_inverse["background"] = db.get(Panel, row.target["panel_id"]).background
    undo_row = DirectorCommand(
        project_id=project_id,
        group_id=existing.id,
        command_id=undo_id,
        command_group_id=row.command_group_id,
        inverse_of_command_id=row.command_id,
        operation=row.operation,
        status=CommandStatus.PREVIEWED.value,
        target=row.target,
        expected_version={"scope": "storyboard", "value": page.storyboard_version},
        payload=row.inverse_payload or {},
        source=row.source,
        inverse_payload=redo_inverse,
        before_snapshot=redo_snapshot,
        envelope_created_at=row.envelope_created_at,
    )
    db.add(undo_row)
    db.flush()
    if row.operation == "update_page_layout":
        if not row.before_snapshot or "panels" not in row.before_snapshot:
            raise _http_409("缺少布局快照，无法撤销")
        _restore_page_snapshot(db, page, row.before_snapshot)
    elif row.operation in {"update_panel_shot", "update_panel_cast", "update_panel_layout"}:
        panel = db.get(Panel, row.target["panel_id"])
        restore_panel_snapshot(db, panel, page, row.inverse_payload or {})
    elif row.operation in {"update_dialogue", "move_dialogue"}:
        dialogue = db.get(Dialogue, row.target["dialogue_id"])
        panel = db.get(Panel, row.target["panel_id"])
        restore_dialogue_snapshot(db, dialogue, panel, page, row.inverse_payload or {})
    elif row.operation == "update_scene_context":
        scene = db.get(Scene, row.target["scene_id"])
        restore_scene_snapshot(
            db, scene, row.inverse_payload or {}, bump_storyboard=True
        )
        if "background" in (row.inverse_payload or {}) and row.target.get("panel_id"):
            panel = db.get(Panel, row.target["panel_id"])
            restore_panel_snapshot(
                db,
                panel,
                page,
                {"background": row.inverse_payload["background"]},
            )
    else:
        raise _http_422("该命令不支持撤销")
    undo_row.status = CommandStatus.EXECUTED.value
    undo_row.storyboard_version_after = page.storyboard_version
    _refresh_group_status(db, existing)
    db.commit()
    return _group_read(db, existing)


def redo_command(db: Session, project_id: str, command_id: str) -> dict:
    """Redo is undo of an undo command."""
    row = _load_command(db, project_id, command_id)
    if not row.inverse_of_command_id:
        raise _http_409("只能重做撤销命令")
    return undo_command(db, project_id, command_id)
