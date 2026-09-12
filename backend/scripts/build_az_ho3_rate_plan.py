import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.ipir.calculations import CalculationNode  # noqa: E402
from app.ipir.common import EffectivePeriod, LiteralValue, NodeReference  # noqa: E402
from app.ipir.constraints import PremiumConstraint, PricingFee, RoundingRule  # noqa: E402
from app.ipir.enums import (  # noqa: E402
    ComparisonOperator,
    ConstraintType,
    ExpressionOperator,
    InputDataType,
    InsuranceLine,
    ModifierType,
    ProvenanceSourceType,
    RoundingMode,
    TableLookupType,
)
from app.ipir.expressions import Expression  # noqa: E402
from app.ipir.inputs import PricingInput  # noqa: E402
from app.ipir.modifiers import PricingModifier  # noqa: E402
from app.ipir.package import IPIRPackage, PricingConstant  # noqa: E402
from app.ipir.product import (  # noqa: E402
    CoverageDefinition,
    InsuranceProduct,
    Jurisdiction,
    PricingOutput,
)
from app.ipir.provenance import Provenance, SourceReference  # noqa: E402
from app.ipir.rules import ComparisonCondition  # noqa: E402
from app.ipir.tables import (  # noqa: E402
    ExactMatch,
    RangeMatch,
    RateTable,
    TableDimension,
    TableRow,
)


def build_canonical_package() -> IPIRPackage:
    """Builds the canonical synthetic Arizona HO3 rate plan package."""
    # 1. Product & Effective Period
    product = InsuranceProduct(
        id="AZ_HO3",
        name="Arizona Homeowners HO3 Product",
        line=InsuranceLine.HOMEOWNERS,
        form="HO3",
        jurisdiction=Jurisdiction(country="US", state_or_province="AZ"),
    )
    effective_period = EffectivePeriod(start=date(2026, 9, 1))

    # 2. Primary Rating Inputs
    inputs = [
        PricingInput(
            id="territory",
            name="Rating Territory Code",
            data_type=InputDataType.STRING,
            description="Arizona rating territory code (T01 through T20)",
            allowed_values=[f"T{i:02d}" for i in range(1, 21)],
        ),
        PricingInput(
            id="roof_age",
            name="Roof Age (Years)",
            data_type=InputDataType.INTEGER,
            description="Age of roof in years (0 to 50)",
            minimum=Decimal("0"),
            maximum=Decimal("50"),
        ),
        PricingInput(
            id="deductible",
            name="Policy Deductible Amount ($)",
            data_type=InputDataType.MONEY,
            description="Policy all-peril deductible ($500, $1000, $2500, $5000)",
            allowed_values=["500", "1000", "2500", "5000"],
        ),
        PricingInput(
            id="protection_class",
            name="Public Protection Class",
            data_type=InputDataType.INTEGER,
            description="ISO public protection class (1 to 10)",
            minimum=Decimal("1"),
            maximum=Decimal("10"),
        ),
        PricingInput(
            id="construction_type",
            name="Building Construction Type",
            data_type=InputDataType.STRING,
            description="Construction type (FRAME, MASONRY, FIRE_RESISTIVE, SUPERIOR)",
            allowed_values=["FRAME", "MASONRY", "FIRE_RESISTIVE", "SUPERIOR"],
        ),
        PricingInput(
            id="dwelling_limit",
            name="Coverage A Dwelling Limit ($)",
            data_type=InputDataType.MONEY,
            description="Coverage A Dwelling replacement cost limit ($100,000 to $2,000,000)",
            minimum=Decimal("100000"),
            maximum=Decimal("2000000"),
        ),
        PricingInput(
            id="claims_free",
            name="Claims-Free Prior Years",
            data_type=InputDataType.BOOLEAN,
            description="Flag indicating policyholder has no claims in prior 3 years",
        ),
        PricingInput(
            id="multi_policy",
            name="Multi-Policy Discount Eligible",
            data_type=InputDataType.BOOLEAN,
            description="Flag indicating companion auto or umbrella policy",
        ),
    ]

    # 3. Constants
    constants = [
        PricingConstant(id="base_rate", name="Base Premium Rate", value=Decimal("650.00")),
        PricingConstant(id="MINIMUM_PREMIUM", name="Minimum Policy Premium Floor", value=Decimal("575.00")),
        PricingConstant(id="POLICY_FEE", name="Statutory Policy Fee", value=Decimal("25.00")),
    ]

    # 4. Tables
    # Table 1: Territory Base Factor (T01=1.00, T17=1.20)
    t1_rows = [
        TableRow(matches=[ExactMatch(value=f"T{i:02d}")], value=Decimal(f"{1.00 + (i-1)*0.0125:.2f}"))
        for i in range(1, 21)
    ]
    t1 = RateTable(
        id="territory_factor",
        name="Territory Base Factor Table",
        dimensions=[TableDimension(input_ref="territory", lookup_type=TableLookupType.EXACT)],
        rows=t1_rows,
    )

    # Table 2: Roof Age Factor (0..5:0.90, 6..10:1.00, 11..20:1.10, 21..30:1.35, 31..50:1.50)
    t2_rows = [
        TableRow(matches=[RangeMatch(minimum=Decimal("0"), maximum=Decimal("5"))], value=Decimal("0.90")),
        TableRow(matches=[RangeMatch(minimum=Decimal("6"), maximum=Decimal("10"))], value=Decimal("1.00")),
        TableRow(matches=[RangeMatch(minimum=Decimal("11"), maximum=Decimal("20"))], value=Decimal("1.10")),
        TableRow(matches=[RangeMatch(minimum=Decimal("21"), maximum=Decimal("30"))], value=Decimal("1.35")),
        TableRow(matches=[RangeMatch(minimum=Decimal("31"), maximum=Decimal("50"))], value=Decimal("1.50")),
    ]
    t2 = RateTable(
        id="roof_age_factor",
        name="Roof Age Factor Table",
        dimensions=[TableDimension(input_ref="roof_age", lookup_type=TableLookupType.RANGE)],
        rows=t2_rows,
    )

    # Table 3: Deductible Factor
    t3_rows = [
        TableRow(matches=[ExactMatch(value="500")], value=Decimal("1.10")),
        TableRow(matches=[ExactMatch(value="1000")], value=Decimal("1.00")),
        TableRow(matches=[ExactMatch(value="2500")], value=Decimal("0.90")),
        TableRow(matches=[ExactMatch(value="5000")], value=Decimal("0.80")),
    ]
    t3 = RateTable(
        id="deductible_factor",
        name="Deductible Factor Table",
        dimensions=[TableDimension(input_ref="deductible", lookup_type=TableLookupType.EXACT)],
        rows=t3_rows,
    )

    # Table 4: Protection Class Factor
    t4_rows = [
        TableRow(matches=[RangeMatch(minimum=Decimal("1"), maximum=Decimal("3"))], value=Decimal("0.95")),
        TableRow(matches=[RangeMatch(minimum=Decimal("4"), maximum=Decimal("6"))], value=Decimal("1.00")),
        TableRow(matches=[RangeMatch(minimum=Decimal("7"), maximum=Decimal("8"))], value=Decimal("1.10")),
        TableRow(matches=[RangeMatch(minimum=Decimal("9"), maximum=Decimal("10"))], value=Decimal("1.25")),
    ]
    t4 = RateTable(
        id="protection_class_factor",
        name="Protection Class Factor Table",
        dimensions=[TableDimension(input_ref="protection_class", lookup_type=TableLookupType.RANGE)],
        rows=t4_rows,
    )

    # Table 5: Construction Factor
    t5_rows = [
        TableRow(matches=[ExactMatch(value="FRAME")], value=Decimal("1.10")),
        TableRow(matches=[ExactMatch(value="MASONRY")], value=Decimal("0.95")),
        TableRow(matches=[ExactMatch(value="FIRE_RESISTIVE")], value=Decimal("0.85")),
        TableRow(matches=[ExactMatch(value="SUPERIOR")], value=Decimal("0.80")),
    ]
    t5 = RateTable(
        id="construction_factor",
        name="Construction Factor Table",
        dimensions=[TableDimension(input_ref="construction_type", lookup_type=TableLookupType.EXACT)],
        rows=t5_rows,
    )

    # Table 6: Dwelling Limit Factor (100k..250k:0.85, 250k1..500k:1.15, 500k1..1M:1.20, 1M1..2M:1.50)
    t6_rows = [
        TableRow(matches=[RangeMatch(minimum=Decimal("100000"), maximum=Decimal("250000"))], value=Decimal("0.85")),
        TableRow(matches=[RangeMatch(minimum=Decimal("250001"), maximum=Decimal("500000"))], value=Decimal("1.15")),
        TableRow(matches=[RangeMatch(minimum=Decimal("500001"), maximum=Decimal("1000000"))], value=Decimal("1.20")),
        TableRow(matches=[RangeMatch(minimum=Decimal("1000001"), maximum=Decimal("2000000"))], value=Decimal("1.50")),
    ]
    t6 = RateTable(
        id="dwelling_limit_factor",
        name="Dwelling Limit Factor Table",
        dimensions=[TableDimension(input_ref="dwelling_limit", lookup_type=TableLookupType.RANGE)],
        rows=t6_rows,
    )

    # Table 7: 2D Territory-Construction Adjustment (60 combinations)
    t7_rows = []
    for terr in [f"T{i:02d}" for i in range(1, 21)]:
        frame_adj = Decimal("1.08") if terr == "T17" else Decimal("1.00")
        t7_rows.append(TableRow(matches=[ExactMatch(value=terr), ExactMatch(value="FRAME")], value=frame_adj))
        t7_rows.append(TableRow(matches=[ExactMatch(value=terr), ExactMatch(value="MASONRY")], value=Decimal("0.98")))
        t7_rows.append(TableRow(matches=[ExactMatch(value=terr), ExactMatch(value="SUPERIOR")], value=Decimal("0.95")))

    t7 = RateTable(
        id="territory_construction_adjustment",
        name="2D Territory-Construction Adjustment Table",
        dimensions=[
            TableDimension(input_ref="territory", lookup_type=TableLookupType.EXACT),
            TableDimension(input_ref="construction_type", lookup_type=TableLookupType.EXACT),
        ],
        rows=t7_rows,
    )

    tables = [t1, t2, t3, t4, t5, t6, t7]

    # 5. Modifiers (Discounts)
    m1 = PricingModifier(
        id="multi_policy_discount",
        name="Multi-Policy Discount (12%)",
        modifier_type=ModifierType.PERCENTAGE_DISCOUNT,
        applies_to="gross_risk_premium",
        value=Decimal("0.12"),
        eligibility=ComparisonCondition(left=NodeReference(ref="multi_policy"), operator=ComparisonOperator.EQ, right=LiteralValue(value=True)),
    )
    m2 = PricingModifier(
        id="claims_free_discount",
        name="Claims-Free Discount (5%)",
        modifier_type=ModifierType.PERCENTAGE_DISCOUNT,
        applies_to="gross_risk_premium",
        value=Decimal("0.05"),
        eligibility=ComparisonCondition(left=NodeReference(ref="claims_free"), operator=ComparisonOperator.EQ, right=LiteralValue(value=True)),
        effective_period=EffectivePeriod(start=date(2026, 9, 1)),
    )
    modifiers = [m1, m2]

    # 6. Constraints & Fees & Rounding Rules
    rr = RoundingRule(id="standard_currency_rounding", precision=2, mode=RoundingMode.HALF_UP)
    min_constraint = PremiumConstraint(
        id="policy_minimum",
        name="Minimum Premium Floor ($575)",
        constraint_type=ConstraintType.MINIMUM,
        amount=Decimal("575.00"),
        applies_to="premium_after_discounts",
        sequence=1,
    )
    policy_fee = PricingFee(
        id="policy_fee",
        name="Statutory Policy Fee ($25)",
        amount=Decimal("25.00"),
        applies_to="premium_after_minimum",
        sequence=2,
    )
    constraints = [min_constraint]
    fees = [policy_fee]

    # 7. Calculation Nodes
    calc1 = CalculationNode(
        id="gross_risk_premium",
        name="Gross Risk Premium Calculation",
        expression=Expression(
            operator=ExpressionOperator.MULTIPLY,
            operands=[
                NodeReference(ref="base_rate"),
                NodeReference(ref="territory_factor"),
                NodeReference(ref="roof_age_factor"),
                NodeReference(ref="deductible_factor"),
                NodeReference(ref="protection_class_factor"),
                NodeReference(ref="construction_factor"),
                NodeReference(ref="dwelling_limit_factor"),
                NodeReference(ref="territory_construction_adjustment"),
            ],
        ),
        depends_on=[
            "base_rate",
            "territory_factor",
            "roof_age_factor",
            "deductible_factor",
            "protection_class_factor",
            "construction_factor",
            "dwelling_limit_factor",
            "territory_construction_adjustment",
        ],
        rounding_rule=rr,
    )
    calc2 = CalculationNode(
        id="premium_after_discounts",
        name="Premium After Discounts Calculation",
        expression=NodeReference(ref="gross_risk_premium"),
        depends_on=["gross_risk_premium", "multi_policy_discount", "claims_free_discount"],
        rounding_rule=rr,
    )
    calc3 = CalculationNode(
        id="premium_after_minimum",
        name="Premium After Minimum Floor Calculation",
        expression=NodeReference(ref="premium_after_discounts"),
        depends_on=["premium_after_discounts", "policy_minimum"],
        rounding_rule=rr,
    )
    calc4 = CalculationNode(
        id="total_policy_premium",
        name="Total Final Policy Premium Calculation",
        expression=NodeReference(ref="premium_after_minimum"),
        depends_on=["premium_after_minimum", "policy_fee"],
        rounding_rule=rr,
    )
    calculations = [calc1, calc2, calc3, calc4]

    # 8. Coverages & Outputs
    coverages = [
        CoverageDefinition(id="COV_A", name="Dwelling Coverage A", calculation_refs=["gross_risk_premium"]),
        CoverageDefinition(id="COV_B", name="Other Structures Coverage B", calculation_refs=["gross_risk_premium"]),
        CoverageDefinition(id="COV_C", name="Personal Property Coverage C", calculation_refs=["gross_risk_premium"]),
        CoverageDefinition(id="COV_D", name="Loss of Use Coverage D", calculation_refs=["gross_risk_premium"]),
        CoverageDefinition(id="COV_E", name="Personal Liability Coverage E", calculation_refs=["gross_risk_premium"]),
        CoverageDefinition(id="COV_F", name="Medical Payments Coverage F", calculation_refs=["gross_risk_premium"]),
    ]
    outputs = [
        PricingOutput(id="final_policy_premium", name="Final Billed Premium", source_ref="total_policy_premium"),
        PricingOutput(id="gross_premium", name="Gross Risk Premium Output", source_ref="gross_risk_premium"),
    ]

    provenance = Provenance(
        sources=[SourceReference(source_type=ProvenanceSourceType.ACTUARIAL_SPEC, source_id="synthetic-az-ho3-2026-09", source_name="Arizona HO3 2026 Rate Filing Spec")],
        extraction_confidence=Decimal("1.0"),
        interpretation_confidence=Decimal("1.0"),
        notes="Synthetic canonical Arizona HO3 rate plan built for RateGuard hackathon demo.",
    )

    return IPIRPackage(
        id="AZ_HO3_2026_09",
        name="Arizona Homeowners HO3 Rate Plan (Canonical 2026.09)",
        version="2026.09",
        effective_period=effective_period,
        product=product,
        inputs=inputs,
        constants=constants,
        tables=tables,
        rounding_rules=[rr],
        modifiers=modifiers,
        constraints=constraints,
        fees=fees,
        coverages=coverages,
        outputs=outputs,
        calculations=calculations,
        provenance=provenance,
    )


def main() -> None:
    package = build_canonical_package()

    root_dir = Path(__file__).resolve().parent.parent.parent
    canonical_dir = root_dir / "data" / "implementations" / "canonical"
    actuarial_dir = root_dir / "data" / "actuarial"
    canonical_dir.mkdir(parents=True, exist_ok=True)
    actuarial_dir.mkdir(parents=True, exist_ok=True)

    canonical_file = canonical_dir / "AZ_HO3_2026_09_ipir.json"
    actuarial_file = actuarial_dir / "AZ_HO3_2026_09_rate_spec.json"

    pkg_json = package.model_dump_json(indent=2) + "\n"

    with open(canonical_file, "w", encoding="utf-8") as f:
        f.write(pkg_json)
    with open(actuarial_file, "w", encoding="utf-8") as f:
        f.write(pkg_json)

    print(f"Successfully wrote canonical IPIR package to: {canonical_file}")
    print(f"Successfully wrote actuarial rate spec JSON to: {actuarial_file}")


if __name__ == "__main__":
    main()
