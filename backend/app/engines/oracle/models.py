from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.ipir.enums import TransactionType
from app.ipir.provenance import Provenance


class RiskInput(BaseModel):
    """Container for policy and risk input values supplied for rating."""

    values: dict[str, int | Decimal | str | bool | date]

    def get(self, key: str, default: Any = None) -> Any:
        """Helper to retrieve an input value by key."""
        return self.values.get(key, default)


class TraceStep(BaseModel):
    """Detailed audit trace step recorded during deterministic calculation."""

    sequence: int
    node_id: str
    node_type: str
    operation: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    result: Decimal | str | int | bool
    description: str | None = None
    provenance: Provenance | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PremiumTrace(BaseModel):
    """Ordered collection of trace steps representing full pricing lineage."""

    steps: list[TraceStep] = Field(default_factory=list)

    def add_step(
        self,
        node_id: str,
        node_type: str,
        operation: str,
        result: Decimal | str | int | bool,
        inputs: dict[str, Any] | None = None,
        description: str | None = None,
        provenance: Provenance | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceStep:
        """Appends a new trace step to the execution trace."""
        step_number = len(self.steps) + 1
        step = TraceStep(
            sequence=step_number,
            node_id=node_id,
            node_type=node_type,
            operation=operation,
            inputs=inputs or {},
            result=result,
            description=description,
            provenance=provenance,
            metadata=metadata or {},
        )
        self.steps.append(step)
        return step


class OracleResult(BaseModel):
    """Final output result of a Premium Oracle evaluation."""

    package_id: str
    package_version: str
    effective_date: date
    transaction_type: TransactionType
    final_output_id: str
    final_premium: Decimal
    currency: str = "USD"
    resolved_values: dict[str, Decimal | str | int | bool]
    trace: PremiumTrace
