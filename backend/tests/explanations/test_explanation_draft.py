from datetime import date
from decimal import Decimal

from app.explanations import (
    build_explanation_draft,
    build_explanation_facts,
    deterministic_fallback_draft,
    extract_value_tokens,
    validate_draft_text,
)


def _facts():
    return build_explanation_facts(
        case_id="case-025",
        prior_premium=Decimal("686.00"),
        new_premium=Decimal("700.00"),
        factor_label="roof age band",
        effective_date=date(2026, 10, 15),
    )


def test_facts_hash_is_deterministic_and_stable():
    a = _facts()
    b = _facts()
    assert a.facts_sha256 == b.facts_sha256
    assert len(a.facts_sha256) == 64


def test_facts_net_change_is_signed():
    facts = _facts()
    assert facts.net_change == "14.00"
    assert facts.approved_factors[0].effect == "14.00"


def test_extract_value_tokens_finds_amounts_and_dates():
    text = "Premium moved from $700.00 to $655.00 on 2026-10-15, a change of -45.00."
    tokens = extract_value_tokens(text)
    assert "700.00" in tokens
    assert "655.00" in tokens
    assert "-45.00" in tokens
    assert "2026-10-15" in tokens


def test_validate_draft_text_accepts_only_facts_values():
    facts = _facts()
    good = f"Effective {facts.effective_date.isoformat()}, your premium changed from {facts.prior_premium} to {facts.new_premium} (net {facts.net_change}) due to {facts.approved_factors[0].label}."
    valid, rejected = validate_draft_text(good, facts)
    assert valid is True
    assert rejected == []


def test_validate_draft_text_rejects_invented_number():
    facts = _facts()
    bad = "Your premium increased by 999.00 due to a new surcharge."
    valid, rejected = validate_draft_text(bad, facts)
    assert valid is False
    assert "999.00" in rejected


def test_validate_draft_text_rejects_prohibited_phrases():
    facts = _facts()
    bad = f"This change is fully compliant and you are eligible for a discount, effective {facts.effective_date.isoformat()}."
    valid, rejected = validate_draft_text(bad, facts)
    assert valid is False
    assert any("prohibited_phrase" in r for r in rejected)


def test_deterministic_fallback_is_always_valid():
    facts = _facts()
    draft = deterministic_fallback_draft(facts)
    valid, rejected = validate_draft_text(draft.draft_text, facts)
    assert valid is True
    assert rejected == []
    assert draft.source == "deterministic_fallback"
    assert draft.validated is True


def test_build_explanation_draft_falls_back_on_unsupported_gemini_text():
    facts = _facts()
    bad_gemini_text = "Your premium rose because of your credit score, a change of 999.00."
    draft = build_explanation_draft(facts, bad_gemini_text)
    assert draft.source == "deterministic_fallback"
    assert draft.rejected_tokens  # non-empty: records what was rejected


def test_build_explanation_draft_accepts_valid_gemini_text():
    facts = _facts()
    good_gemini_text = (
        f"Your premium changed from {facts.prior_premium} to {facts.new_premium} "
        f"(net change {facts.net_change}) effective {facts.effective_date.isoformat()}, "
        f"due to {facts.approved_factors[0].label}."
    )
    draft = build_explanation_draft(facts, good_gemini_text)
    assert draft.source == "gemini"
    assert draft.validated is True
    assert draft.rejected_tokens == []


def test_build_explanation_draft_falls_back_when_gemini_text_is_none():
    facts = _facts()
    draft = build_explanation_draft(facts, None)
    assert draft.source == "deterministic_fallback"
