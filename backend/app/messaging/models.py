from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class AssuranceJob(BaseModel):
    """Strongly typed job message schema for Pub/Sub assurance execution."""

    job_id: str
    run_id: str
    job_type: str = "ASSURANCE_MISSION_V2"
    schema_version: int = 2
    correlation_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    left_source_id: str | None = None
    right_source_id: str | None = None
    left_package_id: str | None = None
    right_package_id: str | None = None
    include_portfolio_analysis: bool = True
    requested_model: str = "gemini-3.7-flash"
    metadata: dict[str, Any] = Field(default_factory=dict)
