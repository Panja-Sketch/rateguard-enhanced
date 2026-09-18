"""The locked 20-stage mission pipeline (docs/architecture/
RATEGUARD_LOCKED_SOURCE_OF_TRUTH.md section 7.3): "Stages may be marked
NOT_APPLICABLE with a reason. They may not silently disappear."

This module defines the stage enum and outcome model only — see
docs/implementation/DECISIONS.md (D4) for why wiring it into
`app.agents.supervisor` is deferred to a later session (CP9) while the enum
and this recording abstraction are introduced now.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MissionStage(StrEnum):
    """The 20 required stages, in the locked execution order."""

    REQUEST_VALIDATION = "REQUEST_VALIDATION"
    SOURCE_A_LOAD = "SOURCE_A_LOAD"
    SOURCE_B_LOAD_OR_CONNECTOR_CHECK = "SOURCE_B_LOAD_OR_CONNECTOR_CHECK"
    COMPATIBILITY_GATE = "COMPATIBILITY_GATE"
    SEMANTIC_DIFF = "SEMANTIC_DIFF"
    DEPENDENCY_IMPACT = "DEPENDENCY_IMPACT"
    TEST_CANDIDATE_GENERATION = "TEST_CANDIDATE_GENERATION"
    TEST_SELECTION = "TEST_SELECTION"
    ORACLE_EXECUTION = "ORACLE_EXECUTION"
    TARGET_EXECUTION = "TARGET_EXECUTION"
    RECONCILIATION = "RECONCILIATION"
    PORTFOLIO_IMPACT = "PORTFOLIO_IMPACT"
    PIPELINE_IMPACT = "PIPELINE_IMPACT"
    COHORT_DISTRIBUTION = "COHORT_DISTRIBUTION"
    REMEDIATION = "REMEDIATION"
    REVALIDATION = "REVALIDATION"
    EXPLANATION_FACTS = "EXPLANATION_FACTS"
    EXPLANATION_DRAFT = "EXPLANATION_DRAFT"
    DECISION = "DECISION"
    EVIDENCE_FINALIZATION = "EVIDENCE_FINALIZATION"


# The order a mission runs its stages in, used by `StageRecorder` to validate
# that every stage is eventually accounted for.
MISSION_STAGE_ORDER: tuple[MissionStage, ...] = tuple(MissionStage)


class StageStatus(StrEnum):
    """Every locked stage must end in exactly one of these — never omitted."""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class StageOutcome(BaseModel):
    """The recorded result of one mission stage."""

    model_config = ConfigDict(extra="forbid")

    stage: MissionStage
    status: StageStatus
    reason: str | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _require_reason_for_skip(self) -> "StageOutcome":
        if self.status in (StageStatus.NOT_APPLICABLE, StageStatus.FAILED, StageStatus.REVIEW_REQUIRED):
            if not self.reason:
                raise ValueError(
                    f"StageOutcome for '{self.stage.value}' with status '{self.status.value}' "
                    "must include a reason."
                )
        return self


class StageRecorder:
    """Accumulates `StageOutcome`s for one mission run and can confirm every
    locked stage was accounted for before a mission is allowed to reach a
    terminal decision. A thin, dependency-free bookkeeping object — deferring
    to `agents/supervisor.py`'s own event/store logging for persistence
    (CP9), not a replacement for it.
    """

    def __init__(self) -> None:
        self._outcomes: dict[MissionStage, StageOutcome] = {}

    def record(self, stage: MissionStage, status: StageStatus, reason: str | None = None) -> StageOutcome:
        outcome = StageOutcome(stage=stage, status=status, reason=reason)
        self._outcomes[stage] = outcome
        return outcome

    def outcome_for(self, stage: MissionStage) -> StageOutcome | None:
        return self._outcomes.get(stage)

    def missing_stages(self) -> list[MissionStage]:
        """Stages with no recorded outcome yet — a mission may not reach
        DECISION/EVIDENCE_FINALIZATION while this is non-empty for any
        stage other than the two currently being recorded."""
        return [stage for stage in MISSION_STAGE_ORDER if stage not in self._outcomes]

    def all_accounted_for(self) -> bool:
        return not self.missing_stages()

    def outcomes(self) -> list[StageOutcome]:
        return [self._outcomes[stage] for stage in MISSION_STAGE_ORDER if stage in self._outcomes]
