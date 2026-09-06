"""Regression: CLI failure decoding keeps both streams readable.

Chinese-localized Windows CLIs commonly write UTF-8 JSON/text to stdout
while emitting a cp936 (GBK) diagnostic to stderr. Concatenating the raw
streams before decoding makes one half mojibake under whichever encoding
wins, and failure-code keyword matching then runs over corrupted text.
Each stream is decoded independently with the same encoding chain, so
both halves stay readable for the failure classifiers.
"""

import pytest

from app.model_adapters.antigravity_cli import _decode_output as agy_decode
from app.model_adapters.antigravity_cli import _map_failure as agy_map
from app.model_adapters.codex_cli import _decode_output as codex_decode
from app.model_adapters.grok_build_cli import _decode_output as grok_decode
from app.services.cli_executor import CLIProcessOutcome


def _mixed_outcome() -> CLIProcessOutcome:
    return CLIProcessOutcome(
        exit_code=1,
        stdout="配额已用尽 quota exceeded".encode("utf-8"),
        stderr="请重新登录".encode("gbk"),
    )


@pytest.mark.parametrize("decode", [agy_decode, codex_decode, grok_decode])
def test_mixed_stream_encodings_stay_readable(decode):
    decoded = decode(_mixed_outcome())
    assert "quota exceeded" in decoded
    # The UTF-8 half must not be mojibaked by a concatenated cp936 decode.
    assert "配额已用尽" in decoded
    # The cp936 half must survive as well.
    assert "请重新登录" in decoded


def test_antigravity_failure_classification_survives_mixed_streams():
    code, _message, retryable = agy_map(agy_decode(_mixed_outcome()))
    assert code == "RATE_LIMIT"
    assert retryable is True
