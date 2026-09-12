from datetime import date
from decimal import Decimal

from app.engines.impact.models import ImpactPredicate, PredicateClause
from app.engines.portfolio.models import SyntheticPolicy
from app.engines.portfolio.predicate_evaluator import matches_predicate
from app.ipir.enums import ComparisonOperator


def _policy(**overrides) -> SyntheticPolicy:
    base = dict(
        policy_id="POL-001",
        product_id="AZ_HO3",
        state="AZ",
        form="HO3",
        transaction_type="NEW_BUSINESS",
        effective_date=date(2026, 9, 15),
        territory="T01",
        roof_age=25,
        deductible=500,
        protection_class=5,
        construction_type="FRAME",
        dwelling_limit=500000,
        multi_policy=False,
        claims_free=False,
        canonical_premium=Decimal("1000.00"),
    )
    base.update(overrides)
    return SyntheticPolicy(**base)


def test_boolean_clause_true_matches_true_policy():
    """Regression test: `bool` is a subclass of `int` in Python, so a naive
    `isinstance(val, (int, Decimal))` check on a boolean clause value used to
    fall into the numeric branch and crash on `Decimal(str(True))` --
    InvalidOperation. This is the first real-world path (modifier eligibility
    predicates, see app.engines.impact.predicates) that ever produces a
    boolean-valued PredicateClause."""
    pred = ImpactPredicate(
        id="pred_bool_test",
        clauses=[PredicateClause(field="multi_policy", operator=ComparisonOperator.EQ, value=True)],
        description="multi_policy eligibility test",
    )
    assert matches_predicate(_policy(multi_policy=True), pred) is True
    assert matches_predicate(_policy(multi_policy=False), pred) is False


def test_boolean_clause_false_matches_false_policy():
    pred = ImpactPredicate(
        id="pred_bool_test_2",
        clauses=[PredicateClause(field="claims_free", operator=ComparisonOperator.EQ, value=False)],
        description="claims_free eligibility test",
    )
    assert matches_predicate(_policy(claims_free=False), pred) is True
    assert matches_predicate(_policy(claims_free=True), pred) is False


def test_empty_clauses_predicate_matches_every_policy():
    """A predicate with no clauses and no temporal window is the
    deliberate representation for a change with no risk-based eligibility
    of its own (a flat fee, a premium constraint) -- see
    app.engines.impact.predicates._global_predicate."""
    pred = ImpactPredicate(id="pred_global", clauses=[], description="global test")
    assert matches_predicate(_policy(), pred) is True
    assert matches_predicate(_policy(multi_policy=True, deductible=5000), pred) is True


def test_numeric_clause_still_works_after_boolean_exclusion():
    """Excluding bool from the numeric branch must not affect genuine
    int/Decimal comparisons (the deductible/roof_age/etc. predicates this
    function has always handled)."""
    pred = ImpactPredicate(
        id="pred_numeric",
        clauses=[PredicateClause(field="roof_age", operator=ComparisonOperator.GTE, value=21)],
        description="numeric test",
    )
    assert matches_predicate(_policy(roof_age=25), pred) is True
    assert matches_predicate(_policy(roof_age=10), pred) is False
