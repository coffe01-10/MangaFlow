"""Regression: CLI failure classification keeps transient failures retryable.

The stderr substring matchers mapped bare "not available"/"denied"/
"permission"/"blocked" to non-retryable UNSUPPORTED, so a transient 5xx
("service temporarily not available") or an unrelated EACCES crash line
permanently failed the task despite contract §7.5 reserving terminal
classification for deterministic failures. Deterministic denials are
enforced in code (tool preflight, approval gate), so stderr matching now
triggers UNSUPPORTED only on the account/approval capability tokens and
falls back to retryable UPSTREAM.
"""

from app.model_adapters.antigravity_cli import _map_failure as agy_map
from app.model_adapters.grok_build_cli import _map_failure as grok_map


def test_grok_transient_5xx_wording_stays_retryable():
    code, _, retryable = grok_map("error: service temporarily not available (500)")
    assert code == "UPSTREAM"
    assert retryable is True


def test_grok_transient_permission_noise_stays_retryable():
    code, _, retryable = grok_map("EACCES: permission denied, unlink '/tmp/x'")
    assert code == "UPSTREAM"
    assert retryable is True


def test_grok_account_capability_denial_stays_terminal():
    for text in ("requires supergrok to use imagine", "imagine isn't available on this plan"):
        code, _, retryable = grok_map(text)
        assert code == "UNSUPPORTED"
        assert retryable is False


def test_grok_auth_and_quota_unchanged():
    assert grok_map("not authenticated, please sign in")[0] == "UNAUTHENTICATED"
    code, _, retryable = grok_map("quota exceeded, too many requests")
    assert code == "RATE_LIMIT" and retryable is True


def test_antigravity_transient_denied_stays_retryable():
    code, _, retryable = agy_map("IOError: permission denied while writing cache")
    assert code == "UPSTREAM"
    assert retryable is True


def test_antigravity_approval_gate_stays_terminal():
    code, _, retryable = agy_map("image tool requires approval before running")
    assert code == "UNSUPPORTED"
    assert retryable is False


def test_antigravity_auth_and_quota_unchanged():
    assert agy_map("authentication required: not logged in")[0] == "UNAUTHENTICATED"
    code, _, retryable = agy_map("resource_exhausted: rate limit hit")
    assert code == "RATE_LIMIT" and retryable is True
