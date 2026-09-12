from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ArtifactCategory(str, Enum):  # noqa: UP042
    """Categorized category for stored source/evidence artifacts."""

    SOURCE_DOCUMENT = "SOURCE_DOCUMENT"
    SOURCE_WORKBOOK = "SOURCE_WORKBOOK"
    SOURCE_JSON = "SOURCE_JSON"
    IPIR_PACKAGE = "IPIR_PACKAGE"
    ASSURANCE_REPORT = "ASSURANCE_REPORT"


class ArtifactDescriptor(BaseModel):
    """Metadata descriptor for a stored artifact in local or GCS storage."""

    artifact_id: str
    run_id: str | None = None
    category: ArtifactCategory
    filename: str
    content_type: str
    size_bytes: int
    storage_uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
