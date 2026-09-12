# Risk-Directed Test Generation Specification

## 1. Overview & Purpose
The **Risk-Directed Test Generator** deterministically derives, scores, and optimizes test scenarios to exercise semantic differences, boundary limits, interaction paths, temporal discrepancy windows, and policy minimum constraints discovered between rate plan representations.

---

## 2. Generation Strategies
1. **Range Boundary Generation:** Generates boundary values (`just_below`, `lower_bound`, `interior`, `upper_bound`, `just_above`) for numeric range predicates.
2. **Categorical Testing:** Generates exact categorical inputs matching changed table cells alongside baseline control categories.
3. **Temporal Window Testing:** Generates test effective dates spanning before, start, inside, end, and after effective-date drift windows.
4. **Interaction Testing:** Combines changed dimensions (e.g. `roof_age 24 + T17 + FRAME`) to exercise multi-dimensional interaction adjustments.
5. **Minimum Premium Exposure:** Derives low-risk attribute combinations to force pre-constraint premium below policy minimum limits.

---

## 3. Scoring & Greedy Optimization
Each candidate test scenario is scored deterministically based on changed node coverage (+30), boundary coverage (+20), interaction coverage (+20), temporal discrepancy coverage (+15), minimum exposure (+15), and baseline control value (+10).

A greedy optimizer selects a minimal, high-value scenario subset maximizing semantic difference coverage while reducing candidate redundancy.

