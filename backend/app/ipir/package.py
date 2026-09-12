from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ipir.calculations import CalculationNode
from app.ipir.common import EffectivePeriod, validate_identifier_string
from app.ipir.constraints import PremiumConstraint, PricingFee, RoundingRule
from app.ipir.enums import TransactionType
from app.ipir.inputs import PricingInput
from app.ipir.modifiers import PricingModifier
from app.ipir.product import CoverageDefinition, InsuranceProduct, PricingOutput
from app.ipir.provenance import Provenance
from app.ipir.rules import PricingRule
from app.ipir.tables import RateTable


class PricingConstant(BaseModel):
    """Named constant value used within rate calculation ASTs."""

    id: str
    name: str
    value: Decimal
    description: str | None = None
    effective_period: EffectivePeriod | None = None
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def validate_constant(self) -> "PricingConstant":
        self.id = validate_identifier_string(self.id)
        return self


class IPIRPackage(BaseModel):
    """Root canonical package containing an end-to-end executable IPIR pricing definition."""

    # Unknown top-level fields (e.g. a friendly `rating_tables` instead of the
    # required `tables`) must be rejected, not silently dropped -- a source
    # that "compiles" successfully while its actual pricing content is
    # discarded is exactly the false-confidence failure mode this tool
    # exists to catch. This is what makes a typo'd or hand-rolled field name
    # surface as a clear validation error instead of an empty package.
    model_config = ConfigDict(extra="forbid")

    ipir_version: str = "0.1"
    id: str
    name: str
    version: str = "1.0.0"
    product: InsuranceProduct
    effective_period: EffectivePeriod
    transaction_types: list[TransactionType] = Field(
        default_factory=lambda: [TransactionType.NEW_BUSINESS, TransactionType.RENEWAL]
    )
    inputs: list[PricingInput] = Field(default_factory=list)
    constants: list[PricingConstant] = Field(default_factory=list)
    tables: list[RateTable] = Field(default_factory=list)
    rules: list[PricingRule] = Field(default_factory=list)
    calculations: list[CalculationNode] = Field(default_factory=list)
    rounding_rules: list[RoundingRule] = Field(default_factory=list)
    modifiers: list[PricingModifier] = Field(default_factory=list)
    constraints: list[PremiumConstraint] = Field(default_factory=list)
    fees: list[PricingFee] = Field(default_factory=list)
    coverages: list[CoverageDefinition] = Field(default_factory=list)
    outputs: list[PricingOutput] = Field(default_factory=list)
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def validate_package(self) -> "IPIRPackage":
        self.id = validate_identifier_string(self.id)

        # 1. Duplicate ID validation across semantic entities
        seen_ids: dict[str, str] = {}

        collections = [
            ("input", self.inputs),
            ("constant", self.constants),
            ("table", self.tables),
            ("rule", self.rules),
            ("calculation", self.calculations),
            ("modifier", self.modifiers),
            ("constraint", self.constraints),
            ("fee", self.fees),
            ("coverage", self.coverages),
            ("output", self.outputs),
        ]

        for entity_type, items in collections:
            for item in items:
                item_id = item.id
                if item_id in seen_ids:
                    raise ValueError(
                        f"Duplicate node ID '{item_id}' found in package. "
                        f"First seen in {seen_ids[item_id]}, duplicated in {entity_type}."
                    )
                seen_ids[item_id] = entity_type

        # 2. Rounding rule duplicate ID validation
        seen_rounding_ids: set[str] = set()
        for rr in self.rounding_rules:
            if rr.id in seen_rounding_ids:
                raise ValueError(f"Duplicate RoundingRule ID '{rr.id}' in package.")
            seen_rounding_ids.add(rr.id)

        # 3. Output source_ref existence validation
        all_node_ids = set(seen_ids.keys())
        for output in self.outputs:
            if output.source_ref not in all_node_ids:
                raise ValueError(
                    f"PricingOutput '{output.id}' references nonexistent "
                    f"source node '{output.source_ref}'"
                )

        return self
