"""Bidirectional Semantic Diff Engine for IPIR packages."""

from app.engines.diff.comparator import compare_packages
from app.engines.diff.enums import DifferenceSeverity, DifferenceType
from app.engines.diff.models import SemanticDifference, SemanticDiffResult
from app.engines.diff.table_diff import compare_rate_tables
from app.ipir.package import IPIRPackage


class SemanticDiffEngine:
    """Wrapper class for IPIR package semantic comparison."""

    def compare_packages(self, left: IPIRPackage, right: IPIRPackage) -> SemanticDiffResult:
        return compare_packages(left, right)


__all__ = [
    "DifferenceSeverity",
    "DifferenceType",
    "SemanticDiffEngine",
    "SemanticDiffResult",
    "SemanticDifference",
    "compare_packages",
    "compare_rate_tables",
]
