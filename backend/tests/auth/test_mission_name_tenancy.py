"""A mission name stays with its tenant's mission and cannot be used to reach,
enumerate or rename another tenant's mission."""

import pytest

from tests.auth.conftest import bearer

pytestmark = pytest.mark.real_auth

NAME = "[CANDIDATE-VERIFY-babc5cb90952] controlled workbook vs versioned REST connector"


def _payload(name):
    return {
        "name": name, "mode": "RELEASE_CONFORMANCE", "product": "AZ_HO3", "jurisdiction": "Arizona",
        "source_a": {"source_id": "AZ_HO3_2026_09", "source_type": "SAMPLE_RELEASE", "name": "Intent"},
        "source_b": {"source_id": "AZ_HO3_2026_09_CLEAN", "source_type": "SAMPLE_RELEASE", "name": "Target"},
    }


def _create_as_tenant_a(api) -> str:
    res = api.post("/api/v1/missions", json=_payload(f"  {NAME}  "), headers=bearer("owner-token"))
    assert res.status_code == 202, res.text
    return res.json()["mission_id"]


def test_name_is_stored_normalised_and_visible_only_to_its_tenant(api):
    mission_id = _create_as_tenant_a(api)
    own = api.get(f"/api/v1/missions/{mission_id}", headers=bearer("viewer-token"))
    assert own.status_code == 200 and own.json()["metadata"]["name"] == NAME
    other = api.get(f"/api/v1/missions/{mission_id}", headers=bearer("b-admin-token"))
    assert other.status_code == 404 and NAME not in other.text
    listing = api.get("/api/v1/missions", headers=bearer("b-admin-token")).json()
    assert mission_id not in {m["mission_id"] for m in listing["missions"]}
    assert NAME not in str(listing)


@pytest.mark.parametrize("method", ["PUT", "PATCH", "POST"])
@pytest.mark.parametrize("token", ["owner-token", "b-admin-token"])
def test_there_is_no_route_that_renames_a_mission(api, method, token):
    mission_id = _create_as_tenant_a(api)
    res = api.request(method, f"/api/v1/missions/{mission_id}", json={"name": "renamed"}, headers=bearer(token))
    assert res.status_code in (404, 405)
    assert api.get(f"/api/v1/missions/{mission_id}", headers=bearer("viewer-token")).json()["metadata"]["name"] == NAME
