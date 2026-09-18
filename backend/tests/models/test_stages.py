import pytest
from pydantic import ValidationError

from app.models.stages import (
    MISSION_STAGE_ORDER,
    MissionStage,
    StageOutcome,
    StageRecorder,
    StageStatus,
)


def test_mission_stage_has_all_20_locked_stages():
    expected = {
        "REQUEST_VALIDATION",
        "SOURCE_A_LOAD",
        "SOURCE_B_LOAD_OR_CONNECTOR_CHECK",
        "COMPATIBILITY_GATE",
        "SEMANTIC_DIFF",
        "DEPENDENCY_IMPACT",
        "TEST_CANDIDATE_GENERATION",
        "TEST_SELECTION",
        "ORACLE_EXECUTION",
        "TARGET_EXECUTION",
        "RECONCILIATION",
        "PORTFOLIO_IMPACT",
        "PIPELINE_IMPACT",
        "COHORT_DISTRIBUTION",
        "REMEDIATION",
        "REVALIDATION",
        "EXPLANATION_FACTS",
        "EXPLANATION_DRAFT",
        "DECISION",
        "EVIDENCE_FINALIZATION",
    }
    assert {s.value for s in MissionStage} == expected
    assert len(MISSION_STAGE_ORDER) == 20


def test_stage_outcome_completed_needs_no_reason():
    outcome = StageOutcome(stage=MissionStage.SEMANTIC_DIFF, status=StageStatus.COMPLETED)
    assert outcome.reason is None


@pytest.mark.parametrize("status", [StageStatus.FAILED, StageStatus.REVIEW_REQUIRED, StageStatus.NOT_APPLICABLE])
def test_stage_outcome_requires_reason_for_non_completed(status):
    with pytest.raises(ValidationError):
        StageOutcome(stage=MissionStage.COHORT_DISTRIBUTION, status=status)
    # With a reason, it's fine.
    StageOutcome(stage=MissionStage.COHORT_DISTRIBUTION, status=status, reason="not needed for this mission")


def test_stage_recorder_tracks_missing_stages():
    recorder = StageRecorder()
    assert not recorder.all_accounted_for()
    assert len(recorder.missing_stages()) == 20

    for stage in MISSION_STAGE_ORDER:
        recorder.record(stage, StageStatus.COMPLETED)

    assert recorder.all_accounted_for()
    assert recorder.missing_stages() == []
    assert len(recorder.outcomes()) == 20


def test_stage_recorder_not_applicable_with_reason_counts_as_accounted_for():
    recorder = StageRecorder()
    for stage in MISSION_STAGE_ORDER:
        recorder.record(stage, StageStatus.NOT_APPLICABLE, reason="deferred to a later checkpoint")
    assert recorder.all_accounted_for()


def test_stage_recorder_outcome_for_returns_latest_recording():
    recorder = StageRecorder()
    recorder.record(MissionStage.DECISION, StageStatus.REVIEW_REQUIRED, reason="first pass")
    recorder.record(MissionStage.DECISION, StageStatus.COMPLETED)
    outcome = recorder.outcome_for(MissionStage.DECISION)
    assert outcome.status == StageStatus.COMPLETED
