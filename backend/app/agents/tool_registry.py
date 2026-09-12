"""Controlled tool registry for the Gemini-driven investigation supervisor.

This is a classification and validation surface only — every tool listed here
is a thin, already-existing deterministic capability (adapters/engines under
`app.adapters` / `app.engines` / `app.services`). Nothing here reimplements an
engine; it exists so the supervisor can:

  1. Advertise a fixed, named tool vocabulary to Gemini in prompts.
  2. Reject any `requested_tool` a Gemini response names that isn't in this
     registry, *before* anything executes.
  3. Enforce which tools Gemini is even allowed to request (mandatory stages
     always run regardless of what Gemini says; conditional tools require an
     explicit, validated Gemini request or a deterministic policy override).
"""

from enum import StrEnum


class ToolClass(StrEnum):
    """How much discretion Gemini has over invoking a given tool."""

    MANDATORY_DETERMINISTIC = "MANDATORY_DETERMINISTIC"
    GEMINI_SELECTABLE = "GEMINI_SELECTABLE"
    CONDITIONAL_IMPACT = "CONDITIONAL_IMPACT"


TOOL_REGISTRY: dict[str, ToolClass] = {
    # Mandatory deterministic stages — Gemini cannot skip, veto, or replace these.
    "extract_source": ToolClass.MANDATORY_DETERMINISTIC,
    "compile_ipir": ToolClass.MANDATORY_DETERMINISTIC,
    "validate_ipir_schema": ToolClass.MANDATORY_DETERMINISTIC,
    "compare_ipir": ToolClass.MANDATORY_DETERMINISTIC,
    "analyze_dependencies": ToolClass.MANDATORY_DETERMINISTIC,
    "generate_candidate_tests": ToolClass.MANDATORY_DETERMINISTIC,
    "premium_oracle": ToolClass.MANDATORY_DETERMINISTIC,
    "execute_target": ToolClass.MANDATORY_DETERMINISTIC,
    "reconcile_trace": ToolClass.MANDATORY_DETERMINISTIC,
    # Gemini-selectable investigative tools — Gemini chooses *which* deterministic
    # candidates to act on, never whether the underlying capability runs.
    "prioritize_differences": ToolClass.GEMINI_SELECTABLE,
    "select_boundary_tests": ToolClass.GEMINI_SELECTABLE,
    "request_additional_probe": ToolClass.GEMINI_SELECTABLE,
    "choose_extraction_strategy": ToolClass.GEMINI_SELECTABLE,
    # Conditionally allowed impact/remediation tools — gated by deterministic
    # guardrails (e.g. portfolio always runs when a mismatch exists regardless
    # of what Gemini requests; remediation never touches the original package).
    "query_portfolio": ToolClass.CONDITIONAL_IMPACT,
    "propose_remediation": ToolClass.CONDITIONAL_IMPACT,
    "select_revalidation_tests": ToolClass.CONDITIONAL_IMPACT,
    "revalidate_remediation": ToolClass.CONDITIONAL_IMPACT,
}


def is_known_tool(tool_name: str) -> bool:
    return tool_name in TOOL_REGISTRY


def tool_class(tool_name: str) -> ToolClass | None:
    return TOOL_REGISTRY.get(tool_name)
