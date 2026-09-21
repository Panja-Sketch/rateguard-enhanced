import os
import tempfile

# --- Hermetic runtime configuration -----------------------------------------
# Must run before `app.main` is imported (Settings/AgentConfig read the
# environment at import). Pin the locked values, drop any forbidden legacy
# variable a developer's shell may carry, and never read the developer's real
# backend/.env (it is not committed and may hold stale values).
for _forbidden in (
    "GEMINI_MODEL",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREBASE_ADMIN_KEY_SECRET",
    "RATEGUARD_GEMINI_LOCATION",
    "GOOGLE_CLOUD_LOCATION",
):
    os.environ.pop(_forbidden, None)
os.environ["RATEGUARD_GEMINI_MODEL"] = "gemini-3.1-flash-lite"
os.environ["VERTEX_AI_LOCATION"] = "us"
os.environ["RATEGUARD_FIREBASE_PROJECT_ID"] = "rateguard-test"
os.environ.setdefault("RATEGUARD_IMPACT_POLL_INTERVAL_SECONDS", "0.05")
os.environ["RATEGUARD_ENV_FILE"] = os.path.join(tempfile.gettempdir(), "rateguard-tests-no-such.env")

from collections.abc import Generator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import AuthenticatedUser, Role, get_current_user  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.ratelimit import RateDecision, get_rate_limiter  # noqa: E402

TEST_TENANT = "rateguard-demo"


class _UnlimitedForLegacyTests:
    """Legacy (pre-rate-limit) API tests create many missions/uploads as one user.
    They run with an always-allow limiter; the real limiter is exercised, with
    real policies, by every `real_auth` test and tests/ratelimit/*."""

    def hit(self, tenant_id, uid, policy, now=None):
        return RateDecision(True, policy.limit, 1)


@pytest.fixture(autouse=True)
def _default_test_identity(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Legacy API tests predate authentication. Unless a test opts into real
    authentication with `@pytest.mark.real_auth`, every request is treated as an
    ADMIN of the demo tenant, and pre-tenant records are explicitly assigned to
    that tenant (the documented legacy migration switch)."""
    if request.node.get_closest_marker("real_auth"):
        monkeypatch.setattr(get_settings(), "legacy_record_tenant_id", None)
        yield
        return
    monkeypatch.setattr(get_settings(), "legacy_record_tenant_id", TEST_TENANT)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        uid="test-admin", tenant_id=TEST_TENANT, role=Role.ADMIN
    )
    app.dependency_overrides[get_rate_limiter] = lambda: _UnlimitedForLegacyTests()
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_rate_limiter, None)


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """TestClient fixture for FastAPI application testing."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def firestore_emulator_db():
    """A Firestore client bound to the local emulator (FIRESTORE_EMULATOR_HOST).

    Emulator-backed tests are real transactional/query tests, not mocks. Without
    an emulator they skip, unless RATEGUARD_REQUIRE_EMULATOR=1 (used for the final
    verification run and CI), in which case a missing emulator is a failure."""
    import os

    if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
        if os.environ.get("RATEGUARD_REQUIRE_EMULATOR") == "1":
            pytest.fail("RATEGUARD_REQUIRE_EMULATOR=1 but FIRESTORE_EMULATOR_HOST is not set.")
        pytest.skip("Firestore emulator not running (set FIRESTORE_EMULATOR_HOST).")
    from google.cloud import firestore

    return firestore.Client(project="demo-rateguard-tests")


@pytest.fixture(autouse=True)
def _connector_impact_uses_in_process_rating_engine() -> Generator[None, None, None]:
    """Connector-backed impact scans in API-level tests hit the real demo rating
    engine over an in-process ASGI transport (no socket), with an in-memory
    checkpoint store."""
    import httpx

    from app.connectors.client import ConnectorClient
    from app.impact.runtime import set_connector_client_factory
    from app.impact.store import InMemoryImpactStore, set_impact_store
    from rating_engine.main import app as engine_app

    async def _no_sleep(_: float) -> None:
        return None

    # A small (1,200-row) slice of the real portfolio keeps API-level runs fast;
    # the full 50k scan has its own dedicated tests.
    import csv
    from pathlib import Path

    from app.impact import snapshot as snapshot_mod

    real = snapshot_mod._resolve_path(snapshot_mod.DEFAULT_DATASET)
    small = Path(tempfile.gettempdir()) / "rateguard-tests-portfolio-1200.csv"
    if not small.exists():
        with open(real, encoding="utf-8") as src, open(small, "w", encoding="utf-8", newline="") as dst:
            reader = csv.reader(src)
            writer = csv.writer(dst)
            for i, row in enumerate(reader):
                if i > 1200:
                    break
                writer.writerow(row)
    original_resolve = snapshot_mod._resolve_path
    snapshot_mod._resolve_path = lambda dataset: small
    snapshot_mod.clear_snapshot_cache()

    transport = httpx.ASGITransport(app=engine_app)
    set_impact_store(InMemoryImpactStore())
    set_connector_client_factory(
        lambda on_retry=None: ConnectorClient(transport=transport, sleep_fn=_no_sleep, on_retry=on_retry)
    )
    yield
    snapshot_mod._resolve_path = original_resolve
    snapshot_mod.clear_snapshot_cache()
    set_connector_client_factory(None)
    set_impact_store(None)
