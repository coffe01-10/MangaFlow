"""Default pytest collection must stay inside tests/, not desktop PoC e2e."""

from pathlib import Path


def test_pytest_ini_limits_default_collection_to_tests():
    text = (Path(__file__).resolve().parents[1] / "pytest.ini").read_text(encoding="utf-8")
    assert "testpaths = tests" in text
    assert "apps/desktop-poc" in text
