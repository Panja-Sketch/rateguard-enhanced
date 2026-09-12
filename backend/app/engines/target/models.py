from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.engines.oracle.models import PremiumTrace
from app.ipir.enums import TransactionType


class TargetQuoteResult(BaseModel):
    """Result returned by an external/implemented rating engine target execution."""

    target_id: str
    package_id: str
    package_version: str
    effective_date: date
    transaction_type: TransactionType
    final_premium: Decimal
    currency: str = "USD"
    resolved_values: dict[str, Decimal | str | int | bool]
    trace: PremiumTrace
    status: str = "SUCCESS"
    metadata: dict[str, Any] = Field(default_factory=dict)
