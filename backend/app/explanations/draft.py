"""Bounded consumer-explanation drafting (locked doc section 10.1 step 2):
Gemini receives only the deterministic `ExplanationFacts`; every amount/date
token its draft cites must exactly match a fact value. Any unsupported token
-- an invented number, a different date, anything not in the facts object --
rejects the draft and falls back to a deterministic template built directly
from the facts, which is always valid by construction."""

import re

from app.explanations.models import ExplanationDraft, ExplanationFacts

# Matches signed/unsigned decimal amounts ("700.00", "-45.00", "$700.00")
# and ISO-8601 dates ("2026-10-15") -- the only two token shapes a draft is
# ever permitted to state as fact (locked doc: "every amount/date... exact
# membership in the facts object").
_AMOUNT_PATTERN = re.compile(r"-?\$?\d[\d,]*\.\d{2}")
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

PROHIBITED_PHRASES = (
    "guarantee",
    "compliant",
    "legal advice",
    "you are eligible",
    "not covered",
    "climate",
    "credit score",
    "criminal",
)


def extract_value_tokens(text: str) -> list[str]:
    """Every amount/date-shaped token appearing in free text, normalized to
    the same string form ExplanationFacts.allowed_value_tokens() produces
    (no '$', no thousands separators)."""
    tokens = []
    for match in _AMOUNT_PATTERN.findall(text):
        tokens.append(match.replace("$", "").replace(",", ""))
    tokens.extend(_DATE_PATTERN.findall(text))
    return tokens


def validate_draft_text(text: str, facts: ExplanationFacts) -> tuple[bool, list[str]]:
    """Returns (valid, rejected_tokens). Rejects on any unsupported
    amount/date token, or any prohibited phrase (locked doc section 10.2:
    drafts cannot state regulatory compliance, blame, eligibility, or
    coverage advice)."""
    allowed = facts.allowed_value_tokens()
    found = extract_value_tokens(text)
    rejected = [t for t in found if t not in allowed]

    lowered = text.lower()
    for phrase in PROHIBITED_PHRASES:
        if phrase in lowered:
            rejected.append(f"prohibited_phrase:{phrase}")

    return (len(rejected) == 0, rejected)


def deterministic_fallback_draft(facts: ExplanationFacts) -> ExplanationDraft:
    """Always valid by construction -- built entirely from facts, no free text."""
    factor_lines = "; ".join(f"{f.label} ({f.effect} {facts.currency})" for f in facts.approved_factors)
    text = (
        f"For authorized review--not sent. Effective {facts.effective_date.isoformat()}, the premium "
        f"changed from {facts.prior_premium} {facts.currency} to {facts.new_premium} {facts.currency} "
        f"(net change {facts.net_change} {facts.currency}). Contributing factor(s): {factor_lines}."
    )
    return ExplanationDraft(
        draft_text=text,
        cited_fact_ids=[facts.case_id],
        source="deterministic_fallback",
        validated=True,
    )


def build_explanation_draft(facts: ExplanationFacts, gemini_draft_text: str | None) -> ExplanationDraft:
    """`gemini_draft_text` is the raw text returned by a Gemini call (or None
    when Gemini was not invoked/failed/was over budget) -- the caller (the
    supervisor, via its existing budgeted `_ask_gemini`) owns the actual
    model invocation and evidence recording; this function only validates
    and decides fallback, matching every other Gemini decision point's
    "supervisor validates, engine never trusts the model" pattern."""
    if gemini_draft_text:
        valid, rejected = validate_draft_text(gemini_draft_text, facts)
        if valid:
            return ExplanationDraft(
                draft_text=gemini_draft_text,
                cited_fact_ids=[facts.case_id],
                source="gemini",
                validated=True,
            )
        fallback = deterministic_fallback_draft(facts)
        fallback.rejected_tokens = rejected
        return fallback

    return deterministic_fallback_draft(facts)
