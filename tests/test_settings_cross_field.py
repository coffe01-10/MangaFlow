"""Cross-field Settings geometry and structured-block bounding regressions.

The lease/timeout pair is individually bounded but a lease longer than the
timeout wedges reclaim (the heartbeat stops renewing at the timeout while the
lease keeps the row ACTIVE); the structured-block bound must hold for EVERY
input shape, including scalars no current caller sends.
"""

import json

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.prompt_compiler import _bound_structured_block


# ---------------------------------------------------------------- lease geometry


def test_lease_longer_than_timeout_is_rejected():
    with pytest.raises(ValidationError) as raised:
        Settings(job_lease_seconds=3600)
    assert "job_lease_seconds" in str(raised.value)


def test_lease_equal_to_timeout_is_accepted():
    settings = Settings(job_lease_seconds=3600, job_timeout_seconds=3600)
    assert settings.job_lease_seconds == settings.job_timeout_seconds


def test_default_lease_geometry_is_valid():
    assert Settings(job_lease_seconds=30).job_timeout_seconds >= 30


# ------------------------------------------------- structured block scalar paths


def test_bound_structured_block_truncates_hostile_scalar():
    huge = int("9" * 3_000)
    bounded = _bound_structured_block(huge, 100)
    assert len(json.dumps(bounded, ensure_ascii=False)) <= 100
    assert isinstance(bounded, str)
    assert bounded.startswith("999")


def test_bound_structured_block_keeps_small_scalars_untouched():
    for value in (True, False, None, 7, 3.5):
        assert _bound_structured_block(value, 2_000) is value


def test_bound_structured_block_inside_dict_bounds_hostile_scalar():
    block = {"profile": {"depth": int("9" * 3_000)}}
    bounded = _bound_structured_block(block, 200)
    assert len(json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))) <= 200
