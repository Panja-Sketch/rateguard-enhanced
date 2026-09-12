from app.engines.testing.models import PricingTestScenario


def score_scenario(scenario: PricingTestScenario) -> float:
    """Calculates a deterministic risk-directed quality score for a test scenario."""
    score = 0.0
    tags = set(scenario.tags)

    if scenario.target_node_ids:
        score += 30.0

    if "BOUNDARY" in tags:
        score += 20.0

    if "INTERACTION" in tags:
        score += 20.0

    if "TEMPORAL" in tags:
        score += 15.0
        if "TARGETED" in tags:
            score += 10.0  # Bonus for date inside discrepancy window

    if "MINIMUM_PREMIUM" in tags or "SEQUENCE_ORDER" in tags:
        score += 15.0

    if "CONTROL" in tags:
        score += 10.0

    return score
