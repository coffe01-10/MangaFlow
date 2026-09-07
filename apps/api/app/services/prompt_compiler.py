import hashlib
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Character, Dialogue, MangaPage, Outfit, Panel, Project, Scene, StyleProfile

LOGGER = logging.getLogger("mangaflow.prompt_compiler")

PAGE_TEMPLATE_VERSION = "page-v2.2.0"

# Prompt-size budget (#160): the compiled page/inspection prompt embeds only
# the page's cast in full and reduces every other project character to a
# name-only roster, so per-call cost stops growing linearly with the total
# project cast (the product targets 2M-char web novels with hundreds of
# characters). 60_000 characters keeps a single page prompt comfortably
# inside current provider context windows while leaving headroom for
# source_text and the panel layout; when the first build still exceeds it the
# cast descriptions are compressed to the tighter limit below and the prompt
# is rebuilt once.
PROMPT_CHAR_BUDGET = 60_000
CAST_DESCRIPTION_MAX_CHARS = 8_000
CAST_DESCRIPTION_COMPRESSED_MAX_CHARS = 2_000
# Per-character alias embedding cap for prompts; well under the 40-alias API
# cap because aliases are matching hints, not content.
CAST_ALIAS_PROMPT_LIMIT = 8
# Name-only roster for non-cast project characters, truncated to bound the
# summary line itself on huge casts.
OTHER_CHARACTERS_ROSTER_MAX_CHARS = 2_000
# Per-block cap for embedded JSON structures: outfit components / state
# rules / locked fields, style.profile and per-cast locked feature lists.
# The #160 caps bounded the description text, but these structural blocks
# were unbounded side doors into the same prompt; 2_000 matches the
# compressed description tier.
STRUCTURED_BLOCK_MAX_CHARS = 2_000


def _bound_structured_block(value: object, max_chars: int) -> object:
    """Bound one embedded JSON block to ``max_chars`` of serialized text.

    Cast descriptions cap plain strings by code-point slicing; these fields
    are JSON structures, so an oversized block drops trailing list items /
    dict keys (deterministic hard cuts, #160 style) until the compact
    serialization fits. A single surviving item that still busts the cap is
    bounded recursively (leaf strings are code-point truncated); if even
    that cannot fit — only reachable through pathological nesting — the
    block collapses to an empty list/dict, so the result always
    re-serializes at or under the cap.
    """

    def serialized(item: object) -> str:
        return json.dumps(item, ensure_ascii=False, separators=(",", ":"))

    max_chars = max(max_chars, 2)  # "" / [] / {} serialize to 2 chars
    if len(serialized(value)) <= max_chars:
        return value
    if isinstance(value, str):
        # Reserve room for the JSON quotes/escapes, then shave code points
        # until the serialized form fits.
        trimmed = value[: max_chars - 2]
        while trimmed and len(serialized(trimmed)) > max_chars:
            trimmed = trimmed[:-1]
        return trimmed
    if isinstance(value, list):
        for count in range(len(value), 0, -1):
            prefix = value[:count]
            if len(serialized(prefix)) <= max_chars:
                return prefix
        bounded = _bound_structured_block(value[0], max_chars - 2)
        if len(serialized([bounded])) <= max_chars:
            return [bounded]
        return []
    if isinstance(value, dict):
        keys = list(value)
        for count in range(len(keys), 0, -1):
            subset = {key: value[key] for key in keys[:count]}
            if len(serialized(subset)) <= max_chars:
                return subset
        key = keys[0]
        original = value[key]
        # A key so large it alone busts the cap is truncated first; its
        # budget leaves room for the {"":} wrapper and a minimal value.
        key_budget = max(2, max_chars - 5)
        if len(serialized(key)) > key_budget:
            key = _bound_structured_block(key, key_budget)
        inner = max(2, max_chars - len(serialized(key)) - 3)
        bounded = _bound_structured_block(original, inner)
        if len(serialized({key: bounded})) <= max_chars:
            return {key: bounded}
        return {}
    # Non-container scalars (bool/int/float/None) are tiny in practice, but a
    # hostile huge number would serialize unbounded through this fallthrough —
    # stringify and truncate so even this branch re-serializes under the cap.
    return _bound_structured_block(str(value), max_chars)


def _cast_bible_entry(
    item: Character,
    fact: dict | None,
    *,
    description_limit: int,
) -> dict:
    """Full bible entry for one cast member, size-capped per #160.

    ``fact`` is the queue-time ``character_packages`` snapshot block: the
    frozen name and identity/visual/negative specs replace the live reads
    (contract §8.5) while the description/locked features stay live reads.
    """

    if fact:
        return {
            "id": item.id,
            "primary_name": fact.get("primary_name") or item.primary_name,
            # get-with-default only falls back when the key is absent:
            # a deliberately frozen empty alias list must not fall back
            # to the live aliases (contract §8.2 frozen facts).
            "aliases": (fact.get("aliases", item.aliases) or [])[:CAST_ALIAS_PROMPT_LIMIT],
            "description": (item.canonical_description or "")[:description_limit],
            "locked_features": _bound_structured_block(
                item.locked_features, STRUCTURED_BLOCK_MAX_CHARS
            ),
            "forbidden_changes": _bound_structured_block(
                item.forbidden_changes, STRUCTURED_BLOCK_MAX_CHARS
            ),
            "identity_spec": fact.get("identity_spec") or {},
            "visual_spec": fact.get("visual_spec") or {},
            "negative_constraints": fact.get("negative_constraints") or [],
        }
    return {
        "id": item.id,
        "primary_name": item.primary_name,
        "aliases": (item.aliases or [])[:CAST_ALIAS_PROMPT_LIMIT],
        "description": (item.canonical_description or "")[:description_limit],
        "locked_features": _bound_structured_block(
            item.locked_features, STRUCTURED_BLOCK_MAX_CHARS
        ),
        "forbidden_changes": _bound_structured_block(
            item.forbidden_changes, STRUCTURED_BLOCK_MAX_CHARS
        ),
    }


def _character_match_names(item: Character, fact: dict | None) -> list[str]:
    """Uncapped name list (primary name first) for deterministic matching.

    The rendered prompt caps each cast entry's aliases at
    CAST_ALIAS_PROMPT_LIMIT, but PRESENCE compliance must map every
    registered name a model may report — a 9th alias or any decorated
    form — so the snapshot carries the full list outside the rendered
    ``input`` payload (#164). Frozen facts resolve names exactly like
    ``_cast_bible_entry`` (contract §8.2/§8.5).
    """

    if fact:
        return [
            fact.get("primary_name") or item.primary_name,
            *(fact.get("aliases", item.aliases) or []),
        ]
    return [item.primary_name, *(item.aliases or [])]


def compile_page_prompt(
    db: Session,
    page: MangaPage,
    project: Project,
    scene_background: str | None = None,
    character_package_facts: dict[str, dict] | None = None,
) -> tuple[str, dict]:
    """Compile the generation prompt for one page.

    ``scene_background`` replaces the panel-bound background text when the
    queue-time snapshot holds scene asset facts: the frozen snapshot is the
    compile-time contract (docs/v02-scene-asset-contract.md §5), while
    ``Panel.background`` stays untouched as the storyboard snapshot.

    ``character_package_facts`` carries the queue-time ``character_packages``
    snapshot block: bible entries for those characters use the frozen name and
    the frozen identity/visual/negative specs instead of live Character rows
    (contract §8.5). Characters without facts keep the live reads.

    Character embedding is cast-first (#160): only the characters this page
    actually depicts — panel cast, panel presence keys, and dialogue speakers —
    are embedded in full (aliases + description, capped); every other project
    character is aggregated into one truncated name-only roster so the prompt
    no longer grows with the whole project bible. The inspection handler
    compiles this same snapshot, so both paid paths share the budget.
    """

    characters = list(
        db.scalars(
            select(Character)
            .where(Character.project_id == project.id)
            .order_by(Character.primary_name)
        )
    )
    source_ranges = page.source_coverage.get("ranges", [])
    source_text = "\n".join(item.get("text", "") for item in source_ranges)
    frozen_facts = character_package_facts or {}
    character_names = {
        item.id: (
            frozen_facts[item.id].get("primary_name") or item.primary_name
            if item.id in frozen_facts
            else item.primary_name
        )
        for item in characters
    }
    scenes = (
        list(db.scalars(select(Scene).where(Scene.id.in_(page.scene_ids))))
        if page.scene_ids
        else []
    )
    outfit_ids = {
        outfit_id
        for scene in scenes
        for outfit_id in scene.outfit_assignments.values()
        if outfit_id
    }
    outfits = (
        list(db.scalars(select(Outfit).where(Outfit.id.in_(outfit_ids)))) if outfit_ids else []
    )
    style = (
        db.get(StyleProfile, page.style_id or project.default_style_id)
        if (page.style_id or project.default_style_id)
        else None
    )
    color_mode = style.color_mode if style else "monochrome"
    panels = list(
        db.scalars(select(Panel).where(Panel.page_id == page.id).order_by(Panel.reading_order))
    )
    panel_script = []
    page_cast_ids: set[str] = set()
    for panel in panels:
        dialogues = list(
            db.scalars(
                select(Dialogue)
                .where(Dialogue.panel_id == panel.id)
                .order_by(Dialogue.reading_order)
            )
        )
        page_cast_ids.update(panel.characters or [])
        page_cast_ids.update((panel.character_presence or {}).keys())
        page_cast_ids.update(
            item.speaker_character_id for item in dialogues if item.speaker_character_id
        )
        panel_script.append(
            {
                "reading_order": panel.reading_order,
                "bounds": panel.bounds,
                "shot_type": panel.shot_type,
                "camera_angle": panel.camera_angle,
                "characters": panel.characters,
                "character_presence": panel.character_presence,
                "props": panel.props,
                "actions": panel.actions,
                "expressions": panel.expressions,
                "background": scene_background or panel.background,
                "dialogues": [
                    {
                        "speaker": character_names.get(item.speaker_character_id, "旁白"),
                        "text": item.target_text,
                        "reading_order": item.reading_order,
                        "text_direction": item.text_direction,
                        "region": item.region,
                        "rewrite_forbidden": item.rewrite_forbidden,
                    }
                    for item in dialogues
                ],
                "bleed": panel.bleed,
                "borderless": panel.borderless,
            }
        )
    other_names = [
        name.strip()
        for item in characters
        if item.id not in page_cast_ids
        for name in [character_names.get(item.id) or ""]
        if name.strip()
    ]
    other_characters = {
        "count": len(other_names),
        "names": "、".join(other_names)[:OTHER_CHARACTERS_ROSTER_MAX_CHARS],
    }

    def build_payload(description_limit: int) -> dict:
        return {
            "project": {
                "language": project.language,
                "reading_direction": project.reading_direction,
                "page_ratio": project.page_ratio,
                "workflow_mode": project.workflow_mode.value,
            },
            "page": {
                "number": page.page_number,
                "panel_count": page.panel_count,
                "estimated_text_chars": page.estimated_text_chars,
                "estimated_bubbles": page.estimated_bubbles,
                "source_text": source_text,
                "layout": panel_script,
            },
            # Cast-first bible (#160): full entries for the page cast only.
            "characters": [
                _cast_bible_entry(
                    item, frozen_facts.get(item.id), description_limit=description_limit
                )
                for item in characters
                if item.id in page_cast_ids
            ],
            "other_characters": other_characters,
            "scene_outfits": [
                {"scene_id": scene.id, "assignments": scene.outfit_assignments} for scene in scenes
            ],
            "outfits": [
                {
                    "id": outfit.id,
                    "character_id": outfit.character_id,
                    "name": outfit.name,
                    "components": _bound_structured_block(
                        outfit.components, STRUCTURED_BLOCK_MAX_CHARS
                    ),
                    "state_rules": _bound_structured_block(
                        outfit.state_rules, STRUCTURED_BLOCK_MAX_CHARS
                    ),
                    "locked_fields": _bound_structured_block(
                        outfit.locked_fields, STRUCTURED_BLOCK_MAX_CHARS
                    ),
                }
                for outfit in outfits
            ],
            "style": (
                {
                    "id": style.id,
                    "name": style.name,
                    "color_mode": style.color_mode,
                    "profile": _bound_structured_block(
                        style.profile, STRUCTURED_BLOCK_MAX_CHARS
                    ),
                }
                if style
                else None
            ),
        }

    mode_instruction = {
        "AUTO": "自动模式：在不改变剧情的前提下主动补足镜头、表演、环境和过场细节。",
        "DIRECTOR": "导演模式：严格执行已给出的格位、镜头、人物、服装和动作，不擅自改动。",
        "SEMI_AUTO": "半自动模式：严格保持剧情、人物与服装，允许补足不影响剧情的环境和表演细节。",
    }[project.workflow_mode.value]
    director_role = "黑白网点日式漫画" if color_mode == "monochrome" else "彩色日式漫画"
    render_rules = (
        "使用干净墨线、专业网点、明确黑白对比与克制留白；"
        if color_mode == "monochrome"
        else "使用统一色彩脚本、稳定肤色发色与服装配色、清晰光影层次；不得擅自改变跨格固有色；"
    )
    style_dimensions = (
        "线稿、网点、黑白对比和构图规则"
        if color_mode == "monochrome"
        else "线稿、色板、上色方式、光影和构图"
    )

    def build_prompt(payload: dict) -> str:
        return f"""你是{director_role}的单页导演。请只生成第 {page.page_number} 页，不生成相邻页面。
阅读方向必须为从右到左，严格按照 layout 中的格位、阅读顺序和镜头生成 {page.panel_count} 格。
中文文字必须严格保留，不得总结、改写或遗漏。
character_presence 为 VISIBLE 的角色必须在对应画面中画出；OFFSCREEN/MENTIONED 的角色不得出现。
{mode_instruction}
原文与页面结构如下：
{json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
要求：采用专业日本漫画页面语言，格子大小有节奏变化，右上开始、左下结束；
{render_rules}使用清晰格线；严格使用 scene_outfits 指定服装；角色身份、服装、道具和场景连续；
若存在 style.profile，按其总结的{style_dimensions}执行；
other_characters 仅为项目其他角色名单，不得主动画入本页；
禁止加入原文没有的关键剧情；输出一张完整竖版漫画页。
"""

    payload = build_payload(CAST_DESCRIPTION_MAX_CHARS)
    prompt = build_prompt(payload)
    if len(prompt) > PROMPT_CHAR_BUDGET:
        # Over-budget projects get one deterministic compression tier (#160):
        # cast descriptions drop to the tighter cap and the prompt is rebuilt.
        payload = build_payload(CAST_DESCRIPTION_COMPRESSED_MAX_CHARS)
        prompt = build_prompt(payload)
        if len(prompt) > PROMPT_CHAR_BUDGET:
            # The single tier could not fit (e.g. huge source_text/layout or
            # more cast than even 2_000-char descriptions can hold). Emit an
            # explicit record instead of failing silently, but never raise:
            # the caller already paid for this pipeline step.
            LOGGER.warning(
                "compile_page_prompt 压缩重建后仍超出提示词预算：project=%s page=%s"
                "(page_number=%s) final_length=%d budget=%d，按现状继续",
                project.id,
                page.id,
                page.page_number,
                len(prompt),
                PROMPT_CHAR_BUDGET,
            )
    # Uncapped matching index for the inspection handler (#164): rendered
    # prompt text keeps the 8-alias cap, while this snapshot field carries
    # every registered name so PRESENCE compliance maps a 9th alias instead
    # of synthesizing a false MISSING row.
    match_aliases = {
        item.id: _character_match_names(item, frozen_facts.get(item.id))
        for item in characters
        if item.id in page_cast_ids
    }
    snapshot = {
        "template": PAGE_TEMPLATE_VERSION,
        "checksum": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "input": payload,
        "match_aliases": match_aliases,
    }
    return prompt, snapshot
