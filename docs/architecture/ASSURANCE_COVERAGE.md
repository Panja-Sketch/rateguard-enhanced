# Assurance Coverage Specification

## 1. Overview & Purpose
RateGuard AI explicitly distinguishes three separate tier metrics to measure rate plan assurance:

1. **Semantic Difference Coverage (`semantic_difference_coverage_pct`):** Percentage of detected `SemanticDifference` nodes that are targeted or exercised by at least one scenario in the test plan.
2. **Behavioral Difference Coverage (`behavioral_difference_coverage_pct`):** Percentage of conceptual pricing issues where execution traces between canonical and target engines diverge (`trace_diverged == True`).
3. **Premium Reproduction Rate (`premium_difference_reproduction_rate_pct`):** Percentage of conceptual pricing issues where final monetary premiums diverge (`premium_matches == False`).

---

## 2. Masking & Isolation
A semantic difference (e.g. effective date drift or calculation sequence swap) may produce an intermediate trace divergence while being masked at final premium evaluation by constraints (such as `$575.00` minimum policy premium floor).

Risk-directed test scenarios use specific risk attribute profiles to isolate defects and prevent minimum floor masking during behavioral reproduction testing.

