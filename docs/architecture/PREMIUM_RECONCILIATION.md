# Premium Reconciliation & Deterministic Root Cause Analysis Specification

## 1. Overview & Purpose
The **Reconciliation Engine** aligns expected Oracle calculation traces against target engine quote traces to detect premium variances, identify the earliest divergent node, and construct deterministic Root Cause Analysis (`RootCauseFinding`) explanations.

---

## 2. Core Architectural Principles
1. **Node-ID Trace Alignment:** Aligns execution steps by semantic `node_id` rather than raw sequence index.
2. **Upstream First Divergence:** Uses NetworkX dependency graph traversal to select upstream root causes (`roof_age_factor`) over downstream consequences (`gross_risk_premium`).
3. **Semantic Evidence Integration:** Connects first divergence to `SemanticDifference` evidence to distinguish value mismatches (`TABLE_ROW_CHANGE`), temporal drift (`EFFECTIVE_DATE_CHANGE`), or execution order changes (`ORDER_CHANGE`).
4. **Semantic vs. Behavioral Distinction:**
   - **Semantic Difference:** Structural AST mismatch between packages.
   - **Behavioral Difference:** Observable premium variance produced when a test scenario exercises a semantic difference.

