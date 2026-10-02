"""DIRECTOR_PARSE worker handler (DIR-01A/01B).

Compiles one Chinese utterance into previewed DirectorCommand rows on an
existing PARSING group. Contract: docs/dir-01a-director-nl-contract.md.

The handler always lands the group in a terminal parse state
(PREVIEWED / NEEDS_CLARIFICATION / STALE / PARSE_FAILED) and returns
normally — parse outcomes are business results, not job failures, and no
model error may auto-retry into a second paid call (§7).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.director_commands import (
    USER_PROMPT_MAX_CHARS,
    CommandEnvelope,
    CommandGroupStatus,
)
from app.domain.states import JobStatus
from app.model_adapters.base import ProviderAdapterError, StructuredRequest
from app.models import (
    Chapter,
    Character,
    Dialogue,
    DirectorCommandGroup,
    GenerationJob,
    MangaPage,
    ModelCallAttempt,
    Outfit,
    Panel,
    Scene,
)
from app.services.ai_schemas import DirectorParseOutput
from app.services.director_commands import attach_parse_commands
from app.services.worker_handlers import execution, provider
from app.services.worker_handlers.provider import _invoke_provider

LOGGER = logging.getLogger(__name__)

# NL v1 whitelist (contract §5): regenerate_region is intentionally absent —
# NL never mints paid derived-generation jobs.
NL_OPERATIONS = frozenset(
    {
        "update_page_layout",
        "update_panel_layout",
        "update_panel_shot",
        "update_panel_cast",
        "update_scene_context",
        "update_dialogue",
        "move_dialogue",
    }
)

COMMAND_LIMIT = 8
CONTEXT_MAX_CHARS = 32 * 1024
DIALOGUE_TEXT_PREVIEW = 200
CLARIFY_OPTIONS_MAX = 12


def _now_rfc3339() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fail(group: DirectorCommandGroup, code: str, message: str, **extra) -> None:
    first_result = {
        "kind": "error",
        "error": {"code": code, "message": message},
    }
    first_result.update(extra)
    group.status = CommandGroupStatus.PARSE_FAILED.value
    group.first_result = first_result


def _build_context(db: Session, project_id: str, page: MangaPage) -> dict:
    panels = list(
        db.scalars(
            select(Panel).where(Panel.page_id == page.id).order_by(Panel.reading_order)
        )
    )
    dialogues = list(
        db.scalars(
            select(Dialogue)
            .join(Panel, Dialogue.panel_id == Panel.id)
            .where(Panel.page_id == page.id)
            .order_by(Panel.reading_order, Dialogue.reading_order)
        )
    )
    speaker_names = {
        row.speaker_character_id
        for row in dialogues
        if row.speaker_character_id
    }
    characters = list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project_id)
            .order_by(Character.created_at)
        )
    )[:40]
    outfits = list(
        db.scalars(
            select(Outfit)
            .where(Outfit.project_id == project_id)
            .order_by(Outfit.created_at)
        )
    )
    outfits_by_character: dict[str, list[dict]] = {}
    for outfit in outfits:
        outfits_by_character.setdefault(outfit.character_id, []).append(
            {"outfit_id": outfit.id, "name": outfit.name}
        )
    speaker_lookup = {
        cid: next(
            (
                c.primary_name
                for c in characters
                if c.id == cid
            ),
            None,
        )
        for cid in speaker_names
    }
    scene_ids = list(page.scene_ids or [])
    scenes = (
        list(
            db.scalars(select(Scene).where(Scene.id.in_(scene_ids)).order_by(Scene.ordinal))
        )
        if scene_ids
        else []
    )
    return {
        "page": {
            "page_id": page.id,
            "page_number": page.page_number,
            "panel_count": page.panel_count,
            "storyboard_version": page.storyboard_version,
        },
        "panels": [
            {
                "panel_id": panel.id,
                "reading_order": panel.reading_order,
                "shot_type": panel.shot_type,
                "camera_angle": panel.camera_angle,
                "camera_height": panel.camera_height,
                "background": panel.background,
                "characters": [
                    speaker_lookup.get(cid) or cid
                    for cid in (panel.characters or [])
                ],
                "dialogues": [
                    {
                        "dialogue_id": d.id,
                        "reading_order": d.reading_order,
                        "speaker": speaker_lookup.get(d.speaker_character_id),
                        "target_text": (d.target_text or "")[:DIALOGUE_TEXT_PREVIEW],
                    }
                    for d in dialogues
                    if d.panel_id == panel.id
                ],
            }
            for panel in panels
        ],
        "scenes": [
            {
                "scene_id": scene.id,
                "ordinal": scene.ordinal,
                "location": scene.location,
                "time_label": scene.time_label,
                "weather": scene.weather,
            }
            for scene in scenes
        ],
        "characters": [
            {
                "character_id": character.id,
                "primary_name": character.primary_name,
                "aliases": list(character.aliases or [])[:8],
                "outfits": outfits_by_character.get(character.id, [])[:12],
            }
            for character in characters
        ],
    }


def _build_prompt(utterance: str, context: dict, selection: dict | None) -> str:
    context_json = json.dumps(context, ensure_ascii=False)
    if len(context_json) > CONTEXT_MAX_CHARS:
        context_json = context_json[:CONTEXT_MAX_CHARS]
    selection_json = json.dumps(selection, ensure_ascii=False) if selection else "null"
    ops = (
        "- update_page_layout: 整页布局\n"
        '  payload={"panel_count": int(3-8), "layout_mode": "dynamic|balanced"}\n'
        '- update_panel_layout: 格布局\n'
        '  payload={"bounds"?, "reading_order"?, "bleed"?, "borderless"?}\n'
        '- update_panel_shot: 镜头\n'
        '  payload={"shot_type"?, "camera_angle"?, "camera_height"?,'
        ' "background"?, "sound_effects"?}\n'
        "- update_panel_cast: 角色\n"
        '  payload={"characters"?[角色名],'
        ' "character_presence"?{角色名:"VISIBLE|OFFSCREEN|MENTIONED"},'
        ' "outfits"?{角色名:"服装名"}, "expressions"?{角色名:"表情"},'
        ' "actions"?{}}\n'
        "- update_scene_context: 场景\n"
        '  payload={"location"?, "time_label"?, "weather"?, "background"?}\n'
        "- update_dialogue: 气泡\n"
        '  payload={"target_text"?, "text_direction"?, "region"?,'
        ' "speaker"?, "rewrite_forbidden"?}\n'
        '- move_dialogue: 气泡顺序\n'
        '  payload={"reading_order"?, "region"?}'
    )
    return f"""把用户的中文导演指令编译成结构化编辑命令 JSON。只输出 JSON，不要解释。

可用 operation（白名单，其余一律放入 unsupported）：
{ops}

target_hint 写法（只写提示，不要编造 id）：
- 格：{{"panel": 阅读顺序号}}
- 气泡：{{"panel": 格序, "dialogue": 气泡序}}
- 场景：{{"scene": 场景序(按 scenes 顺序从1起) 或 地点关键词}}

规则：
- 无法唯一确定目标时，不要猜，把歧义写入 clarifications（options 可为空数组）
- 指令要求重画/生成图片/局部重绘 → unsupported
- payload 里的角色一律写名字（与 characters 目录中的 primary_name/aliases 对齐），不要写 id
- 每条命令给 op_ref（c1、c2…）
- 当前选中范围（可为 null）：{selection_json}

输出 schema：{{"commands":[{{"op_ref","operation","target_hint","payload"}}],
"clarifications":[{{"kind","question","options"}}],"unsupported":[{{"reason","excerpt"}}]}}

页面上下文：{context_json}

用户指令：{utterance}"""


def _normalize_name(value: str) -> str:
    return "".join(value.split()).lower()


def _resolve_character(
    characters: list[Character], token: str
) -> tuple[Character | None, list[Character]]:
    """Resolve a uuid or character name/alias to a unique catalog row."""
    if not token:
        return None, []
    for character in characters:
        if character.id == token:
            return character, []
    needle = _normalize_name(str(token))
    matches = [
        character
        for character in characters
        if _normalize_name(character.primary_name) == needle
        or any(_normalize_name(alias) == needle for alias in (character.aliases or []))
    ]
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def _clarify_option(kind: str, option_id: str | None, label: str) -> dict:
    return {"kind": kind, "id": option_id, "label": label}


def _resolve_targets(
    command: dict,
    page: MangaPage,
    panels: list[Panel],
    dialogues: list[Dialogue],
    scenes: list[Scene],
    selection: dict | None,
    project_id: str,
) -> tuple[dict | None, list[dict], str | None]:
    """target_hint → envelope target dict.

    Returns (target, clarify_options, error). ``error`` set means the target
    could not be anchored at all; ``clarify_options`` lists candidates when
    the hint was ambiguous or missing.
    """
    hint = command.target_hint if isinstance(command.target_hint, dict) else {}
    operation = command.operation
    target = {"project_id": project_id, "page_id": page.id}

    def panel_options() -> list[dict]:
        return [
            _clarify_option("panel", p.id, f"格 {p.reading_order}")
            for p in panels[:CLARIFY_OPTIONS_MAX]
        ]

    panel: Panel | None = None
    if operation in {
        "update_panel_layout",
        "update_panel_shot",
        "update_panel_cast",
        "update_dialogue",
        "move_dialogue",
    }:
        raw = hint.get("panel")
        selected = None
        if raw is None and selection and selection.get("kind") == "panel":
            selected = selection.get("panel_id")
            raw = selected
        if raw is None:
            # No anchor at all → clarification, never a rejected row. An
            # empty page has no options and no resolution either — that is
            # the one case that lands as a rejected row.
            options = panel_options()
            return None, options, None if options else "目标格不存在"
        resolved = False
        if isinstance(raw, str) and raw in {p.id for p in panels}:
            panel = next(p for p in panels if p.id == raw)
            resolved = True
        elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
            matches = [p for p in panels if p.reading_order == int(raw)]
            panel = matches[0] if matches else None
            resolved = len(matches) == 1
        if not resolved:
            if selected is None:
                # An explicit but invalid anchor is a failed command, not a
                # clarification (contract A3).
                return None, [], f"格锚点「{raw}」不在目标页内"
            return None, panel_options(), None
        target["panel_id"] = panel.id

    if operation in {"update_dialogue", "move_dialogue"}:
        panel_dialogues = [d for d in dialogues if d.panel_id == panel.id]
        dialogue: Dialogue | None = None
        raw = hint.get("dialogue")
        anchored_by_selection = False
        if raw is None and selection and selection.get("kind") == "dialogue":
            raw = selection.get("dialogue_id")
            anchored_by_selection = True
        resolved = False
        if isinstance(raw, str) and raw in {d.id for d in panel_dialogues}:
            dialogue = next(d for d in panel_dialogues if d.id == raw)
            resolved = True
        elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
            matches = [d for d in panel_dialogues if d.reading_order == int(raw)]
            if matches:
                dialogue = matches[0]
                resolved = True
        elif raw is None and len(panel_dialogues) == 1:
            # A single dialogue in the anchored panel is unambiguous.
            dialogue = panel_dialogues[0]
            resolved = True
        if not resolved:
            options = [
                _clarify_option(
                    "dialogue",
                    d.id,
                    f"格 {panel.reading_order} · 气泡 {d.reading_order}："
                    f"{(d.target_text or '')[:20]}",
                )
                for d in panel_dialogues[:CLARIFY_OPTIONS_MAX]
            ]
            if raw is not None and not anchored_by_selection:
                return None, [], f"气泡锚点「{raw}」不在目标格内"
            if options:
                return None, options, None
            return None, [], "目标气泡不存在"
        target["dialogue_id"] = dialogue.id

    if operation == "update_scene_context":
        scene: Scene | None = None
        raw = hint.get("scene")
        if isinstance(raw, str) and raw in {s.id for s in scenes}:
            scene = next(s for s in scenes if s.id == raw)
        elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
            index = int(raw) - 1
            if 0 <= index < len(scenes):
                scene = scenes[index]
        elif isinstance(raw, str) and raw:
            matches = [s for s in scenes if raw in (s.location or "")]
            if len(matches) == 1:
                scene = matches[0]
            elif len(matches) > 1:
                return None, [
                    _clarify_option("scene", s.id, f"第 {s.ordinal} 场 · {s.location}")
                    for s in matches[:CLARIFY_OPTIONS_MAX]
                ], None
        if scene is None:
            options = [
                _clarify_option("scene", s.id, f"第 {s.ordinal} 场 · {s.location}")
                for s in scenes[:CLARIFY_OPTIONS_MAX]
            ]
            if options:
                return None, options, None
            return None, [], "目标场景不存在"
        target["scene_id"] = scene.id
        # update_scene_context.background additionally needs a panel anchor.
        if "background" in (command.payload or {}) and panel is None:
            raw = hint.get("panel")
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                matches = [p for p in panels if p.reading_order == int(raw)]
                if len(matches) == 1:
                    panel = matches[0]
            if panel is None:
                return None, panel_options(), "background 修改需要指定格"

        if panel is not None:
            target["panel_id"] = panel.id

    return target, [], None


def _version_scope(
    operation: str,
    page: MangaPage,
    target: dict,
    panels: list[Panel],
    scenes: list[Scene],
) -> dict:
    if operation == "update_page_layout":
        return {"scope": "page", "value": page.version}
    if operation == "update_scene_context":
        scene = next((s for s in scenes if s.id == target.get("scene_id")), None)
        return {"scope": "scene", "value": scene.version if scene else 1}
    panel = next((p for p in panels if p.id == target.get("panel_id")), None)
    return {"scope": "panel", "value": panel.version if panel else page.version}


def _resolve_payload(
    command: dict,
    characters: list[Character],
    outfits: list[Outfit],
) -> tuple[dict | None, str | None]:
    """Resolve name references in the payload to ids. Returns (payload, error)."""
    payload = dict(command.payload or {})
    operation = command.operation

    def resolve_char(token) -> tuple[str | None, str | None]:
        character, _ = _resolve_character(characters, str(token))
        if character is None:
            return None, f"无法唯一识别角色「{token}」"
        return character.id, None

    if operation == "update_panel_cast":
        if "characters" in payload and payload["characters"] is not None:
            resolved = []
            for token in payload["characters"]:
                cid, error = resolve_char(token)
                if error:
                    return None, error
                resolved.append(cid)
            payload["characters"] = resolved
        for key in ("character_presence", "outfits", "expressions"):
            mapping = payload.get(key)
            if not isinstance(mapping, dict):
                continue
            resolved_map = {}
            for token, value in mapping.items():
                cid, error = resolve_char(token)
                if error:
                    return None, error
                resolved_map[cid] = value
            payload[key] = resolved_map
        outfit_map = payload.get("outfits")
        if isinstance(outfit_map, dict):
            for cid, token in outfit_map.items():
                if not isinstance(token, str):
                    continue
                by_id = any(o.id == token for o in outfits)
                if by_id:
                    continue
                candidates = [o for o in outfits if o.character_id == cid]
                match = [o for o in candidates if _normalize_name(o.name) == _normalize_name(token)]
                if len(match) == 1:
                    outfit_map[cid] = match[0].id
                else:
                    return None, f"无法唯一识别服装「{token}」"
    if operation == "update_dialogue":
        speaker = payload.pop("speaker", None)
        if speaker is None:
            speaker = payload.get("speaker_character_id")
        if isinstance(speaker, str) and speaker:
            cid, error = resolve_char(speaker)
            if error:
                return None, error
            payload["speaker_character_id"] = cid
    return payload, None


def _run_director_parse(db: Session, job: GenerationJob) -> None:
    params = job.request_parameters or {}
    group = db.scalar(
        select(DirectorCommandGroup).where(
            DirectorCommandGroup.project_id == job.project_id,
            DirectorCommandGroup.command_group_id == params.get("command_group_id"),
        )
    )
    if group is None:
        raise ProviderAdapterError(
            "PARSE_GROUP_MISSING", "解析会话不存在", retryable=False
        )
    if group.status != CommandGroupStatus.PARSING.value:
        # Idempotent resume: a repeated attempt must never double-attach rows.
        return

    page = db.get(MangaPage, params.get("page_id") or "")
    chapter = db.get(Chapter, page.chapter_id) if page else None
    if page is None or chapter is None or chapter.deleted_at is not None:
        _fail(group, "PAGE_GONE", "目标页面或章节不存在")
        return
    if page.storyboard_version != params.get("storyboard_version"):
        group.status = CommandGroupStatus.STALE.value
        group.first_result = {
            "kind": "stale",
            "reason": "分镜已变更，请刷新后重发指令",
            "current_version": page.storyboard_version,
        }
        return

    project_id = job.project_id
    panels = list(
        db.scalars(select(Panel).where(Panel.page_id == page.id).order_by(Panel.reading_order))
    )
    dialogues = list(
        db.scalars(
            select(Dialogue)
            .join(Panel, Dialogue.panel_id == Panel.id)
            .where(Panel.page_id == page.id)
        )
    )
    scene_ids = list(page.scene_ids or [])
    scenes = (
        list(
            db.scalars(select(Scene).where(Scene.id.in_(scene_ids)).order_by(Scene.ordinal))
        )
        if scene_ids
        else []
    )
    characters = list(
        db.scalars(
            select(Character).where(Character.project_id == project_id)
        )
    )
    outfits = list(
        db.scalars(select(Outfit).where(Outfit.project_id == project_id))
    )
    selection = params.get("selection")
    utterance = str(params.get("utterance") or "")

    try:
        binding = provider._binding(
            db,
            operation="structured_text",
            project_id=project_id,
            explicit_reference=provider._text_model_reference(
                job, _project(db, project_id)
            ),
            task_kind=job.job_type,
        )
    except ProviderAdapterError as error:
        _fail(group, error.code, error.user_message, retryable=error.retryable)
        return
    job.catalog_model_id = binding.resolved.model.id

    context = _build_context(db, project_id, page)
    prompt = _build_prompt(utterance, context, selection)
    # Release the worker's JOB lock before the independent audit insert takes
    # a foreign-key lock on that row. Only model selection/progress is pending;
    # command output remains in the final lease-fenced worker transaction.
    execution._commit_owned_progress(db, job, status=JobStatus.GENERATING, progress=45)
    started = time.monotonic()
    output: DirectorParseOutput
    try:
        output = _invoke_provider(
            db,
            binding,
            lambda adapter: adapter.generate_structured(
                StructuredRequest(
                    prompt=prompt,
                    system_instruction=(
                        "你是漫画分镜导演命令编译器。只编译用户明确提出的编辑，"
                        "不推测、不补充、不编造实体 id。"
                    ),
                    temperature=0.1,
                    metadata={"max_output_tokens": 4096, "thinking_budget": 0},
                ),
                DirectorParseOutput,
            ),
        )
    except ProviderAdapterError as error:
        _fail(
            group,
            error.code,
            error.user_message,
            retryable=error.retryable,
            model=_model_meta(binding),
        )
        return
    except Exception:
        LOGGER.exception("director parse: unexpected model failure for group %s", group.id)
        _fail(group, "PARSE_ERROR", "指令解析失败，请重试")
        return
    duration_ms = int((time.monotonic() - started) * 1000)

    attempt_id = db.scalar(
        select(ModelCallAttempt.id)
        .where(ModelCallAttempt.job_id == job.id)
        .order_by(ModelCallAttempt.created_at.desc())
        .limit(1)
    )
    source = {
        "user_prompt": utterance[:USER_PROMPT_MAX_CHARS],
        "reference_asset_ids": [],
        "model": _model_meta(binding),
        "raw_output_id": attempt_id,
    }

    commands = list(output.commands)
    truncated = len(commands) > COMMAND_LIMIT
    commands = commands[:COMMAND_LIMIT]

    parsed: list[CommandEnvelope] = []
    rejected: list[dict] = []
    clarify_options: list[dict] = []
    for item in commands:
        if item.operation == "regenerate_region":
            clarify_options.append(
                _clarify_option(
                    "value",
                    None,
                    "暂不支持用指令局部重绘，请走「检查 → 修复」流程",
                )
            )
            continue
        if item.operation not in NL_OPERATIONS:
            rejected.append(
                _rejected_row(group, item, source, "SCHEMA", "operation 不在白名单内")
            )
            continue
        target, options, target_error = _resolve_targets(
            item, page, panels, dialogues, scenes, selection, project_id
        )
        if target is None:
            if options:
                # Unanchored/ambiguous targets become clarification options —
                # they are not executable commands and must not land as rows
                # (contract: 歧义不猜，澄清优先于失败行).
                clarify_options.extend(options)
            else:
                rejected.append(
                    _rejected_row(
                        group,
                        item,
                        source,
                        "TARGET_UNRESOLVED",
                        target_error or "无法唯一确定命令目标",
                    )
                )
            continue
        payload, payload_error = _resolve_payload(item, characters, outfits)
        if payload_error:
            rejected.append(
                _rejected_row(group, item, source, "RESOLUTION", payload_error)
            )
            continue
        expected = _version_scope(item.operation, page, target, panels, scenes)
        raw = {
            "schema_version": 1,
            "command_id": str(uuid4()),
            "command_group_id": group.command_group_id,
            "created_at": _now_rfc3339(),
            "target": target,
            "expected_version": expected,
            "operation": item.operation,
            "payload": payload,
            "source": source,
        }
        try:
            parsed.append(CommandEnvelope.model_validate(raw))
        except (ValidationError, ValueError) as exc:
            rejected.append(
                _rejected_row(group, item, source, "SCHEMA", str(exc)[:500])
            )

    meta = {
        "model": _model_meta(binding),
        "model_call_attempt_id": attempt_id,
        "duration_ms": duration_ms,
        "truncated": truncated or None,
    }

    if parsed or rejected:
        attach_parse_commands(db, group, parsed, rejected)
        first_result = {
            "kind": "ready",
            "commands_ready": len(parsed),
            "commands_failed": len(rejected),
            "clarify_options": clarify_options or None,
            "unsupported": [u.model_dump() for u in output.unsupported] or None,
        }
        first_result.update({k: v for k, v in meta.items() if v is not None})
        group.first_result = first_result
        return

    # Nothing anchored: clarifications (model or derived) drive the outcome.
    options = clarify_options
    # §8: model-produced clarify option ids must pass project/page ownership
    # before they reach the client — a hallucinated id is demoted to a
    # label-only hint (frontend renders id-less options as disabled hints).
    owned_ids = {
        "panel": {panel.id for panel in panels},
        "dialogue": {dialogue.id for dialogue in dialogues},
        "scene": {scene.id for scene in scenes},
        "character": {character.id for character in characters},
    }
    for clarification in output.clarifications:
        for option in clarification.options[:CLARIFY_OPTIONS_MAX]:
            if isinstance(option, dict) and option.get("id"):
                kind = str(option.get("kind") or "target")
                option_id = str(option["id"])
                verified = option_id if option_id in owned_ids.get(kind, set()) else None
                options.append(
                    _clarify_option(
                        kind,
                        verified,
                        str(option.get("label") or clarification.question),
                    )
                )
    reasons = [u.reason for u in output.unsupported if u.reason]
    questions = [c.question for c in output.clarifications if c.question]
    group.status = CommandGroupStatus.NEEDS_CLARIFICATION.value
    group.first_result = {
        "kind": "clarify",
        "reason": "; ".join([*questions, *reasons])[:500] or "需要补充信息",
        "clarify_options": options[:CLARIFY_OPTIONS_MAX],
        **{k: v for k, v in meta.items() if v is not None},
    }


def _project(db: Session, project_id: str):
    from app.models import Project

    return db.get(Project, project_id)


def _model_meta(binding) -> dict:
    resolved = binding.resolved
    provider_name = getattr(resolved.provider, "preset_key", None) or getattr(
        resolved.provider, "name", None
    )
    return {
        "provider": provider_name,
        "catalog_model_id": getattr(resolved.model, "id", None),
        "model_id": getattr(resolved.model, "provider_model_id", None),
    }


def _rejected_row(
    group: DirectorCommandGroup, item, source: dict, code: str, message: str
) -> dict:
    return {
        "command_id": str(uuid4()),
        "operation": (item.operation or "unknown")[:48],
        "target": {},
        "expected_version": {},
        "payload": dict(item.payload or {}),
        "source": source,
        "error": {"code": code, "message": message},
        "created_at": _now_rfc3339(),
    }
