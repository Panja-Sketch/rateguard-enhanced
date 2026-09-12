from app.engines.diff import SemanticDiffResult
from app.engines.oracle import PremiumOracle, RiskInput
from app.engines.portfolio.models import SyntheticPolicy
from app.engines.reconciliation.comparator import ReconciliationEngine
from app.engines.reconciliation.models import PremiumReconciliationResult
from app.engines.target import IPIRRatingTarget
from app.engines.testing.models import PricingTestScenario
from app.ipir.package import IPIRPackage

_oracle = PremiumOracle()
_target_engine = IPIRRatingTarget()
_reconciler = ReconciliationEngine()


def reprice_policy(
    policy: SyntheticPolicy,
    canonical_pkg: IPIRPackage,
    target_pkg: IPIRPackage,
    diff_result: SemanticDiffResult,
) -> PremiumReconciliationResult:
    """Evaluates canonical Oracle and target engine for a policy and returns reconciliation."""
    risk = RiskInput(
        values={
            "territory": policy.territory,
            "roof_age": policy.roof_age,
            "deductible": policy.deductible,
            "protection_class": policy.protection_class,
            "construction_type": policy.construction_type,
            "dwelling_limit": policy.dwelling_limit,
            "multi_policy": policy.multi_policy,
            "claims_free": policy.claims_free,
            "claims_free_years": policy.claims_free_years,
        }
    )

    scenario = PricingTestScenario(
        id=policy.policy_id,
        name=f"Policy {policy.policy_id}",
        risk_values=risk.values,
        effective_date=policy.effective_date,
        transaction_type=policy.transaction_type,
        purpose="Portfolio policy repricing execution",
    )

    exp_res = _oracle.calculate(
        package=canonical_pkg,
        risk=risk,
        effective_date=policy.effective_date,
        transaction_type=policy.transaction_type,
    )

    act_res = _target_engine.quote(
        package=target_pkg,
        risk=risk,
        effective_date=policy.effective_date,
        transaction_type=policy.transaction_type,
    )

    return _reconciler.reconcile(scenario, exp_res, act_res, diff_result, canonical_pkg)
