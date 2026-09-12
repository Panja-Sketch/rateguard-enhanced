# Pricing Dependency Graph & Impact Predicate Specification

## 1. Overview & Purpose
The **Impact Analysis Engine** derives the downstream blast radius and candidate risk predicates resulting from semantic pricing differences discovered between rate plans.

---

## 2. Dependency Graph Architecture (`PricingDependencyGraph`)
Uses a NetworkX directed acyclic graph (DAG) constructed from an `IPIRPackage`:

- **Nodes:** Rating inputs, constants, rate tables, rules, calculation nodes, modifiers, constraints, fees, coverages, and outputs.
- **Edges:**
  - `TableDimension.input_ref` → `RateTable.id`
  - `CalculationNode.depends_on` → `CalculationNode.id`
  - `PricingModifier.applies_to` → `PricingModifier.id`
  - `PremiumConstraint.applies_to` → `PremiumConstraint.id`
  - `PricingFee.applies_to` → `PricingFee.id`
  - `CoverageDefinition` & `PricingOutput` references.

---

## 3. Impact Predicate Derivation

Converts `SemanticDifference` findings into candidate risk predicates (`ImpactPredicate` & `PredicateClause`) to determine which policies in a portfolio exercise the changed pricing logic:

1. **1D Exact Table Diff (e.g. `territory = T17`):**
   `PredicateClause(field="territory", operator=EQ, value="T17")`
2. **1D Range Table Diff (e.g. `roof_age 21..30`):**
   `PredicateClause(field="roof_age", operator=GTE, value=21)` AND `PredicateClause(field="roof_age", operator=LTE, value=30)`
3. **2D Table Diff (e.g. `T17` + `FRAME`):**
   `PredicateClause(field="territory", operator=EQ, value="T17")` AND `PredicateClause(field="construction_type", operator=EQ, value="FRAME")`
4. **Effective Date Drift:**
   Captured as structured temporal discrepancy window (`temporal_start <= policy_effective_date < temporal_end`).

