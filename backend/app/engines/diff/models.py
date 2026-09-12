from typing import Any

from pydantic import BaseModel, Field

from app.engines.diff.enums import DifferenceSeverity, DifferenceType
from app.ipir.provenance import Provenance


class SemanticDifference(BaseModel):
    """Represents a single granular semantic difference between two IPIR packages."""

    id: str
    difference_type: DifferenceType
    semantic_path: str
    node_id: str
    node_type: str
    left_value: Any = None
    right_value: Any = None
    severity: DifferenceSeverity
    description: str
    left_provenance: Provenance | None = None
    right_provenance: Provenance | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SemanticDiffResult(BaseModel):
    """Aggregated result of a bidirectional semantic diff comparison."""

    left_package_id: str
    right_package_id: str
    left_version: str
    right_version: str
    differences: list[SemanticDifference] = Field(default_factory=list)
    difference_count: int = 0
    severity_counts: dict[str, int] = Field(default_factory=dict)
    semantically_equal: bool = True
    comparison_metadata: dict[str, Any] = Field(default_factory=dict)
