from enum import StrEnum


class InsuranceLine(StrEnum):
    """Supported lines of business."""

    HOMEOWNERS = "HOMEOWNERS"
    PERSONAL_AUTO = "PERSONAL_AUTO"
    COMMERCIAL_AUTO = "COMMERCIAL_AUTO"
    COMMERCIAL_PROPERTY = "COMMERCIAL_PROPERTY"
    WORKERS_COMPENSATION = "WORKERS_COMPENSATION"
    OTHER = "OTHER"


class TransactionType(StrEnum):
    """Supported policy transaction contexts."""

    NEW_BUSINESS = "NEW_BUSINESS"
    RENEWAL = "RENEWAL"
    POLICY_CHANGE = "POLICY_CHANGE"


class InputDataType(StrEnum):
    """Data types for risk and policy inputs."""

    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    MONEY = "MONEY"
    STRING = "STRING"
    BOOLEAN = "BOOLEAN"
    CATEGORY = "CATEGORY"
    DATE = "DATE"


class TableLookupType(StrEnum):
    """Rate table lookup strategy."""

    EXACT = "EXACT"
    RANGE = "RANGE"


class ModifierType(StrEnum):
    """Pricing modifier categories."""

    PERCENTAGE_DISCOUNT = "PERCENTAGE_DISCOUNT"
    PERCENTAGE_SURCHARGE = "PERCENTAGE_SURCHARGE"
    FLAT_DISCOUNT = "FLAT_DISCOUNT"
    FLAT_SURCHARGE = "FLAT_SURCHARGE"


class ConstraintType(StrEnum):
    """Premium constraint categories."""

    MINIMUM = "MINIMUM"
    MAXIMUM = "MAXIMUM"


class RoundingMode(StrEnum):
    """Supported rounding algorithms."""

    HALF_UP = "HALF_UP"
    HALF_EVEN = "HALF_EVEN"
    FLOOR = "FLOOR"
    CEILING = "CEILING"


class ExpressionOperator(StrEnum):
    """Supported mathematical calculation operators."""

    ADD = "ADD"
    SUBTRACT = "SUBTRACT"
    MULTIPLY = "MULTIPLY"
    DIVIDE = "DIVIDE"
    MIN = "MIN"
    MAX = "MAX"
    ROUND = "ROUND"


class ComparisonOperator(StrEnum):
    """Supported relational comparison operators."""

    EQ = "EQ"
    NE = "NE"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"


class LogicalOperator(StrEnum):
    """Supported boolean logic operators."""

    AND = "AND"
    OR = "OR"


class ProvenanceSourceType(StrEnum):
    """Categories of source materials for pricing logic lineage."""

    REGULATORY_FILING = "REGULATORY_FILING"
    ACTUARIAL_SPEC = "ACTUARIAL_SPEC"
    ACTUARIAL_WORKBOOK = "ACTUARIAL_WORKBOOK"
    PRICING_PLATFORM = "PRICING_PLATFORM"
    POLICY_ADMIN_SYSTEM = "POLICY_ADMIN_SYSTEM"
    RATING_ENGINE = "RATING_ENGINE"
    REST_API = "REST_API"
    LEGACY_SYSTEM = "LEGACY_SYSTEM"
    MANUAL_ENTRY = "MANUAL_ENTRY"
    OTHER = "OTHER"
