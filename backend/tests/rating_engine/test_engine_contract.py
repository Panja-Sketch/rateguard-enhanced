"""Contract behaviour of the black-box engine, including provenance/capabilities,
and parity with RateGuard's independent oracle (RateGuard's side may import both)."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_data_dir
from app.engines.oracle.evaluator import evaluate_package
from app.engines.oracle.models import RiskInput
from app.ipir.enums import TransactionType
from app.ipir.v0_2.compat import lower_to_v0_1
from app.ipir.v0_2.package import IPIRPackageV2
from rating_engine.main import app


def _body(engine_version="canonical-v1", roof_age=25, **over):
    body = {
        "request_id": "req-1",
        "engine_version": engine_version,
        "product_id": "az_ho3",
        "effective_date": "2026-10-01",
        "transaction_type": "NEW_BUSINESS",
        "inputs": {"roof_age": roof_age, "dwelling_limit": "300000.00"},
    }
    body.update(over)
    return body


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _oracle_premium(kind: str, roof_age: int) -> Decimal:
    path = get_data_dir() / "implementations" / "v0_2" / kind / "AZ_HO3_GOLDEN_ipir.json"
    package = lower_to_v0_1(IPIRPackageV2.model_validate_json(path.read_text(encoding="utf-8")))
    result = evaluate_package(
        package=package,
        risk=RiskInput(values={"roof_age": roof_age, "dwelling_limit": "300000.00"}),
        effective_date=date(2026, 10, 1),
        transaction_type=TransactionType.NEW_BUSINESS,
    )
    return result.final_premium


@pytest.mark.parametrize("engine_version,kind", [("canonical-v1", "canonical"), ("defective-v1", "defective")])
@pytest.mark.parametrize("roof_age", list(range(0, 41)) + [60, 80, 120])
def test_engine_matches_independent_oracle_for_each_version(client, engine_version, kind, roof_age):
    response = client.post("/quote", json=_body(engine_version, roof_age))
    assert response.status_code == 200
    assert Decimal(response.json()["outputs"]["final_premium"]) == _oracle_premium(kind, roof_age)


def test_canonical_is_intended_result_and_defect_is_only_at_roof_age_21_plus(client):
    def premium(version, age):
        return client.post("/quote", json=_body(version, age)).json()["outputs"]["final_premium"]

    assert premium("canonical-v1", 25) == "700.00" and premium("defective-v1", 25) == "655.00"
    for age in (20, 21, 22):
        assert premium("canonical-v1", age) == ("600.00" if age == 20 else "700.00")
        assert premium("defective-v1", age) == ("600.00" if age == 20 else "655.00")
    for age in (0, 10, 11, 19):
        assert premium("canonical-v1", age) == premium("defective-v1", age)


def test_capabilities_advertise_versions_and_non_sensitive_provenance(client, monkeypatch):
    monkeypatch.setenv("ENGINE_SOURCE_COMMIT", "a" * 40)
    monkeypatch.setenv("K_REVISION", "rateguard-rating-engine-00099-xyz")
    body = client.get("/capabilities").json()
    assert body["engine_versions"] == ["canonical-v1", "defective-v1"]
    assert body["quote_batch"]["schema_version"] == "quote-batch-v1"
    prov = body["provenance"]
    assert prov["source_commit"] == "a" * 40
    assert prov["deployment_revision"] == "rateguard-rating-engine-00099-xyz"
    assert prov["implementation_version"]
    assert set(prov) == {
        "service", "implementation_version", "source_commit", "image_digest", "deployment_revision",
    }
    for word in ("black-box", "Demo Insurer"):
        assert word in prov["service"]
    for forbidden in ("guidewire", "duck creek"):
        assert forbidden not in prov["service"].lower()


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "authorization", "roles", "auth"])
def test_engine_rejects_caller_supplied_identity_or_authorization_fields(client, field):
    assert client.post("/quote", json=_body(**{field: "x"})).status_code == 422


def test_effective_date_before_plan_start_is_rejected(client):
    assert client.post("/quote", json=_body(effective_date="2026-09-30")).status_code == 422


def test_policy_change_transaction_is_rejected(client):
    assert client.post("/quote", json=_body(transaction_type="POLICY_CHANGE")).status_code == 422


@pytest.mark.parametrize("bad", [-1, "abc", True, 2.5, None])
def test_invalid_roof_age_is_rejected_without_echoing_the_value(client, bad):
    response = client.post("/quote", json=_body(roof_age=bad))
    assert response.status_code == 422
    assert "abc" not in response.text
