"""Consumer explanation drafting (locked doc section 10): deterministic
facts, bounded Gemini drafting, and a numeric/date validator with a
deterministic fallback."""

from app.explanations.draft import (
    PROHIBITED_PHRASES,
    build_explanation_draft,
    deterministic_fallback_draft,
    extract_value_tokens,
    validate_draft_text,
)
from app.explanations.models import (
    ApprovedFactor,
    ExplanationDraft,
    ExplanationFacts,
    build_explanation_facts,
)

__all__ = [
    "PROHIBITED_PHRASES",
    "ApprovedFactor",
    "ExplanationDraft",
    "ExplanationFacts",
    "build_explanation_draft",
    "build_explanation_facts",
    "deterministic_fallback_draft",
    "extract_value_tokens",
    "validate_draft_text",
]
