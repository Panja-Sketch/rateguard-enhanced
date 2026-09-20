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


def test_engine_fixtures_resolve_under_shared_data_dir():
    from app.core.config import get_data_dir
    from rating_engine.engines.registry import ENGINE_FIXTURE_PATHS

    root = get_data_dir()
    for path in ENGINE_FIXTURE_PATHS.values():
        assert root in path.parents and path.is_file()
