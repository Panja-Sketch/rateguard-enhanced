from datetime import date

from app.engines.oracle.evaluator import evaluate_package
from app.engines.oracle.models import OracleResult, RiskInput
from app.ipir.enums import TransactionType
from app.ipir.package import IPIRPackage


class PremiumOracle:
    """Authoritative, deterministic premium calculation oracle for IPIR 0.1 rate plans."""

    def calculate(
        self,
        package: IPIRPackage,
        risk: RiskInput,
        effective_date: date,
        transaction_type: TransactionType = TransactionType.NEW_BUSINESS,
        strict_inputs: bool = False,
    ) -> OracleResult:
        """Calculates expected policy premium using exact Decimal arithmetic and returns full trace.

        Args:
            package: Canonical IPIRPackage rate plan.
            risk: RiskInput instance containing rating variables.
            effective_date: Date for rate calculation.
            transaction_type: Policy transaction context.
            strict_inputs: If True, rejects unmodeled extra inputs.

        Returns:
            OracleResult containing final premium, resolved context values, and step-by-step trace.
        """
        return evaluate_package(
            package=package,
            risk=risk,
            effective_date=effective_date,
            transaction_type=transaction_type,
            strict_inputs=strict_inputs,
        )
