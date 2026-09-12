"""Deterministic Premium Oracle engine for IPIR 0.1 rate plans."""

from app.engines.oracle.errors import (
    ConditionEvaluationError,
    EffectiveDateError,
    ExpressionEvaluationError,
    InputValidationError,
    OracleError,
    ReferenceResolutionError,
    TableLookupError,
    UnsupportedIPIRFeatureError,
)
from app.engines.oracle.evaluator import evaluate_package, is_active, validate_risk_inputs
from app.engines.oracle.models import OracleResult, PremiumTrace, RiskInput, TraceStep
from app.engines.oracle.oracle import PremiumOracle
from app.engines.oracle.rounding import round_decimal
from app.engines.oracle.table_lookup import lookup_table

__all__ = [
    "ConditionEvaluationError",
    "EffectiveDateError",
    "ExpressionEvaluationError",
    "InputValidationError",
    "OracleError",
    "OracleResult",
    "PremiumOracle",
    "PremiumTrace",
    "ReferenceResolutionError",
    "RiskInput",
    "TableLookupError",
    "TraceStep",
    "UnsupportedIPIRFeatureError",
    "evaluate_package",
    "is_active",
    "lookup_table",
    "round_decimal",
    "validate_risk_inputs",
]
