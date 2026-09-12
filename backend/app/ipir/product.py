from pydantic import BaseModel, Field, model_validator

from app.ipir.common import validate_identifier_string
from app.ipir.enums import InsuranceLine


class Jurisdiction(BaseModel):
    """Geographic scope definition for insurance products and filings."""

    country: str = "US"
    state_or_province: str | None = None


class InsuranceProduct(BaseModel):
    """Specification of an insurance product line and jurisdiction context."""

    id: str
    name: str
    line: InsuranceLine
    form: str | None = None
    jurisdiction: Jurisdiction

    @model_validator(mode="after")
    def validate_product(self) -> "InsuranceProduct":
        self.id = validate_identifier_string(self.id)
        return self


class CoverageDefinition(BaseModel):
    """Association of calculation nodes with coverage-level premiums."""

    id: str
    name: str
    calculation_refs: list[str] = Field(default_factory=list)
    output_ref: str | None = None

    @model_validator(mode="after")
    def validate_coverage(self) -> "CoverageDefinition":
        self.id = validate_identifier_string(self.id)
        self.calculation_refs = [validate_identifier_string(ref) for ref in self.calculation_refs]
        if self.output_ref is not None:
            self.output_ref = validate_identifier_string(self.output_ref)
        return self


class PricingOutput(BaseModel):
    """Declaration of target output premium fields (e.g. total_policy_premium)."""

    id: str
    name: str
    source_ref: str
    currency: str | None = "USD"
    description: str | None = None

    @model_validator(mode="after")
    def validate_output(self) -> "PricingOutput":
        self.id = validate_identifier_string(self.id)
        self.source_ref = validate_identifier_string(self.source_ref)
        return self
