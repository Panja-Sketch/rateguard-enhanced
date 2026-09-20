class OracleError(Exception):
    """Base exception for all Premium Oracle evaluation errors."""

    pass


class InputValidationError(OracleError):
    """Raised when supplied risk input fails IPIR validation rules."""

    pass


class TableLookupError(OracleError):
    """Raised when rate table lookup yields zero or multiple matching rows."""

    pass


class ExpressionEvaluationError(OracleError):
    """Raised when mathematical expression evaluation fails (e.g., division by zero)."""

    pass


class ConditionEvaluationError(OracleError):
    """Raised when boolean condition evaluation encounters invalid operands or references."""

    pass


class ReferenceResolutionError(OracleError):
    """Raised when a referenced node ID cannot be resolved from evaluation context."""

    pass


class EffectiveDateError(OracleError):
    """Raised when calculation date is outside effective periods or inactive."""

    pass


class CalculationDateError(OracleError):
    """Raised when a calculation date is missing, malformed, or outside the
    package's effective period. Carries a specific machine-readable `code`
    (INVALID_CALCULATION_DATE, MISSING_CALCULATION_DATE,
    CALCULATION_DATE_OUT_OF_PERIOD)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UnsupportedIPIRFeatureError(OracleError):
    """Raised when encountering an IPIR feature not supported by oracle 0.1."""

    pass
