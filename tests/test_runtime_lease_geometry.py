"""Runtime lease/timeout cross-field geometry regressions.

The boot Settings validator rejects job_lease_seconds > job_timeout_seconds,
but the runtime override path mutated the live Settings directly: a PATCH
pushing the timeout below the effective lease (boot default OR stored runtime
value) silently recreated the wedged ACTIVE-but-unreclaimable geometry the
boot validator forbids. The guard must judge the MERGED result — either side
may come from the stored row, the payload, or the boot fallback — while rows
poisoned before the guard keep reads alive and stay repairable.
"""

import pytest

from app.config import Settings, get_settings
from app.models import AppSetting
from app.services.runtime_settings import apply_runtime_overrides

CROSS_FIELD_MESSAGE = "job_lease_seconds 不得大于 job_timeout_seconds"


@pytest.fixture
def pinned_settings(monkeypatch):
    """Pin the process Settings pair so assertions are order-independent.

    apply_runtime_overrides mutates the singleton in place; monkeypatch
    restores the pre-test values on teardown so later test files in the same
    session are unaffected by these PATCHes.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "job_timeout_seconds", 900)
    monkeypatch.setattr(settings, "job_lease_seconds", 120)
    return settings


# ------------------------------------------------- timeout pushed below lease


def test_patch_timeout_below_boot_lease_is_422(client, db_session, pinned_settings):
    response = client.patch(
        "/api/v1/settings/runtime", json={"version": 1, "job_timeout_seconds": 60}
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert CROSS_FIELD_MESSAGE in detail
    # Actionable: the rejected effective pair is spelled out.
    assert "60" in detail and "120" in detail
    # Nothing persisted and the live process keeps the boot geometry.
    assert db_session.get(AppSetting, "runtime") is None
    assert pinned_settings.job_timeout_seconds == 900
    assert pinned_settings.job_lease_seconds == 120


def test_patch_timeout_below_stored_runtime_lease_is_422(
    client, db_session, pinned_settings
):
    # The effective lease comes from the stored runtime row, not the payload.
    first = client.patch(
        "/api/v1/settings/runtime", json={"version": 1, "job_lease_seconds": 300}
    )
    assert first.status_code == 200, first.text

    second = client.patch(
        "/api/v1/settings/runtime", json={"version": 2, "job_timeout_seconds": 120}
    )
    assert second.status_code == 422
    assert CROSS_FIELD_MESSAGE in second.json()["detail"]

    # The stored row keeps the last valid pair and its version claim.
    row = db_session.get(AppSetting, "runtime")
    assert row.value == {"job_lease_seconds": 300}
    assert row.version == 2


# ------------------------------------------------- lease pushed above timeout


def test_patch_lease_above_effective_timeout_is_422(client, db_session, pinned_settings):
    # Above the boot timeout...
    response = client.patch(
        "/api/v1/settings/runtime", json={"version": 1, "job_lease_seconds": 1200}
    )
    assert response.status_code == 422
    assert CROSS_FIELD_MESSAGE in response.json()["detail"]
    assert db_session.get(AppSetting, "runtime") is None

    # ...and above a stored runtime timeout.
    stored = client.patch(
        "/api/v1/settings/runtime", json={"version": 1, "job_timeout_seconds": 300}
    )
    assert stored.status_code == 200, stored.text
    above_stored = client.patch(
        "/api/v1/settings/runtime", json={"version": 2, "job_lease_seconds": 600}
    )
    assert above_stored.status_code == 422
    assert CROSS_FIELD_MESSAGE in above_stored.json()["detail"]


# ------------------------------------------------------------ legal PATCHes


def test_legal_runtime_patches_still_succeed(client, pinned_settings):
    both = client.patch(
        "/api/v1/settings/runtime",
        json={"version": 1, "job_timeout_seconds": 600, "job_lease_seconds": 300},
    )
    assert both.status_code == 200, both.text
    body = both.json()
    assert body["job_timeout_seconds"] == 600
    assert body["job_lease_seconds"] == 300
    # The applied pair takes effect in the live process without a restart.
    assert pinned_settings.job_timeout_seconds == 600
    assert pinned_settings.job_lease_seconds == 300

    # Single-side updates within bounds keep working; lease == timeout is the
    # same boundary the boot validator allows.
    raised = client.patch(
        "/api/v1/settings/runtime",
        json={"version": body["version"], "job_lease_seconds": 600},
    )
    assert raised.status_code == 200, raised.text
    assert raised.json()["job_lease_seconds"] == 600

    widened = client.patch(
        "/api/v1/settings/runtime",
        json={"version": raised.json()["version"], "job_timeout_seconds": 3600},
    )
    assert widened.status_code == 200, widened.text
    assert widened.json()["job_timeout_seconds"] == 3600
    assert widened.json()["job_lease_seconds"] == 600


# ------------------------------------------------------ poisoned-row tolerance


def test_poisoned_runtime_row_keeps_reads_alive_and_can_be_repaired(
    client, db_session, pinned_settings
):
    # A row written before this guard: both values pass their per-field bounds
    # (30..3600) but the pair is wedged. Like the explicit-null poison in
    # test_input_validation_hardening, reads must not break.
    db_session.add(
        AppSetting(
            key="runtime",
            value={"job_timeout_seconds": 30, "job_lease_seconds": 3600},
            version=7,
        )
    )
    db_session.commit()

    response = client.get("/api/v1/settings/runtime")
    assert response.status_code == 200, response.text
    assert response.json()["job_timeout_seconds"] == 30
    assert response.json()["job_lease_seconds"] == 3600

    # A PATCH whose merged result stays wedged is rejected with the same
    # actionable message — the merged result is judged, not the payload.
    unrelated = client.patch(
        "/api/v1/settings/runtime", json={"version": 7, "queue_mode": "LOCAL"}
    )
    assert unrelated.status_code == 422
    assert CROSS_FIELD_MESSAGE in unrelated.json()["detail"]

    # A repairing PATCH (both sides moved compatibly) succeeds and the row is
    # valid from then on.
    repaired = client.patch(
        "/api/v1/settings/runtime",
        json={"version": 7, "job_timeout_seconds": 900, "job_lease_seconds": 120},
    )
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["job_timeout_seconds"] == 900
    assert repaired.json()["job_lease_seconds"] == 120
    assert repaired.json()["version"] == 8


# --------------------------------------------------- rehydrate does not wedge


def test_poisoned_row_does_not_rehydrate_wedged_geometry(db_session):
    db_session.add(
        AppSetting(
            key="runtime",
            value={"job_timeout_seconds": 30, "job_lease_seconds": 3600},
            version=1,
        )
    )
    db_session.commit()
    settings = Settings(job_timeout_seconds=900, job_lease_seconds=120)
    apply_runtime_overrides(db_session, settings)
    # The lease/timeout pair is applied atomically: an invalid candidate keeps
    # the boot geometry instead of recreating the wedge in the live process.
    assert settings.job_timeout_seconds == 900
    assert settings.job_lease_seconds == 120


def test_pre_guard_timeout_below_boot_lease_does_not_rehydrate(db_session):
    # The realistic pre-guard poison: a runtime timeout legally stored below
    # the boot lease (per-field bounds held; the pair did not).
    db_session.add(
        AppSetting(key="runtime", value={"job_timeout_seconds": 30}, version=1)
    )
    db_session.commit()
    settings = Settings(job_timeout_seconds=900, job_lease_seconds=120)
    apply_runtime_overrides(db_session, settings)
    assert settings.job_timeout_seconds == 900
    assert settings.job_lease_seconds == 120


def test_valid_pair_rehydrates_both_sides(db_session):
    db_session.add(
        AppSetting(
            key="runtime",
            value={"job_timeout_seconds": 600, "job_lease_seconds": 300},
            version=1,
        )
    )
    db_session.commit()
    settings = Settings(job_timeout_seconds=900, job_lease_seconds=120)
    apply_runtime_overrides(db_session, settings)
    assert settings.job_timeout_seconds == 600
    assert settings.job_lease_seconds == 300
