"""Hand-maintained provider model sunset/EOL table (P0-2 模型目录生命周期管理).

The authoritative EOL source is an operator-maintained constant map — the
same isomorphic pattern as ``PRESETS`` in ``services/provider_presets.py``
(P1-3 frozen decision: presets never get hard-written into the data model).
Provider deprecation announcements are release-curated here; a catalog row
whose ``provider_model_id`` is listed reads as EOL/deprecated on the
effective date without waiting for a probe failure, and discovery sync
stamps ``lifecycle='EOL'`` onto the row so history stays readable while the
model stops accepting new-artifact dispatches.

Each entry names, per upstream ``provider_model_id``:

- ``sunset_date``: provider-announced retirement date (the model may still
  answer until then — treated as DEPRECATED by the read port below).
- ``eol_date``: provider-announced shutdown date (reads as EOL).
- ``source_url``: the announcement URL a maintainer verified.
- ``source_date``: when the maintainer recorded the entry.
- ``successor_hint``: optional migration hint surfaced in 409 details and
  catalog serialization (e.g. the upstream id to move to).

Known risk (accepted by the spec): the window between a provider
announcement and a release that carries the row. Probe failures stay the
fallback, but they can only mark "故障" — never "EOL" — so the health
surface's accuracy is bounded by how often this table is updated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

LIFECYCLE_ACTIVE: Final = "ACTIVE"
LIFECYCLE_DEPRECATED: Final = "DEPRECATED"
LIFECYCLE_EOL: Final = "EOL"
LIFECYCLE_STATES: Final = (LIFECYCLE_ACTIVE, LIFECYCLE_DEPRECATED, LIFECYCLE_EOL)


@dataclass(frozen=True)
class ModelSunset:
    """One hand-maintained sunset row keyed by upstream model id."""

    sunset_date: date | None = None
    eol_date: date | None = None
    source_url: str = ""
    source_date: date | None = None
    successor_hint: str | None = None


# Release-curated sunset table. Add entries only after checking the
# provider's official announcement and fill ``source_url``/``source_date``
# so the entry stays auditable. Empty by design until a real announcement
# exists — do not pre-populate speculative retirements.
MODEL_SUNSETS: Final[dict[str, ModelSunset]] = {}


def sunset_entry(provider_model_id: str | None) -> ModelSunset | None:
    """Look up the maintained sunset row for one upstream model id."""

    if not provider_model_id:
        return None
    return MODEL_SUNSETS.get(str(provider_model_id))


def sunset_state(
    provider_model_id: str | None, *, today: date | None = None
) -> str | None:
    """Lifecycle forced by the sunset table, or ``None`` when unlisted.

    ``eol_date`` reached reads ``EOL``; only ``sunset_date`` reached reads
    ``DEPRECATED``; a listed model before both dates still returns ``None``
    so callers can surface the upcoming sunset without refusing dispatch.
    """

    entry = sunset_entry(provider_model_id)
    if entry is None:
        return None
    today = today or date.today()
    if entry.eol_date is not None and today >= entry.eol_date:
        return LIFECYCLE_EOL
    if entry.sunset_date is not None and today >= entry.sunset_date:
        return LIFECYCLE_DEPRECATED
    return None


def _as_date(value: Any) -> date | None:
    """Coerce a stored ``sunset_at``/date-like value for day comparisons."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def model_lifecycle_state(model: Any, *, today: date | None = None) -> str:
    """Effective lifecycle: sunset table overrides the stored column.

    The sunset table wins over ``AIModel.lifecycle`` because a provider
    shutdown is factual regardless of when an operator flips the column; a
    DEPRECATED/EOL column value is otherwise the source of truth (an
    operator may deprecate a model the sunset table has not listed yet).
    A poisoned/missing column reads ACTIVE — fail-open on the catalog side
    is deliberate, dispatch still gates on the resolved value.
    """

    forced = sunset_state(getattr(model, "provider_model_id", None), today=today)
    if forced is not None:
        return forced
    stored = getattr(model, "lifecycle", None)
    return stored if stored in LIFECYCLE_STATES else LIFECYCLE_ACTIVE


def lifecycle_sunset_at(model: Any) -> Any:
    """Announced sunset date for serialization: stored column wins, then the
    table's known sunset/eol dates."""

    stored = getattr(model, "sunset_at", None)
    if stored is not None:
        return stored
    entry = sunset_entry(getattr(model, "provider_model_id", None))
    if entry is None:
        return None
    return entry.sunset_date or entry.eol_date


def lifecycle_successor_hint(model: Any) -> str | None:
    """Migration hint for a retired model: the sunset table's declared
    successor, if the maintainer recorded one."""

    entry = sunset_entry(getattr(model, "provider_model_id", None))
    return entry.successor_hint if entry else None
