"""`POST /api/v1/sources` + `POST /api/v1/sources/{id}/compile` for the
Controlled Workbook v1 upload path (Session 4, item 2 of the CP9 plan):
proves the real `CompilationReceipt` (compiler_version, artifact_sha256,
status, control_case_results) computed by
`app.ingestion.workbook_v1.compile_workbook` is actually surfaced over HTTP
under `workbook_compilation_receipt` -- not just computed and discarded."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "data" / "samples" / "workbook_v1"


def _upload(path: Path) -> str:
    with path.open("rb") as f:
        resp = client.post(
            "/api/v1/sources",
            files={"file": (path.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["source_id"]


def test_canonical_workbook_upload_surfaces_real_compilation_receipt():
    source_id = _upload(SAMPLES_DIR / "canonical" / "AZ_HO3_GOLDEN_workbook.xlsx")
    resp = client.post(f"/api/v1/sources/{source_id}/compile")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    receipt = body["workbook_compilation_receipt"]
    assert receipt is not None
    assert receipt["status"] == "VERIFIED"
    assert receipt["compiler_version"]
    assert receipt["artifact_sha256"]
    assert receipt["control_case_results"]
    assert receipt["control_case_results"][0]["passed"] is True
    assert receipt["control_case_results"][0]["actual"] == "700.00"

    # The pre-existing generic receipt summary is unchanged/still present.
    assert body["compilation_receipt"]["product"]


def test_unsupported_workbook_upload_rejects_without_stack_trace():
    source_id = _upload(SAMPLES_DIR / "negative" / "unsupported_function.xlsx")
    resp = client.post(f"/api/v1/sources/{source_id}/compile")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Traceback" not in detail
    assert ".py" not in detail
