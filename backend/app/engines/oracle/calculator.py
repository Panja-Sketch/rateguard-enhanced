from datetime import date
from decimal import Decimal
from typing import Any

from app.engines.oracle.calculation_date import (
    CalculationDateSource,
    ResolvedCalculationDate,
    resolve_calculation_date,
)
from app.engines.oracle.evaluator import evaluate_package
from app.engines.oracle.models import RiskInput
from app.ipir.enums import TransactionType
from app.ipir.package import IPIRPackage

# Keys a probe's `risk_values` may carry that are execution context, not rating
# inputs (unless the package explicitly declares an input with that id).
_CONTEXT_KEYS = ("effective_date", "transaction_type")


class CalcResult:
    def __init__(
        self,
        final_premium: Decimal,
        trace: Any,
        calculation_date: date | None = None,
        calculation_date_source: CalculationDateSource | None = None,
    ):
        self.final_premium = final_premium
        self.trace = trace
        self.calculation_date = calculation_date
        self.calculation_date_source = calculation_date_source


class PremiumOracleCalculator:
    """Convenience wrapper around PremiumOracle for simple dictionary risk inputs."""

    def __init__(self, package: IPIRPackage):
        self.package = package

    def resolve_date(
        self,
        risk_inputs: dict[str, Any] | None = None,
        *,
        effective_date: date | str | None = None,
        control_case_date: date | str | None = None,
    ) -> ResolvedCalculationDate:
        """Resolves the calculation date using the documented precedence
        (explicit > control case > package effective start) and fails closed
        with a specific `CalculationDateError` otherwise. An explicit date may
        be passed directly or carried as `risk_inputs["effective_date"]`."""
        explicit = effective_date
        if explicit is None and risk_inputs is not None:
            explicit = risk_inputs.get("effective_date")
        return resolve_calculation_date(self.package, explicit=explicit, control_case=control_case_date)

    def calculate_policy_premium(
        self,
        risk_inputs: dict[str, Any],
        *,
        effective_date: date | str | None = None,
        control_case_date: date | str | None = None,
        transaction_type: TransactionType | str | None = None,
    ) -> CalcResult:
        resolved = self.resolve_date(
            risk_inputs, effective_date=effective_date, control_case_date=control_case_date
        )

        declared_ids = {inp.id for inp in self.package.inputs}
        clean_inputs = {}
        for k, v in risk_inputs.items():
            if k in _CONTEXT_KEYS and k not in declared_ids:
                continue
            if isinstance(v, (int, float, bool, str, Decimal)):
                clean_inputs[k] = v

        txn = transaction_type if transaction_type is not None else risk_inputs.get("transaction_type")
        kwargs: dict[str, Any] = {}
        if txn is not None:
            kwargs["transaction_type"] = TransactionType(txn)

        risk = RiskInput(values=clean_inputs)
        result = evaluate_package(self.package, risk, resolved.value, **kwargs)
        return CalcResult(
            final_premium=result.final_premium,
            trace=result.trace,
            calculation_date=resolved.value,
            calculation_date_source=resolved.source,
        )
