"""Premium Reconciliation and Root Cause Analysis Engine."""

from app.engines.reconciliation.comparator import ReconciliationEngine
from app.engines.reconciliation.errors import ReconciliationError
from app.engines.reconciliation.models import (
    PremiumReconciliationResult,
    PricingAssuranceRun,
    RootCauseFinding,
    TraceDifference,
)
from app.engines.reconciliation.rca import perform_root_cause_analysis
from app.engines.reconciliation.runner import PricingAssuranceRunner
from app.engines.reconciliation.trace_alignment import align_and_compare_traces
from app.ipir.package import IPIRPackage


class ReconResultWrapper:
    def __init__(self, mismatch_count: int, first_divergent_node: str | None, discovered_root_causes: list[RootCauseFinding]):
        self.mismatch_count = mismatch_count
        self.first_divergent_node = first_divergent_node
        self.discovered_root_causes = discovered_root_causes


class PricingReconciliationEngine:
    """Wrapper class for premium reconciliation and root cause analysis."""

    def reconcile_packages(self, left_pkg: IPIRPackage, right_pkg: IPIRPackage) -> ReconResultWrapper:
        run_res = PricingAssuranceRunner().run_assurance(left_pkg, right_pkg)
        discovered = run_res.discovered_root_causes
        first_node = discovered[0].node_id if discovered else None

        return ReconResultWrapper(
            mismatch_count=run_res.mismatch_count,
            first_divergent_node=first_node,
            discovered_root_causes=discovered,
        )


__all__ = [
    "PremiumReconciliationResult",
    "PricingAssuranceRun",
    "PricingAssuranceRunner",
    "PricingReconciliationEngine",
    "ReconciliationEngine",
    "ReconciliationError",
    "RootCauseFinding",
    "TraceDifference",
    "align_and_compare_traces",
    "perform_root_cause_analysis",
]
