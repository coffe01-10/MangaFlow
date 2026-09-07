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
    """

    return declare_region_capabilities(whole_image_reference_only=True)


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
