"""Storyboard layout contract: read-path normalization and write validation.

Implements the frozen V02-30 data contract
(docs/v02-storyboard-layout-contract.md, V02-30A / Issue #48).

Coordinates are 0-1 normalized page space rounded to 4 decimals; the canvas
carries physical mm sizes. Stored ``Panel.bounds`` / ``Dialogue.region`` stay
the compatibility fields forever: this module never rewrites them and derives
``geometry`` / ``bubble`` at read time when the structured column is missing.
"""

from fastapi import HTTPException

COORD_DECIMALS = 4
MIN_PANEL_SIZE = 0.03
MIN_POLYGON_VERTICES = 3
MAX_POLYGON_VERTICES = 32
MAX_SOUND_EFFECTS_PER_PANEL = 32
MAX_ROTATION = 360.0
DEFAULT_BLEED_MM = 3
DEFAULT_SAFE_MM = 5

# Physical canvas presets by project page_ratio. Ratios without a preset fall
# back to the contract default (B5 portrait 182x257mm).
CANVAS_SIZE_BY_RATIO: dict[str, dict[str, int]] = {
    "b5_portrait": {"width_mm": 182, "height_mm": 257},
    "b5_landscape": {"width_mm": 257, "height_mm": 182},
    "a4_portrait": {"width_mm": 210, "height_mm": 297},
    "a4_landscape": {"width_mm": 297, "height_mm": 210},
    "a5_portrait": {"width_mm": 148, "height_mm": 210},
    "square": {"width_mm": 210, "height_mm": 210},
}

# Read-time anchor table for legacy ``region = {"preferred": ...}`` dicts. Keys
# map to a page quarter (upper_inner → page upper-right quarter, lower_outer →
# page lower-left quarter); values are the top-left origin of a standard bubble
# rect inside that quarter. Read-only constant, per contract §7.
LEGACY_PREFERRED_RECT_ORIGIN: dict[str, tuple[float, float]] = {
    "upper_inner": (0.72, 0.10),
    "upper_outer": (0.08, 0.10),
    "lower_inner": (0.72, 0.70),
    "lower_outer": (0.08, 0.70),
    "center": (0.40, 0.43),
}
LEGACY_BUBBLE_WIDTH = 0.20
LEGACY_BUBBLE_HEIGHT = 0.14
LEGACY_BUBBLE_TAIL_DROP = 0.12


def round4(value: float) -> float:
    """Round to the contract's 4-decimal coordinate precision, normalizing -0.0."""
    return round(float(value), COORD_DECIMALS) + 0.0


def _geometry_error(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


# --- read-path normalization (never writes the database) -------------------


def default_canvas(page_ratio: str | None) -> dict:
    """Lazy canvas default from the project page_ratio (contract §3.1)."""
    preset = CANVAS_SIZE_BY_RATIO.get((page_ratio or "").strip()) or CANVAS_SIZE_BY_RATIO[
        "b5_portrait"
    ]
    return {
        "width_mm": preset["width_mm"],
        "height_mm": preset["height_mm"],
        "bleed_mm": DEFAULT_BLEED_MM,
        "safe_mm": DEFAULT_SAFE_MM,
        "unit": "mm",
    }


def read_canvas(page, page_ratio: str | None) -> dict:
    stored = page.canvas
    if isinstance(stored, dict) and stored:
        return stored
    return default_canvas(page_ratio)


def read_panel_geometry(panel) -> dict | None:
    """Stored geometry as-is, else derived from the flat bounds (contract §11)."""
    stored = panel.geometry
    if isinstance(stored, dict) and stored:
        return stored
    bounds = panel.bounds
    if not isinstance(bounds, dict):
        return None
    try:
        rect = _canonical_rect_values(bounds, min_size=MIN_PANEL_SIZE)
    except HTTPException:
        return None
    return {
        "type": "rect",
        "rect": rect,
        "rotation": 0,
        "z_order": max(int(panel.reading_order), 1),
    }


def read_bubble(dialogue) -> dict | None:
    """Stored bubble as-is, else the legacy ``preferred`` anchor mapping."""
    stored = dialogue.bubble
    if isinstance(stored, dict) and stored:
        return stored
    region = dialogue.region
    if not isinstance(region, dict):
        return None
    return legacy_bubble_geometry(region.get("preferred"))


def legacy_bubble_geometry(preferred) -> dict | None:
    if not isinstance(preferred, str):
        return None
    origin = LEGACY_PREFERRED_RECT_ORIGIN.get(preferred.strip())
    if origin is None:
        return None
    x, y = origin
    anchor_x = round4(x + LEGACY_BUBBLE_WIDTH / 2)
    anchor_y = round4(y + LEGACY_BUBBLE_HEIGHT)
    return {
        "type": "rect",
        "rect": {
            "x": round4(x),
            "y": round4(y),
            "width": round4(LEGACY_BUBBLE_WIDTH),
            "height": round4(LEGACY_BUBBLE_HEIGHT),
        },
        "anchor": {"x": anchor_x, "y": anchor_y},
        "tail_target": {
            "x": anchor_x,
            "y": round4(min(anchor_y + LEGACY_BUBBLE_TAIL_DROP, 1.0)),
        },
        "rotation": 0,
        "mapped_from_legacy": True,
    }


def read_sound_effects(panel) -> list:
    """Wrap legacy string elements into the structured shape (contract §12)."""
    wrapped = []
    for item in panel.sound_effects or []:
        if isinstance(item, str):
            wrapped.append({"text": item, "x": None, "y": None, "rotation": 0, "size": None})
        elif isinstance(item, dict):
            wrapped.append(item)
    return wrapped


# --- write validation (server-enforced contract §9.1) ----------------------


def _canonical_rect_values(value: dict, *, min_size: float) -> dict:
    if not isinstance(value, dict):
        raise _geometry_error("矩形必须是对象")
    try:
        x = float(value["x"])
        y = float(value["y"])
        width = float(value["width"])
        height = float(value["height"])
    except (KeyError, TypeError, ValueError) as error:
        raise _geometry_error("矩形需要 x/y/width/height 数值") from error
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise _geometry_error("矩形原点必须在 0-1 页面内")
    if width <= 0 or height <= 0:
        raise _geometry_error("矩形宽高必须大于 0")
    if width < min_size or height < min_size:
        raise _geometry_error(f"矩形宽高不能小于 {min_size}")
    if round(x + width, 6) > 1.0 or round(y + height, 6) > 1.0:
        raise _geometry_error("矩形越出 0-1 页面范围")
    return {
        "x": round4(x),
        "y": round4(y),
        "width": round4(width),
        "height": round4(height),
    }


def canonical_panel_bounds(value: dict) -> dict:
    """Validate + round a flat panel rect (0-1 page space, min 0.03 size)."""
    return _canonical_rect_values(value, min_size=MIN_PANEL_SIZE)


def _canonical_point(value) -> dict:
    if not isinstance(value, dict):
        raise _geometry_error("坐标点必须是对象")
    try:
        x = float(value["x"])
        y = float(value["y"])
    except (KeyError, TypeError, ValueError) as error:
        raise _geometry_error("坐标点需要 x/y 数值") from error
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise _geometry_error("坐标点必须在 0-1 页面内")
    return {"x": round4(x), "y": round4(y)}


def _canonical_rotation(value) -> float:
    try:
        rotation = float(value)
    except (TypeError, ValueError) as error:
        raise _geometry_error("旋转角度必须是数值") from error
    if not (-MAX_ROTATION <= rotation <= MAX_ROTATION):
        raise _geometry_error("旋转角度必须在 -360 到 360 之间")
    return round4(rotation)


def canonical_panel_geometry(value: dict, *, reading_order: int) -> dict:
    """Validate + round structural panel geometry (contract §4/§5)."""
    if not isinstance(value, dict):
        raise _geometry_error("格子几何必须是对象")
    geometry_type = value.get("type")
    if geometry_type not in {"rect", "polygon"}:
        raise _geometry_error("格子几何 type 必须是 rect 或 polygon")
    z_order = value.get("z_order")
    if z_order is None:
        z_order = max(int(reading_order), 1)
    else:
        try:
            z_order = int(z_order)
        except (TypeError, ValueError) as error:
            raise _geometry_error("z_order 必须是整数") from error
        if z_order < 1:
            raise _geometry_error("z_order 必须大于等于 1")
    rotation = _canonical_rotation(value.get("rotation", 0))
    if geometry_type == "rect":
        if value.get("polygon") is not None:
            raise _geometry_error("polygon 仅在 type=polygon 时可用")
        rect_raw = value.get("rect")
        if rect_raw is None:
            raise _geometry_error("矩形几何缺少 rect")
        return {
            "type": "rect",
            "rect": _canonical_rect_values(rect_raw, min_size=MIN_PANEL_SIZE),
            "rotation": rotation,
            "z_order": z_order,
        }
    if value.get("rect") is not None:
        raise _geometry_error("rect 仅在 type=rect 时可用")
    if rotation != 0:
        raise _geometry_error("多边形首版不支持旋转")
    vertices = value.get("polygon")
    if not isinstance(vertices, list):
        raise _geometry_error("多边形几何缺少 polygon 顶点")
    if not (MIN_POLYGON_VERTICES <= len(vertices) <= MAX_POLYGON_VERTICES):
        raise _geometry_error(
            f"多边形顶点数量必须在 {MIN_POLYGON_VERTICES}-{MAX_POLYGON_VERTICES} 之间"
        )
    return {
        "type": "polygon",
        "polygon": [_canonical_point(vertex) for vertex in vertices],
        "rotation": rotation,
        "z_order": z_order,
    }


def canonical_bubble(value: dict) -> dict:
    """Validate + round structured bubble geometry (contract §7)."""
    if not isinstance(value, dict):
        raise _geometry_error("气泡几何必须是对象")
    bubble_type = value.get("type", "rect")
    if bubble_type not in {"rect", "ellipse"}:
        raise _geometry_error("气泡 type 必须是 rect 或 ellipse")
    rect_raw = value.get("rect")
    if rect_raw is None:
        raise _geometry_error("气泡几何缺少 rect")
    rect = _canonical_rect_values(rect_raw, min_size=0.0)
    bubble: dict = {"type": bubble_type, "rect": rect}
    anchor = value.get("anchor")
    if anchor is not None:
        bubble["anchor"] = _canonical_point(anchor)
    tail_target = value.get("tail_target")
    if tail_target is not None:
        bubble["tail_target"] = _canonical_point(tail_target)
    bubble["rotation"] = _canonical_rotation(value.get("rotation", 0))
    text_region_raw = value.get("text_region")
    if text_region_raw is not None:
        text_region = _canonical_rect_values(text_region_raw, min_size=0.0)
        inside = (
            text_region["x"] >= rect["x"]
            and text_region["y"] >= rect["y"]
            and round(text_region["x"] + text_region["width"], 6)
            <= round(rect["x"] + rect["width"], 6)
            and round(text_region["y"] + text_region["height"], 6)
            <= round(rect["y"] + rect["height"], 6)
        )
        if not inside:
            raise _geometry_error("文字区域必须位于气泡矩形内")
        bubble["text_region"] = text_region
    bubble["mapped_from_legacy"] = False
    return bubble


def canonical_sound_effects(values: list) -> list:
    """Validate structured sound effects; legacy strings pass through."""
    if len(values) > MAX_SOUND_EFFECTS_PER_PANEL:
        raise _geometry_error(f"每页拟声词不能超过 {MAX_SOUND_EFFECTS_PER_PANEL} 个")
    canonical = []
    for item in values:
        if isinstance(item, str):
            canonical.append(item)
            continue
        if not isinstance(item, dict):
            raise _geometry_error("拟声词必须是文本或结构化对象")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _geometry_error("拟声词文本不能为空")
        effect: dict = {"text": text}
        for key in ("x", "y", "size"):
            coordinate = item.get(key)
            if coordinate is not None:
                coordinate = float(coordinate)
                if not (0.0 <= coordinate <= 1.0):
                    raise _geometry_error("拟声词坐标必须在 0-1 内")
                effect[key] = round4(coordinate)
        effect["rotation"] = _canonical_rotation(item.get("rotation", 0))
        canonical.append(effect)
    return canonical


def resolve_panel_shape(
    *,
    stored_bounds: dict,
    stored_geometry: dict | None,
    reading_order: int,
    bounds: dict | None = None,
    geometry: dict | None = None,
    bounds_given: bool = False,
    geometry_given: bool = False,
) -> tuple[dict, dict | None]:
    """Resolve the canonical (bounds, geometry) pair after a panel write.

    ``bounds`` and ``geometry.rect`` are the same fact for rect panels; every
    write path keeps them identical so the two fields never diverge. A bounds
    write alone re-syncs an existing stored rect geometry; a rect geometry
    write alone re-derives bounds. Omitting geometry in a whole-page snapshot
    clears it so the read path re-derives from the new bounds.
    """
    if bounds_given and geometry_given:
        new_bounds = canonical_panel_bounds(bounds) if bounds is not None else stored_bounds
        new_geometry = (
            canonical_panel_geometry(geometry, reading_order=reading_order)
            if geometry is not None
            else None
        )
        if (
            new_geometry is not None
            and new_geometry["type"] == "rect"
            and new_geometry["rect"] != new_bounds
        ):
            raise _geometry_error("geometry.rect 与 bounds 不一致")
        return new_bounds, new_geometry
    if bounds_given:
        new_bounds = canonical_panel_bounds(bounds) if bounds is not None else stored_bounds
        if isinstance(stored_geometry, dict) and stored_geometry.get("type") == "rect":
            return new_bounds, {**stored_geometry, "rect": dict(new_bounds)}
        return new_bounds, stored_geometry
    if geometry_given:
        if geometry is None:
            return stored_bounds, None
        new_geometry = canonical_panel_geometry(geometry, reading_order=reading_order)
        if new_geometry["type"] == "rect":
            new_bounds = canonical_panel_bounds(dict(new_geometry["rect"]))
            return new_bounds, {**new_geometry, "rect": dict(new_bounds)}
        return stored_bounds, new_geometry
    return stored_bounds, stored_geometry
