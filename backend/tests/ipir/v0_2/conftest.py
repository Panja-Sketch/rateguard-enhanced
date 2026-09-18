from app.ipir.common import EffectivePeriod
from app.ipir.enums import InputDataType, RoundingMode, TableLookupType, TransactionType
from app.ipir.inputs import PricingInput
from app.ipir.package import PricingConstant
from app.ipir.product import Jurisdiction
from app.ipir.tables import RangeMatch, RateTable, TableDimension, TableRow
from app.ipir.v0_2.calculations import CalculationNodeV2
from app.ipir.v0_2.control_cases import ControlCase
from app.ipir.v0_2.envelope import IpirSourceType, ProductRefV2, SourceMetadata
from app.ipir.v0_2.expressions import (
    BinaryExpression,
    ReferenceExpression,
    RoundExpression,
    TableLookupExpression,
)
from app.ipir.v0_2.outputs import PricingOutputV2
from app.ipir.v0_2.package import IPIRPackageV2


def make_source() -> SourceMetadata:
    return SourceMetadata(
        source_type=IpirSourceType.STRUCTURED_JSON,
        artifact_sha256="a" * 64,
        compiler_version="test/1.0.0",
        compiled_at="2026-09-17T00:00:00+00:00",
    )


def make_product(**overrides) -> ProductRefV2:
    defaults = dict(
        product_id="az_ho3",
        line="HOMEOWNERS",
        jurisdiction=Jurisdiction(country="US", state_or_province="AZ"),
        currency="USD",
    )
    defaults.update(overrides)
    return ProductRefV2(**defaults)


def make_minimal_package(**overrides) -> IPIRPackageV2:
    """A minimal, structurally valid IPIR v0.2 package: one RANGE table, a
    three-node calculation graph (table lookup -> multiply -> round), one
    output, one control case. Individual tests override just the field(s)
    they're exercising."""
    defaults: dict = dict(
        package_id="test_pkg",
        package_version="1.0.0",
        source=make_source(),
        product=make_product(),
        effective_period=EffectivePeriod(start="2026-10-01", end=None),
        transaction_types=[TransactionType.NEW_BUSINESS, TransactionType.RENEWAL],
        inputs=[PricingInput(id="roof_age", name="Roof Age", data_type=InputDataType.INTEGER)],
        constants=[PricingConstant(id="base_rate", name="Base Rate", value="500.00")],
        tables=[
            RateTable(
                id="roof_age_factor_table",
                name="Roof Age Factor",
                dimensions=[TableDimension(input_ref="roof_age", lookup_type=TableLookupType.RANGE)],
                rows=[
                    TableRow(matches=[RangeMatch(minimum=0, maximum=20)], value="1.00"),
                    TableRow(matches=[RangeMatch(minimum=21, maximum=None)], value="1.40"),
                ],
            )
        ],
        calculations=[
            CalculationNodeV2(
                id="rate_factor",
                name="Rate factor",
                expression=TableLookupExpression(table_id="roof_age_factor_table"),
            ),
            CalculationNodeV2(
                id="raw_premium",
                name="Raw premium",
                expression=BinaryExpression(
                    operator="MULTIPLY",
                    left=ReferenceExpression(ref="base_rate"),
                    right=ReferenceExpression(ref="rate_factor"),
                ),
                depends_on=["base_rate", "rate_factor"],
            ),
            CalculationNodeV2(
                id="final_premium",
                name="Final premium",
                expression=RoundExpression(
                    operand=ReferenceExpression(ref="raw_premium"), scale=2, rounding_mode=RoundingMode.HALF_UP
                ),
                depends_on=["raw_premium"],
            ),
        ],
        outputs=[
            PricingOutputV2(
                id="final_premium_output",
                name="Final Premium",
                source_ref="final_premium",
                currency="USD",
                scale=2,
                rounding_mode=RoundingMode.HALF_UP,
            )
        ],
        control_cases=[
            ControlCase(
                case_id="golden_case",
                inputs={"roof_age": 25},
                expected_outputs={"final_premium_output": "700.00"},
                tolerance="0.00",
            )
        ],
    )
    defaults.update(overrides)
    return IPIRPackageV2(**defaults)
