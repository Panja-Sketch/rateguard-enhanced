import pytest

from rating_engine.startup_selftest import StartupSelfTestFailedError, run_startup_selftest


def test_startup_selftest_proves_canonical_and_defective_golden_values():
    results = run_startup_selftest()
    assert results == {"canonical-v1": "700.00", "defective-v1": "655.00"}


def test_startup_selftest_fails_closed_on_mismatch(monkeypatch):
    from rating_engine import startup_selftest

    monkeypatch.setitem(startup_selftest.GOLDEN_EXPECTED, "canonical-v1", "999.99")
    with pytest.raises(StartupSelfTestFailedError, match="canonical-v1"):
        run_startup_selftest()
