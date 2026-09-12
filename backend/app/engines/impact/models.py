from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.ipir.enums import ComparisonOperator, LogicalOperator


class PredicateClause(BaseModel):
    """Single relational condition clause describing a risk attribute boundary."""

    field: str
    operator: ComparisonOperator
    value: int | Decimal | str | bool | date


class ImpactPredicate(BaseModel):
    """Structured risk attribute predicate describing policy conditions exercising a diff."""

    id: str
    clauses: list[PredicateClause] = Field(default_factory=list)
    logical_operator: LogicalOperator = LogicalOperator.AND
    temporal_start: date | None = None
    temporal_end: date | None = None
    description: str


class ImpactAnalysis(BaseModel):
    """Impact analysis result derived from semantic differences and dependency graph."""

    package_id: str
    changed_nodes: list[str] = Field(default_factory=list)
    directly_affected_nodes: list[str] = Field(default_factory=list)
    downstream_affected_nodes: list[str] = Field(default_factory=list)
    affected_outputs: list[str] = Field(default_factory=list)
    affected_coverages: list[str] = Field(default_factory=list)
    dependency_paths: list[list[str]] = Field(default_factory=list)
    candidate_risk_predicates: list[ImpactPredicate] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
