from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_data_dir, get_settings
from app.ipir.package import IPIRPackage
from app.services.scenario_service import derive_scenario_package
from app.storage import EvidenceRecord, get_run_store

router = APIRouter(prefix="/api/v1", tags=["assurance"])
_ephemeral_packages: dict[str, IPIRPackage] = {}


def resolve_demo_package(package_id: str) -> IPIRPackage:
    """Resolves allowed demo package IDs safely without exposing arbitrary filesystem paths."""
    if package_id in _ephemeral_packages:
        return _ephemeral_packages[package_id]

    data_dir = get_data_dir()
    canonical_file = (
        data_dir / "implementations" / "canonical" / "AZ_HO3_2026_09_ipir.json"
    )
    defective_file = (
        data_dir / "implementations" / "defective" / "AZ_HO3_2026_09_ipir.json"
    )

    if package_id == "AZ_HO3_2026_09":
        target_path = canonical_file
    elif package_id in ("AZ_HO3_2026_09_DEFECTIVE", "AZ_HO3_2026_09_defective"):
        target_path = defective_file
    elif package_id in (
        "AZ_HO3_2026_09_CLEAN",
        "AZ_HO3_2026_09_DEDUCTIBLE_DRIFT",
        "AZ_HO3_2026_09_EFFDATE_DRIFT",
        "AZ_HO3_2026_09_TERRITORY_DRIFT",
    ):
        if not canonical_file.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Canonical package file not found at {canonical_file}",
            )
        with open(canonical_file, encoding="utf-8") as f:
            canonical_pkg = IPIRPackage.model_validate_json(f.read())
        return derive_scenario_package(package_id, canonical_pkg)
    else:
        allowed = [
            "AZ_HO3_2026_09",
            "AZ_HO3_2026_09_DEFECTIVE",
            "AZ_HO3_2026_09_CLEAN",
            "AZ_HO3_2026_09_DEDUCTIBLE_DRIFT",
            "AZ_HO3_2026_09_EFFDATE_DRIFT",
            "AZ_HO3_2026_09_TERRITORY_DRIFT",
        ]
        err_msg = (
            f"Unsupported package_id '{package_id}'. "
            f"Allowed demo packages: {allowed}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg,
        )

    if not target_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Package file for '{package_id}' not found at {target_path}",
        )

    with open(target_path, encoding="utf-8") as f:
        return IPIRPackage.model_validate_json(f.read())


@router.get("/system/info")
def get_system_info() -> dict[str, Any]:
    """Returns runtime system and AI model information."""
    from app.agents.config import get_agent_config

    settings = get_settings()
    agent_config = get_agent_config()
    return {
        "gemini_model": agent_config.gemini_model,
        "gemini_model_display": "Gemini 3.7 Flash",
        "agent_framework": "Google GenAI SDK",
        "agent_provider": "Google Vertex AI",
        "agent_supervisor": "Google GenAI SDK Structured-Decision Supervisor",
        "ipir_version": "0.1",
        "cloud_project": settings.google_cloud_project,
        "region": settings.google_cloud_region,
        "run_store": agent_config.run_store,
        "bigquery_enabled": settings.bigquery_enabled,
    }


@router.get("/assurance/runs/{run_id}/events")
def get_assurance_run_events(run_id: str) -> dict[str, Any]:
    """Fetches ordered workflow event timeline for a run (Mission V2 'MIS-*'
    or legacy 'RUN-*' id -- this endpoint is generic over the run store and
    is what the live Mission detail page's Agent Action Timeline actually
    calls; it is not itself part of the retired legacy pipeline)."""
    store = get_run_store()
    record = store.get_run(run_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Assurance run '{run_id}' not found.",
        )

    events = store.get_events(run_id)
    return {
        "run_id": run_id,
        "event_count": len(events),
        "events": [e.model_dump(mode="json") for e in events],
    }


@router.get("/assurance/runs/{run_id}/evidence")
def get_assurance_run_evidence(run_id: str) -> dict[str, Any]:
    """Fetches evidence lineage records for a run (Mission V2 'MIS-*' or
    legacy 'RUN-*' id) -- see get_assurance_run_events docstring; this is
    also live, used by the Mission detail page's Evidence Lineage tab."""
    store = get_run_store()
    record = store.get_run(run_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Assurance run '{run_id}' not found.",
        )

    evidence_list: list[EvidenceRecord] = store.get_evidence(run_id)
    return {
        "run_id": run_id,
        "evidence_count": len(evidence_list),
        "evidence": [ev.model_dump(mode="json") for ev in evidence_list],
    }
