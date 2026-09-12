"""End-to-end regression coverage for the two downloadable sample IPIR
templates advertised on the Sources page (frontend/public/samples/). These
are the exact files a judge or user downloads and uploads -- if either one
drifts out of schema, or the documented "one factor difference ->
BLOCK_DEPLOYMENT" story stops being true, this must fail loudly rather than
be discovered by a human clicking through the deployed site."""

from pathlib import Path

from app.agents.supervisor import AssuranceSupervisor
from app.ipir.package import IPIRPackage
from app.models.mission import AssuranceMission, ComparisonMode, MissionObjective, PricingSourceRef
from app.storage.memory_store import InMemoryRunStore

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "frontend" / "public" / "samples"
TEMPLATE_A = SAMPLES_DIR / "rateguard-source-template-a.json"
TEMPLATE_B = SAMPLES_DIR / "rateguard-source-template-b-drift.json"


def _load(path: Path) -> IPIRPackage:
    assert path.exists(), f"Sample template missing from repo: {path}"
    return IPIRPackage.model_validate_json(path.read_text(encoding="utf-8"))


def test_sample_templates_are_valid_ipir_schema():
    pkg_a = _load(TEMPLATE_A)
    pkg_b = _load(TEMPLATE_B)
    assert pkg_a.inputs and pkg_a.tables and pkg_a.calculations and pkg_a.outputs
    assert pkg_b.inputs and pkg_b.tables and pkg_b.calculations and pkg_b.outputs


def test_sample_templates_produce_exactly_one_factor_difference_and_block_deployment():
    pkg_a = _load(TEMPLATE_A)
    pkg_b = _load(TEMPLATE_B)

    supervisor = AssuranceSupervisor(InMemoryRunStore())
    mission = AssuranceMission(
        mission_id="MIS-SAMPLE-TEMPLATE-REGRESSION",
        name="Sample Template Drift Regression",
        mode=ComparisonMode.EQUIVALENCE,
        objective=MissionObjective(product="SAMPLE_HO3", jurisdiction="AZ", effective_period_start="2026-01-01"),
        source_a=PricingSourceRef(source_id="sample-a", source_type="FILE", name="Template A"),
        source_b=PricingSourceRef(source_id="sample-b-drift", source_type="FILE", name="Template B (drift)"),
    )

    res = supervisor.run_mission(mission, pkg_a, pkg_b)

    assert res.semantic_analysis.data.difference_count == 1, (
        "The documented sample pair must differ by exactly one roof-age factor -- "
        f"got {res.semantic_analysis.data.difference_count} differences: "
        f"{[d.title for d in res.semantic_analysis.data.differences]}"
    )
    assert res.release_decision.data.status == "BLOCK_DEPLOYMENT"


def test_sample_template_uploaded_against_itself_is_clean_pass():
    pkg_a = _load(TEMPLATE_A)
    pkg_a_again = _load(TEMPLATE_A)

    supervisor = AssuranceSupervisor(InMemoryRunStore())
    mission = AssuranceMission(
        mission_id="MIS-SAMPLE-TEMPLATE-CLEAN",
        name="Sample Template Self-Comparison",
        mode=ComparisonMode.EQUIVALENCE,
        objective=MissionObjective(product="SAMPLE_HO3", jurisdiction="AZ", effective_period_start="2026-01-01"),
        source_a=PricingSourceRef(source_id="sample-a", source_type="FILE", name="Template A"),
        source_b=PricingSourceRef(source_id="sample-a-2", source_type="FILE", name="Template A (again)"),
    )

    res = supervisor.run_mission(mission, pkg_a, pkg_a_again)

    assert res.semantic_analysis.data.difference_count == 0
    assert res.release_decision.data.status == "PASS"
