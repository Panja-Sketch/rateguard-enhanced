"""Consumer explanation models (locked doc section 10): a deterministic
`ExplanationFacts` object, and a bounded-language `ExplanationDraft` whose
every numeric/date value must trace back to those facts."""

import hashlib
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ApprovedFactor(BaseModel):
    label: str
    effect: str  # Decimal serialized as a string, per locked doc section 6.2


class ExplanationFacts(BaseModel):
    """Signed, deterministic facts object -- the only source of truth an
    explanation draft may cite. Never sent with real PII (locked doc 10.2)."""

    case_id: str
    currency: str = "USD"
    prior_premium: str
    new_premium: str
    net_change: str
    approved_factors: list[ApprovedFactor] = Field(default_factory=list)
    effective_date: date
    facts_sha256: str = ""

    @model_validator(mode="after")
    def _compute_hash(self) -> "ExplanationFacts":
        if not self.facts_sha256:
            canonical = self.model_dump_json(exclude={"facts_sha256"}, exclude_none=True)
            object.__setattr__(self, "facts_sha256", hashlib.sha256(canonical.encode("utf-8")).hexdigest())
        return self

    def allowed_value_tokens(self) -> set[str]:
        """Every numeric/date value a draft is permitted to cite verbatim."""
        tokens = {self.prior_premium, self.new_premium, self.net_change, self.effective_date.isoformat()}
        for factor in self.approved_factors:
            tokens.add(factor.effect)
        return tokens


class ExplanationDraft(BaseModel):
    draft_text: str
    cited_fact_ids: list[str] = Field(default_factory=list)
    source: Literal["gemini", "deterministic_fallback"]
    validated: bool
    rejected_tokens: list[str] = Field(default_factory=list)
    status: Literal["DRAFT", "APPROVED", "REJECTED"] = "DRAFT"


def build_explanation_facts(
    case_id: str,
    prior_premium: Decimal,
    new_premium: Decimal,
    factor_label: str,
    effective_date: date,
    currency: str = "USD",
) -> ExplanationFacts:
    """Deterministic facts derivation for a single reconciled mismatch case.

    Scoped to the single-root-cause-factor demonstrated case (the golden
    roof-age-factor drift): the entire net change is attributed to the one
    identified root-cause factor. A general multi-factor decomposition would
    require walking the full calculation trace attributing partial variance
    per node -- out of scope here; documented, not silently generalized.
    """
    net_change = new_premium - prior_premium
    return ExplanationFacts(
        case_id=case_id,
        currency=currency,
        prior_premium=str(prior_premium.quantize(Decimal("0.01"))),
        new_premium=str(new_premium.quantize(Decimal("0.01"))),
        net_change=str(net_change.quantize(Decimal("0.01"))),
        approved_factors=[ApprovedFactor(label=factor_label, effect=str(net_change.quantize(Decimal("0.01"))))],
        effective_date=effective_date,
    )
