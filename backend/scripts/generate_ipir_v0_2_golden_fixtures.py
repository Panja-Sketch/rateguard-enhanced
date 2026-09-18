#!/usr/bin/env python
"""Deterministically generates the two minimal IPIR v0.2 golden fixtures used
to prove the v0.2 contract and the `backend/rating_engine` foundation:
canonical (roof_age factor 1.40 -> $700.00) and defective (factor 1.31 ->
$655.00), for roof_age=25 against a $500.00 base rate. Mirrors the real
AZ_HO3 golden-case narrative (locked doc section 8.3) without migrating the
full production actuarial spec — see docs/implementation/DECISIONS.md.

Re-running this script always produces byte-identical output (no randomness,
no wall-clock timestamps) so the generated fixtures can be regenerated and
diffed rather than trusted blindly.

Usage: python backend/scripts/generate_ipir_v0_2_golden_fixtures.py
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ipir.common import EffectivePeriod  # noqa: E402
from app.ipir.enums import (  # noqa: E402
    InputDataType,
    RoundingMode,
    TableLookupType,
    TransactionType,
)
from app.ipir.inputs import PricingInput  # noqa: E402
from app.ipir.package import PricingConstant  # noqa: E402
from app.ipir.product import Jurisdiction  # noqa: E402
from app.ipir.tables import RangeMatch, RateTable, TableDimension, TableRow  # noqa: E402
from app.ipir.v0_2.calculations import CalculationNodeV2  # noqa: E402
from app.ipir.v0_2.control_cases import ControlCase  # noqa: E402
from app.ipir.v0_2.envelope import IpirSourceType, ProductRefV2, SourceMetadata  # noqa: E402
from app.ipir.v0_2.expressions import (  # noqa: E402
    BinaryExpression,
    ReferenceExpression,
    RoundExpression,
    TableLookupExpression,
)
from app.ipir.v0_2.outputs import PricingOutputV2  # noqa: E402
from app.ipir.v0_2.package import IPIRPackageV2  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPILED_AT = "2026-09-17T00:00:00+00:00"
COMPILER_VERSION = "rateguard-ipir-v0_2-golden-fixture-generator/1.0.0"


def _deterministic_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _build_package(*, engine_version: str, roof_age_21_plus_factor: str) -> IPIRPackageV2:
    return IPIRPackageV2(
        package_id="az_ho3_golden",
        package_version="1.0.0",
        source=SourceMetadata(
            source_type=IpirSourceType.STRUCTURED_JSON,
            artifact_sha256=_deterministic_hash("az_ho3_golden", engine_version),
            compiler_version=COMPILER_VERSION,
            compiled_at=COMPILED_AT,
        ),
        product=ProductRefV2(
            product_id="az_ho3",
            line="HOMEOWNERS",
            jurisdiction=Jurisdiction(country="US", state_or_province="AZ"),
            currency="USD",
        ),
        effective_period=EffectivePeriod(start="2026-10-01", end=None),
        transaction_types=[TransactionType.NEW_BUSINESS, TransactionType.RENEWAL],
        inputs=[
            PricingInput(id="roof_age", name="Roof Age (years)", data_type=InputDataType.INTEGER),
            PricingInput(
                id="dwelling_limit", name="Dwelling Coverage Limit", data_type=InputDataType.MONEY
            ),
        ],
        constants=[PricingConstant(id="base_rate", name="Base Rate", value="500.00")],
        tables=[
            RateTable(
                id="roof_age_factor_table",
                name="Roof Age Rate Factor",
                dimensions=[TableDimension(input_ref="roof_age", lookup_type=TableLookupType.RANGE)],
                rows=[
                    TableRow(
                        matches=[RangeMatch(minimum=0, maximum=10)],
                        value="1.00",
                    ),
                    TableRow(
                        matches=[RangeMatch(minimum=11, maximum=20)],
                        value="1.20",
                    ),
                    TableRow(
                        matches=[RangeMatch(minimum=21, maximum=None)],
                        value=roof_age_21_plus_factor,
                    ),
                ],
            )
        ],
        calculations=[
            CalculationNodeV2(
                id="rate_factor",
                name="Resolved roof-age rate factor",
                expression=TableLookupExpression(table_id="roof_age_factor_table"),
            ),
            CalculationNodeV2(
                id="raw_premium",
                name="Base rate x roof-age factor",
                expression=BinaryExpression(
                    operator="MULTIPLY",
                    left=ReferenceExpression(ref="base_rate"),
                    right=ReferenceExpression(ref="rate_factor"),
                ),
                depends_on=["base_rate", "rate_factor"],
            ),
            CalculationNodeV2(
                id="final_premium",
                name="Rounded final premium",
                expression=RoundExpression(
                    operand=ReferenceExpression(ref="raw_premium"),
                    scale=2,
                    rounding_mode=RoundingMode.HALF_UP,
                ),
                depends_on=["raw_premium"],
            ),
        ],
        outputs=[
            PricingOutputV2(
                id="final_premium_output",
                name="Total Policy Premium",
                source_ref="final_premium",
                currency="USD",
                scale=2,
                rounding_mode=RoundingMode.HALF_UP,
            )
        ],
        control_cases=[
            ControlCase(
                case_id="golden_case",
                inputs={"roof_age": 25, "dwelling_limit": "300000.00"},
                expected_outputs={
                    "final_premium_output": "700.00" if engine_version == "canonical-v1" else "655.00"
                },
                tolerance="0.00",
            )
        ],
    )


def main() -> None:
    canonical = _build_package(engine_version="canonical-v1", roof_age_21_plus_factor="1.40")
    defective = _build_package(engine_version="defective-v1", roof_age_21_plus_factor="1.31")

    targets = {
        REPO_ROOT / "data" / "implementations" / "v0_2" / "canonical" / "AZ_HO3_GOLDEN_ipir.json": canonical,
        REPO_ROOT / "data" / "implementations" / "v0_2" / "defective" / "AZ_HO3_GOLDEN_ipir.json": defective,
    }
    for path, package in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(json.loads(package.model_dump_json()), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
