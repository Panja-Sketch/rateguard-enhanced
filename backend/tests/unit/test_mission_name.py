"""Mission name: optional, normalised display/evidence metadata.

The Sources launcher used to hardcode a name, so a candidate verification
mission could not carry the required `[CANDIDATE-VERIFY-<sha12>] ...` marker.
The API already accepted `name`; it now normalises it. The name never affects
authorization or any decision.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import get_run_store, reset_run_store

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import verify_candidate_mission as vcm  # noqa: E402

client = TestClient(app)
SHA = "babc5cb909526f7648cb064a7260a26bf84253fd"
STATE = {"git_sha": SHA}
MARKED = f"[CANDIDATE-VERIFY-{SHA[:12]}] controlled workbook vs versioned REST connector"


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_run_store()
    yield
    reset_run_store()


def _payload(**overrides):
    payload = {
        "mode": "RELEASE_CONFORMANCE",
        "product": "AZ_HO3",
        "jurisdiction": "Arizona",
        "source_a": {"source_id": "AZ_HO3_2026_09", "source_type": "SAMPLE_RELEASE", "name": "Intent"},
        "source_b": {"source_id": "AZ_HO3_2026_09_CLEAN", "source_type": "SAMPLE_RELEASE", "name": "Target"},
    }
    payload.update(overrides)
    return payload


def _create(**overrides) -> str:
    res = client.post("/api/v1/missions", json=_payload(**overrides))
    assert res.status_code == 202, res.text
    return res.json()["mission_id"]


def _stored_name(mission_id: str) -> str:
    record = get_run_store().get_run(mission_id)
    return record.metadata["mission_object"]["name"]


def test_custom_name_persists_exactly_after_trimming():
    mission_id = _create(name=f"  {MARKED}\t ".replace("\t", " "))
    assert _stored_name(mission_id) == MARKED
    detail = client.get(f"/api/v1/missions/{mission_id}").json()
    assert detail["metadata"]["name"] == MARKED


def test_omitted_name_keeps_the_existing_api_default():
    mission_id = _create()
    assert _stored_name(mission_id) == "Pricing Release Assurance Mission"


@pytest.mark.parametrize("bad", ["", "   ", "\t\n ", None])
def test_explicit_blank_or_null_name_is_rejected(bad):
    assert client.post("/api/v1/missions", json=_payload(name=bad)).status_code == 422


def test_maximum_length_is_enforced():
    assert client.post("/api/v1/missions", json=_payload(name="x" * 121)).status_code == 422
    assert client.post("/api/v1/missions", json=_payload(name=" " + "x" * 121 + " ")).status_code == 422
    assert _stored_name(_create(name="x" * 120)) == "x" * 120
    assert len(MARKED) <= 120


@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\x00b", "a\tb", "a\x1bb", "a‮b", "a​b"])
def test_control_and_format_characters_are_rejected(bad):
    assert client.post("/api/v1/missions", json=_payload(name=bad)).status_code == 422


def test_markup_in_a_name_is_stored_verbatim_and_never_interpreted_server_side():
    name = "<img src=x onerror=alert(1)> & \"quotes\""
    assert _stored_name(_create(name=name)) == name  # escaping is the renderer's job (React)


def test_name_is_not_an_authorization_input():
    """A privileged-looking name grants nothing: the route's role check is unchanged."""
    from app.auth import Role
    from app.auth.dependencies import get_current_user
    from app.auth.models import AuthenticatedUser

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        uid="viewer", tenant_id="demo-tenant", role=Role.VIEWER
    )
    try:
        res = client.post("/api/v1/missions", json=_payload(name="ADMIN OVERRIDE [CANDIDATE-VERIFY-000000000000]"))
        assert res.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# -- the verification helper, against a REAL API-created mission ----------------------------


def _record_for(mission_id: str) -> dict:
    record = vcm._load_record(get_run_store(), mission_id)
    record.update(status="COMPLETED", decision="BLOCK_DEPLOYMENT")  # as after a worker run
    return record


def test_verification_helper_accepts_a_correctly_marked_real_mission():
    mission_id = _create(name=MARKED)
    vcm.validate_mission_record(_record_for(mission_id), STATE)


def test_verification_helper_still_refuses_an_unmarked_real_mission():
    for name in ("Assurance Mission Launched from Sources", "Pricing Release Assurance Mission"):
        mission_id = _create(name=name)
        with pytest.raises(vcm.VerificationError, match="must be marked"):
            vcm.validate_mission_record(_record_for(mission_id), STATE)


def test_verification_helper_refuses_a_marker_for_a_different_commit():
    mission_id = _create(name="[CANDIDATE-VERIFY-000000000000] controlled workbook vs versioned REST connector")
    with pytest.raises(vcm.VerificationError, match="must be marked"):
        vcm.validate_mission_record(_record_for(mission_id), STATE)
