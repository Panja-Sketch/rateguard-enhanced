from enum import StrEnum


class DifferenceType(StrEnum):
    """Types of semantic differences detected between two IPIR representations."""

    VALUE_CHANGE = "VALUE_CHANGE"
    RANGE_CHANGE = "RANGE_CHANGE"
    RULE_CHANGE = "RULE_CHANGE"
    ORDER_CHANGE = "ORDER_CHANGE"
    EFFECTIVE_DATE_CHANGE = "EFFECTIVE_DATE_CHANGE"
    ROUNDING_CHANGE = "ROUNDING_CHANGE"
    MISSING_NODE = "MISSING_NODE"
    EXTRA_NODE = "EXTRA_NODE"
    TABLE_ROW_CHANGE = "TABLE_ROW_CHANGE"
    TABLE_ROW_MISSING = "TABLE_ROW_MISSING"
    TABLE_ROW_EXTRA = "TABLE_ROW_EXTRA"
    TABLE_COVERAGE_GAP = "TABLE_COVERAGE_GAP"
    OUTPUT_CHANGE = "OUTPUT_CHANGE"
    MODIFIER_CHANGE = "MODIFIER_CHANGE"
    CONSTRAINT_CHANGE = "CONSTRAINT_CHANGE"
    FEE_CHANGE = "FEE_CHANGE"


class DifferenceSeverity(StrEnum):
    """Deterministic severity classification for semantic differences."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
