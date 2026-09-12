from abc import ABC, abstractmethod
from datetime import date

from app.engines.oracle.evaluator import evaluate_package
from app.engines.oracle.models import RiskInput
from app.engines.target.models import TargetQuoteResult
from app.ipir.enums import TransactionType
from app.ipir.package import IPIRPackage


class RatingTarget(ABC):
    """Abstract interface for external or target rating engines."""

    @abstractmethod
    def quote(
        self,
        package: IPIRPackage,
        risk: RiskInput,
        effective_date: date,
        transaction_type: TransactionType = TransactionType.NEW_BUSINESS,
    ) -> TargetQuoteResult:
        """Executes a rating quote request against the target engine."""
        pass


class IPIRRatingTarget(RatingTarget):
    """Executes target rating requests independently against a target IPIR package."""

    def __init__(self, target_id: str = "synthetic-target-rating-engine") -> None:
        self.target_id = target_id

    def quote(
        self,
        package: IPIRPackage,
        risk: RiskInput,
        effective_date: date,
        transaction_type: TransactionType = TransactionType.NEW_BUSINESS,
    ) -> TargetQuoteResult:
        """Independently evaluates the target IPIR package using exact Decimal execution."""
        oracle_res = evaluate_package(
            package=package,
            risk=risk,
            effective_date=effective_date,
            transaction_type=transaction_type,
        )

        return TargetQuoteResult(
            target_id=self.target_id,
            package_id=oracle_res.package_id,
            package_version=oracle_res.package_version,
            effective_date=oracle_res.effective_date,
            transaction_type=oracle_res.transaction_type,
            final_premium=oracle_res.final_premium,
            currency=oracle_res.currency,
            resolved_values=oracle_res.resolved_values,
            trace=oracle_res.trace,
            status="SUCCESS",
            metadata={"target_engine_type": "IPIRRatingTarget"},
        )
