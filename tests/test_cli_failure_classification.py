"""Regression: CLI failure classification keeps transient failures retryable.

The stderr substring matchers mapped bare "not available"/"denied"/
"permission"/"blocked" to non-retryable UNSUPPORTED, so a transient 5xx
("service temporarily not available") or an unrelated EACCES crash line
permanently failed the task despite contract §7.5 reserving terminal
classification for deterministic failures. Deterministic denials are
enforced in code (tool preflight, approval gate), so stderr matching now
triggers UNSUPPORTED only on the account/approval capability tokens and
falls back to retryable UPSTREAM.

#645: a FAILED result.json envelope whose error.code is agent-invented
(CONTENT_POLICY, RATE_LIMITED, missing, ...) used to collapse to the
retryable UPSTREAM, so every deterministic CLI failure was re-billed up to
max_attempts times. Unknown/unsafe codes now collapse to the terminal,
diagnostic-retaining UNKNOWN_RESULT; known codes keep their semantics.
"""

import json

import pytest

from app.model_adapters.antigravity_cli import _map_failure as agy_map
from app.model_adapters.base import ProviderAdapterError
from app.model_adapters.grok_build_cli import _map_failure as grok_map
from test_cli_executor import cli_context as _cli_executor_context


@pytest.fixture
def cli_context(tmp_path):
    """Reuse the offline controller/database harness from test_cli_executor."""
    yield from _cli_executor_context.__wrapped__(tmp_path)


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


class _FailedEnvelopeRunner:
    """Agent-style failure: clean exit, FAILED envelope written to result.json."""

    def __init__(self, code):
        self.code = code

    def run(self, *, cwd, **_kwargs):
        output = cwd.parent / "output"
        output.mkdir(parents=True, exist_ok=True)
        error = {} if self.code is None else {"code": self.code, "message": "代理报告失败"}
        (output / "result.json").write_text(
            json.dumps({"schema_version": 1, "status": "FAILED", "error": error}),
            encoding="utf-8",
        )
        from app.services.cli_executor import CLIProcessOutcome

        return CLIProcessOutcome(exit_code=0)


def _execute_failed_envelope(controller, ids, code):
    from test_cli_executor import _prepare

    run_id = _prepare(controller, ids)
    with pytest.raises(ProviderAdapterError) as caught:
        controller.execute(
            run_id, runner=_FailedEnvelopeRunner(code), argv=("fake-cli",)
        )
    return caught.value


def test_failed_envelope_unknown_code_is_terminal_unknown_result(cli_context):
    """CONTENT_POLICY is not a contract code: the collapse target must be the
    non-retryable UNKNOWN_RESULT, never the retryable UPSTREAM (#645)."""

    _settings, _factory, controller, ids = cli_context
    error = _execute_failed_envelope(controller, ids, "CONTENT_POLICY")
    assert error.code == "UNKNOWN_RESULT"
    assert error.retryable is False


def test_failed_envelope_missing_code_is_terminal_unknown_result(cli_context):
    _settings, _factory, controller, ids = cli_context
    error = _execute_failed_envelope(controller, ids, None)
    assert error.code == "UNKNOWN_RESULT"
    assert error.retryable is False


def test_failed_envelope_known_rate_limit_keeps_retryable(cli_context):
    _settings, _factory, controller, ids = cli_context
    error = _execute_failed_envelope(controller, ids, "RATE_LIMIT")
    assert error.code == "RATE_LIMIT" and error.retryable is True


def test_failed_envelope_known_timeout_keeps_retryable(cli_context):
    _settings, _factory, controller, ids = cli_context
    error = _execute_failed_envelope(controller, ids, "TIMEOUT")
    assert error.code == "TIMEOUT" and error.retryable is True


def test_failed_envelope_known_unavailable_keeps_terminal(cli_context):
    _settings, _factory, controller, ids = cli_context
    error = _execute_failed_envelope(controller, ids, "UNAVAILABLE")
    assert error.code == "UNAVAILABLE" and error.retryable is False
