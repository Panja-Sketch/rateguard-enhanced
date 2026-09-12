from datetime import date
from decimal import Decimal
from typing import Any

from app.engines.oracle.evaluator import evaluate_package
from app.engines.oracle.models import RiskInput
from app.ipir.package import IPIRPackage


class CalcResult:
    def __init__(self, final_premium: Decimal, trace: Any):
        self.final_premium = final_premium
        self.trace = trace


class PremiumOracleCalculator:
    """Convenience wrapper around PremiumOracle for simple dictionary risk inputs."""

    def __init__(self, package: IPIRPackage):
        self.package = package

    def calculate_policy_premium(self, risk_inputs: dict[str, Any]) -> CalcResult:
        eff_date_str = str(risk_inputs.get("effective_date", "2026-09-15"))
        try:
            eff_date = date.fromisoformat(eff_date_str)
        except Exception:
            eff_date = date(2026, 9, 15)

        clean_inputs = {}
        for k, v in risk_inputs.items():
            if k == "effective_date":
                continue
            if isinstance(v, (int, float, bool, str, Decimal)):
                clean_inputs[k] = v

        risk = RiskInput(values=clean_inputs)
        result = evaluate_package(self.package, risk, eff_date)
        return CalcResult(final_premium=result.final_premium, trace=result.trace)
