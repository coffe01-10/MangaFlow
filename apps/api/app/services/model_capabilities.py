"""Region-edit capability bits on catalog models (V02-44A matrix §7).

Frozen decision (``docs/v02-image-edit-capability-matrix.md`` §7.2): editing
capabilities hang off concrete catalog models; protocols and adapters only
declare the request surface they can actually express. Every bit is
fail-closed — an absent or UNKNOWN value is never upgraded to true, so region
entries must refuse with ``UNSUPPORTED_CAPABILITY`` instead of silently
degrading to a whole-page image-to-image edit or switching model/provider.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Final

ACCEPTS_EXPLICIT_MASK: Final = "accepts_explicit_mask"
SUPPORTS_INSTRUCTION_REGION_EDIT: Final = "supports_instruction_region_edit"
PRESERVES_OUTSIDE_REGION: Final = "preserves_outside_region"
WHOLE_IMAGE_REFERENCE_ONLY: Final = "whole_image_reference_only"

REGION_CAPABILITY_KEYS: Final = (
    ACCEPTS_EXPLICIT_MASK,
    SUPPORTS_INSTRUCTION_REGION_EDIT,
    PRESERVES_OUTSIDE_REGION,
    WHOLE_IMAGE_REFERENCE_ONLY,
)

# §7.2 provenance: DECLARED by the preset/manual row, DISCOVERED from the
# provider listing, VERIFIED by a real capability probe. Anything else
# (missing/UNKNOWN) serializes as UNSPECIFIED and never grants the bit.
REGION_CAPABILITY_SOURCES: Final = ("DECLARED", "DISCOVERED", "VERIFIED")
REGION_CAPABILITY_SOURCE_KEY: Final = "region_capability_sources"
REGION_CAPABILITY_SOURCE_UNSPECIFIED: Final = "UNSPECIFIED"

# ---------------------------------------------------------------------------
# Structured capability dimensions (P0-1 能力位契约细化).
#
# The V02-44B boolean bits above stay the contract for the routing/worker
# gates that already consume them; the keys below add the normalized
# dimensions that catalog serialization, admin writes and the settings UI
# negotiate. Every dimension is fail-closed: absent/UNKNOWN/UNSPECIFIED never
# reads as supported, and provenance lives in the ``capability_sources`` map
# keyed by ``"<dimension>"`` or ``"<dimension>.<entry>"``.
# ---------------------------------------------------------------------------

# Normalized edit-mode keys. ``mask``/``instruction_region``/
# ``whole_image_reference`` mirror the legacy bits one-to-one (the read port
# falls back to them when ``edit_modes`` is absent); ``in_image_text_edit``
# has no legacy counterpart and is only readable through the structured map.
EDIT_MODE_MASK: Final = "mask"
EDIT_MODE_INSTRUCTION_REGION: Final = "instruction_region"
EDIT_MODE_WHOLE_IMAGE_REFERENCE: Final = "whole_image_reference"
EDIT_MODE_IN_IMAGE_TEXT: Final = "in_image_text_edit"

EDIT_MODE_KEYS: Final = (
    EDIT_MODE_MASK,
    EDIT_MODE_INSTRUCTION_REGION,
    EDIT_MODE_WHOLE_IMAGE_REFERENCE,
    EDIT_MODE_IN_IMAGE_TEXT,
)

EDIT_MODE_LABELS: Final[dict[str, str]] = {
    EDIT_MODE_MASK: "显式 mask 局部编辑",
    EDIT_MODE_INSTRUCTION_REGION: "instruction 区域编辑",
    EDIT_MODE_WHOLE_IMAGE_REFERENCE: "整图参考编辑",
    EDIT_MODE_IN_IMAGE_TEXT: "图中文字原位编辑",
}

EDIT_MODES_KEY: Final = "edit_modes"
RESOLUTION_TIERS_KEY: Final = "resolution_tiers"
MEDIA_KEY: Final = "media"
CAPABILITY_SOURCE_KEY: Final = "capability_sources"
CAPABILITY_SOURCE_UNSPECIFIED: Final = "UNSPECIFIED"
CAPABILITY_SOURCES: Final = REGION_CAPABILITY_SOURCES

# Reserved media slots (P0-1: video/audio stay UNKNOWN placeholders until a
# verifiable provider surface exists).
MEDIA_SLOTS: Final = ("video", "audio")

_EDIT_MODE_LEGACY_BITS: Final[dict[str, str]] = {
    EDIT_MODE_MASK: ACCEPTS_EXPLICIT_MASK,
    EDIT_MODE_INSTRUCTION_REGION: SUPPORTS_INSTRUCTION_REGION_EDIT,
    EDIT_MODE_WHOLE_IMAGE_REFERENCE: WHOLE_IMAGE_REFERENCE_ONLY,
}

_KNOWN_RESOLUTION_TIERS: Final = ("1K", "2K", "4K")


def _structured_source(
    capabilities: dict[str, Any] | None, path: str
) -> str:
    """Provenance of a structured dimension from ``capability_sources``."""

    sources = (capabilities or {}).get(CAPABILITY_SOURCE_KEY)
    if isinstance(sources, dict):
        source = sources.get(path)
        if source in CAPABILITY_SOURCES:
            return str(source)
    return CAPABILITY_SOURCE_UNSPECIFIED


def _structured_entry(value: Any) -> dict[str, Any] | None:
    """Coerce one ``{"supported": bool, "source": str}`` entry or reject."""

    if not isinstance(value, dict):
        return None
    supported = value.get("supported")
    if not isinstance(supported, bool):
        return None
    source = value.get("source")
    return {
        "supported": supported,
        "source": source if source in CAPABILITY_SOURCES else CAPABILITY_SOURCE_UNSPECIFIED,
    }


def edit_mode_supported(capabilities: dict[str, Any] | None, mode: str) -> bool:
    """Fail-closed read of one normalized edit mode.

    ``edit_modes`` is authoritative when present; absent keys fall back to the
    matching V02-44B boolean so existing catalog rows keep their declared
    meaning. ``in_image_text_edit`` has no legacy bit and stays unsupported
    until explicitly declared.
    """

    if mode not in EDIT_MODE_KEYS:
        raise ValueError(f"未知编辑模式：{mode}")
    declared = (capabilities or {}).get(EDIT_MODES_KEY)
    if isinstance(declared, dict) and mode in declared:
        entry = _structured_entry(declared.get(mode))
        return bool(entry and entry["supported"])
    legacy_bit = _EDIT_MODE_LEGACY_BITS.get(mode)
    if legacy_bit is None:
        return False
    return region_capability_enabled(capabilities, legacy_bit)


def edit_mode_source(capabilities: dict[str, Any] | None, mode: str) -> str:
    """Provenance of one edit mode, including the legacy-bit fallback."""

    if mode not in EDIT_MODE_KEYS:
        raise ValueError(f"未知编辑模式：{mode}")
    declared = (capabilities or {}).get(EDIT_MODES_KEY)
    if isinstance(declared, dict) and mode in declared:
        entry = _structured_entry(declared.get(mode))
        if entry is not None:
            return str(entry["source"])
        return CAPABILITY_SOURCE_UNSPECIFIED
    legacy_bit = _EDIT_MODE_LEGACY_BITS.get(mode)
    if legacy_bit is None:
        return CAPABILITY_SOURCE_UNSPECIFIED
    return region_capability_source(capabilities, legacy_bit)


def edit_mode_summary(
    capabilities: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Per-mode ``{supported, source}`` for API serialization/tests."""

    return {
        mode: {
            "supported": edit_mode_supported(capabilities, mode),
            "source": edit_mode_source(capabilities, mode),
        }
        for mode in EDIT_MODE_KEYS
    }


def model_edit_mode_supported(model: Any, mode: str) -> bool:
    """Catalog-model wrapper around :func:`edit_mode_supported`."""

    return edit_mode_supported(getattr(model, "capabilities", None), mode)


def resolution_tier_map(
    capabilities: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Normalized ``{resolution: {supported, source}}`` read.

    The structured ``resolution_tiers`` map is authoritative; when it is
    absent the legacy ``resolutions`` list degrades to ``supported=True`` rows
    with provenance taken from ``capability_sources["resolutions"]`` (a bare
    list without provenance serializes as UNSPECIFIED, never as a real
    source). Malformed structured entries read as unsupported.
    """

    tiers = (capabilities or {}).get(RESOLUTION_TIERS_KEY)
    if isinstance(tiers, dict):
        result: dict[str, dict[str, Any]] = {}
        for name, value in tiers.items():
            if not isinstance(name, str):
                continue
            entry = _structured_entry(value)
            result[name] = entry or {
                "supported": False,
                "source": CAPABILITY_SOURCE_UNSPECIFIED,
            }
        return result
    raw = (capabilities or {}).get("resolutions")
    if isinstance(raw, str):
        raw = [raw]
    names = [item for item in raw if isinstance(item, str)] if isinstance(
        raw, (list, tuple)
    ) else []
    source = _structured_source(capabilities, "resolutions")
    return {
        name: {"supported": True, "source": source}
        for name in names
    }


def capability_resolution_supported(
    capabilities: dict[str, Any] | None, resolution: str
) -> bool | None:
    """Fail-closed tier check: None = undeclared (caller keeps legacy
    semantics), True/False only when the tier map says so."""

    tiers = (capabilities or {}).get(RESOLUTION_TIERS_KEY)
    if not isinstance(tiers, dict) or not tiers:
        return None
    entry = _structured_entry(tiers.get(resolution))
    return bool(entry and entry["supported"])


def media_capability_summary(
    capabilities: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Reserved video/audio slots; absent always reads UNSPECIFIED+false."""

    declared = (capabilities or {}).get(MEDIA_KEY)
    result: dict[str, dict[str, Any]] = {}
    for slot in MEDIA_SLOTS:
        entry = (
            _structured_entry(declared.get(slot))
            if isinstance(declared, dict)
            else None
        )
        result[slot] = entry or {
            "supported": False,
            "source": CAPABILITY_SOURCE_UNSPECIFIED,
        }
    return result


def declare_edit_modes(
    *,
    mask: bool = False,
    instruction_region: bool = False,
    whole_image_reference: bool = False,
    in_image_text_edit: bool = False,
    source: str = "DECLARED",
    verified: tuple[str, ...] = (),
    unspecified: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Structured ``edit_modes`` fragment mirroring :func:`declare_region_capabilities`.

    ``verified`` names modes whose provenance is a real probe result;
    ``unspecified`` names modes that must stay honestly UNKNOWN (their
    supported bit is forced to False and their provenance to UNSPECIFIED).
    Every other mode carries ``source``. Modes left at their default are
    still written so the declaration is explicit rather than guessed later.
    """

    if source not in CAPABILITY_SOURCES:
        raise ValueError(f"未知能力来源：{source}")
    unknown = set(unspecified)
    values = {
        EDIT_MODE_MASK: bool(mask),
        EDIT_MODE_INSTRUCTION_REGION: bool(instruction_region),
        EDIT_MODE_WHOLE_IMAGE_REFERENCE: bool(whole_image_reference),
        EDIT_MODE_IN_IMAGE_TEXT: bool(in_image_text_edit),
    }
    for mode in unknown:
        if mode not in values:
            raise ValueError(f"未知编辑模式：{mode}")
        values[mode] = False
    sources = {
        f"{EDIT_MODES_KEY}.{mode}": (
            CAPABILITY_SOURCE_UNSPECIFIED
            if mode in unknown
            else "VERIFIED"
            if mode in verified
            else source
        )
        for mode in values
    }
    return {
        EDIT_MODES_KEY: {
            mode: {"supported": supported, "source": sources[f"{EDIT_MODES_KEY}.{mode}"]}
            for mode, supported in values.items()
        },
        CAPABILITY_SOURCE_KEY: dict(sources),
    }


def declare_resolution_tiers(
    tiers: dict[str, bool], *, source: str = "DECLARED"
) -> dict[str, Any]:
    """Structured ``resolution_tiers`` fragment; unknown tiers stay absent."""

    if source not in CAPABILITY_SOURCES:
        raise ValueError(f"未知能力来源：{source}")
    normalized = {
        str(name): {"supported": bool(supported), "source": source}
        for name, supported in tiers.items()
        if isinstance(name, str)
    }
    return {
        RESOLUTION_TIERS_KEY: normalized,
        CAPABILITY_SOURCE_KEY: {
            f"{RESOLUTION_TIERS_KEY}.{name}": source for name in normalized
        },
    }


def media_placeholders() -> dict[str, Any]:
    """Explicit UNKNOWN slots for video/audio so serialization is honest."""

    return {
        MEDIA_KEY: {
            slot: {"supported": False, "source": CAPABILITY_SOURCE_UNSPECIFIED}
            for slot in MEDIA_SLOTS
        },
    }


def merge_capability_fragments(*fragments: dict[str, Any]) -> dict[str, Any]:
    """Combine declaration fragments without losing provenance maps.

    A plain ``{**a, **b}`` would let the last fragment's ``capability_sources``
    (or ``region_capability_sources``) silently replace every earlier entry;
    this merge unions both provenance maps while other keys follow normal
    last-wins dict semantics.
    """

    merged: dict[str, Any] = {}
    for fragment in fragments:
        for key, value in (fragment or {}).items():
            if key in (CAPABILITY_SOURCE_KEY, REGION_CAPABILITY_SOURCE_KEY):
                existing = merged.get(key)
                if isinstance(existing, dict) and isinstance(value, dict):
                    merged[key] = {**existing, **value}
                elif isinstance(value, dict):
                    merged[key] = dict(value)
                else:
                    merged[key] = value
            else:
                merged[key] = value
    return merged

# Declared region-edit surfaces (§7/§8 M2): the routing layer and UI use these
# to keep explicit mask, instruction-only and whole-image-reference editing
# apart instead of treating every "edit" as a local edit.
SURFACE_EXPLICIT_MASK: Final = "EXPLICIT_MASK"
SURFACE_INSTRUCTION_REGION: Final = "INSTRUCTION_REGION"
SURFACE_WHOLE_IMAGE_REFERENCE: Final = "WHOLE_IMAGE_REFERENCE"
SURFACE_UNSUPPORTED: Final = "UNSUPPORTED"

REGION_EDIT_SURFACE_LABELS: Final[dict[str, str]] = {
    SURFACE_EXPLICIT_MASK: "显式 mask 局部编辑",
    SURFACE_INSTRUCTION_REGION: "仅 instruction 区域编辑（不支持选区 mask）",
    SURFACE_WHOLE_IMAGE_REFERENCE: "仅整图参考编辑（不保证区域外不变）",
    SURFACE_UNSUPPORTED: "未声明任何区域编辑能力（按不支持处理）",
}


def region_capability_enabled(capabilities: dict[str, Any] | None, key: str) -> bool:
    """Fail-closed read of one capability bit: absent/falsy is unsupported."""

    if key not in REGION_CAPABILITY_KEYS:
        raise ValueError(f"未知区域编辑能力位：{key}")
    return bool((capabilities or {}).get(key))


def region_capability_source(capabilities: dict[str, Any] | None, key: str) -> str:
    """Readable provenance of one bit; missing/unknown stays UNSPECIFIED."""

    if key not in REGION_CAPABILITY_KEYS:
        raise ValueError(f"未知区域编辑能力位：{key}")
    sources = (capabilities or {}).get(REGION_CAPABILITY_SOURCE_KEY)
    if isinstance(sources, dict):
        source = sources.get(key)
        if source in REGION_CAPABILITY_SOURCES:
            return str(source)
    return REGION_CAPABILITY_SOURCE_UNSPECIFIED


def region_capability_summary(
    capabilities: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Per-bit ``{supported, source}`` declaration surface for the API/tests."""

    return {
        key: {
            "supported": region_capability_enabled(capabilities, key),
            "source": region_capability_source(capabilities, key),
        }
        for key in REGION_CAPABILITY_KEYS
    }


def model_region_edit_surface(model: Any) -> str:
    """Classify the declared edit surface of one catalog model.

    ``EXPLICIT_MASK`` beats ``INSTRUCTION_REGION`` beats
    ``WHOLE_IMAGE_REFERENCE``; a model without any declared bit stays
    ``UNSUPPORTED`` and can never enter a region path.
    """

    capabilities = getattr(model, "capabilities", None)
    if region_capability_enabled(capabilities, ACCEPTS_EXPLICIT_MASK):
        return SURFACE_EXPLICIT_MASK
    if region_capability_enabled(capabilities, SUPPORTS_INSTRUCTION_REGION_EDIT):
        return SURFACE_INSTRUCTION_REGION
    if region_capability_enabled(capabilities, WHOLE_IMAGE_REFERENCE_ONLY):
        return SURFACE_WHOLE_IMAGE_REFERENCE
    return SURFACE_UNSUPPORTED


def model_supports_explicit_mask(model: Any) -> bool:
    """Fail-closed mask capability bit (V02-42B audit §7, V02-44B §7.2)."""

    return region_capability_enabled(
        getattr(model, "capabilities", None), ACCEPTS_EXPLICIT_MASK
    )


def declare_region_capabilities(
    *,
    accepts_explicit_mask: bool = False,
    supports_instruction_region_edit: bool = False,
    preserves_outside_region: bool = False,
    whole_image_reference_only: bool = False,
    source: str = "DECLARED",
) -> dict[str, Any]:
    """Honest capability fragment for one catalog model row (§7.2).

    Presets declare only what the adapter surface actually expresses, and
    every declared bit carries its provenance so UNKNOWN can stay UNKNOWN.
    """

    if source not in REGION_CAPABILITY_SOURCES:
        raise ValueError(f"未知能力来源：{source}")
    bits = {
        ACCEPTS_EXPLICIT_MASK: bool(accepts_explicit_mask),
        SUPPORTS_INSTRUCTION_REGION_EDIT: bool(supports_instruction_region_edit),
        PRESERVES_OUTSIDE_REGION: bool(preserves_outside_region),
        WHOLE_IMAGE_REFERENCE_ONLY: bool(whole_image_reference_only),
    }
    return {
        **bits,
        REGION_CAPABILITY_SOURCE_KEY: {key: source for key in REGION_CAPABILITY_KEYS},
    }


def whole_image_reference_edit_capabilities() -> dict[str, Any]:
    """Declaration for adapters whose only edit surface is whole-image
    reference editing (matrix §1.2/§6): no native mask parameter, no
    instruction-only region edit, no outside-region preservation guarantee.

    Emits both the legacy boolean bits and the structured ``edit_modes`` map
    (P0-1): the adapter-request-surface modes are DECLARED from code evidence,
    while ``in_image_text_edit`` stays UNSPECIFIED — provider text-in-image
    editing is not verifiable from the adapter surface.
    """

    return merge_capability_fragments(
        declare_region_capabilities(whole_image_reference_only=True),
        declare_edit_modes(
            whole_image_reference=True,
            unspecified=(EDIT_MODE_IN_IMAGE_TEXT,),
        ),
        media_placeholders(),
    )


MAX_DECLARED_REFERENCE_IMAGES: Final = 100


def capability_reference_limit(capabilities: dict[str, Any] | None) -> int | None:
    """Read ``max_reference_images`` as a bounded non-negative integer.

    Returns ``None`` when the bit is absent or malformed so callers keep the
    documented undeclared semantics instead of crashing: one bad admin write
    must not 500 the model catalog, kill worker binding or break vertex binds.
    Booleans, negative, fractional and oversized values read as undeclared.
    """

    value = (capabilities or {}).get("max_reference_images")
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if (
        not number.is_finite()
        or number < 0
        or number > MAX_DECLARED_REFERENCE_IMAGES
        or number != number.to_integral_value()
    ):
        return None
    return int(number)
