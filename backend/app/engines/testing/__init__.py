"""Risk-Directed Test Generation and Optimization Engine."""

from app.engines.testing.boundary_generator import generate_range_boundary_values
from app.engines.testing.candidate_generator import generate_candidate_scenarios
from app.engines.testing.errors import TestGenerationError
from app.engines.testing.models import PricingTestPlan, PricingTestScenario
from app.engines.testing.optimizer import optimize_test_plan
from app.engines.testing.planner import PricingTestPlanner
from app.engines.testing.scorer import score_scenario

RiskDirectedTestGenerator = PricingTestPlanner

__all__ = [
    "PricingTestPlan",
    "PricingTestPlanner",
    "PricingTestScenario",
    "RiskDirectedTestGenerator",
    "TestGenerationError",
    "generate_candidate_scenarios",
    "generate_range_boundary_values",
    "optimize_test_plan",
    "score_scenario",
]
