"""ENGINE-OWNED rating behaviour for the RateGuard Demo Insurer Rating Engine.

This is the one small file a developer edits to change what the deployed
engine prices. It is deliberately self-contained: plain tables and plain
Python, with no import from RateGuard (no oracle, IPIR, workbook compiler,
test planner, comparison or decision code). RateGuard only ever reaches this
behaviour through the versioned REST contract in `rating_engine.main`.

Two implementation versions are served (the caller selects one by name):

* ``canonical-v1`` prices the Arizona HO3 plan the way the approved
  specification intends.
* ``defective-v1`` is the same implementation carrying one realistic defect:
  the top roof-age tier was keyed in with the wrong factor.

Premium = round_half_up(BASE_RATE x roof_age_factor, 2 decimal places).
``dwelling_limit`` is a required rating input that does not change premium in
this plan.

To demonstrate a developer change, edit a factor below (for example correct the
``defective-v1`` top tier from ``1.31`` to ``1.40``), redeploy this service and
re-run the same RateGuard mission: RateGuard code is not touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

PRODUCT_ID = "az_ho3"
EFFECTIVE_FROM = date(2026, 10, 1)
SUPPORTED_TRANSACTION_TYPES = ("NEW_BUSINESS", "RENEWAL")
REQUIRED_INPUTS = ("roof_age", "dwelling_limit")

BASE_RATE = Decimal("500.00")

# Semantic version of this engine implementation (not of any RateGuard artifact).
IMPLEMENTATION_VERSION = "1.0.0"


@dataclass(frozen=True)
class RoofAgeTier:
    """Inclusive roof-age range; ``max_age=None`` means no upper bound."""

    min_age: int
    max_age: int | None
    factor: Decimal

    def contains(self, roof_age: int) -> bool:
        return roof_age >= self.min_age and (self.max_age is None or roof_age <= self.max_age)


ROOF_AGE_TIERS: dict[str, tuple[RoofAgeTier, ...]] = {
    "canonical-v1": (
        RoofAgeTier(0, 10, Decimal("1.00")),
        RoofAgeTier(11, 20, Decimal("1.20")),
        RoofAgeTier(21, None, Decimal("1.40")),
    ),
    "defective-v1": (
        RoofAgeTier(0, 10, Decimal("1.00")),
        RoofAgeTier(11, 20, Decimal("1.20")),
        # DEFECT: a mis-keyed factor for roofs 21 years and older (should be 1.40).
        RoofAgeTier(21, None, Decimal("1.31")),
    ),
}


class UnknownEngineVersionError(Exception):
    """The requested ``engine_version`` is not served. Never falls back to a
    default version."""


def known_engine_versions() -> list[str]:
    return sorted(ROOF_AGE_TIERS)


def tiers_for(engine_version: str) -> tuple[RoofAgeTier, ...]:
    tiers = ROOF_AGE_TIERS.get(engine_version)
    if tiers is None:
        raise UnknownEngineVersionError(
            f"Unknown engine_version '{engine_version}'. Supported: {known_engine_versions()}."
        )
    return tiers
