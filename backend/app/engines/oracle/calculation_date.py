"""Deterministic calculation-date resolution for premium oracle execution.

Precedence (first one supplied wins; a supplied-but-invalid value fails closed
rather than falling through to a lower-precedence source):

1. an explicit mission/probe calculation date;
2. the workbook control case's own calculation date, when the probe came from
   a control case;
3. the compiled package's effective-period start date;
4. otherwise: a specific validation error. No constant and no "today" is ever
   substituted.

Every resolved date is checked against the package's effective period, so a
date the package is not active on is rejected here with a specific code before
any pricing arithmetic or connector call happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from app.engines.oracle.errors import CalculationDateError
from app.ipir.package import IPIRPackage


class CalculationDateSource(StrEnum):
    EXPLICIT = "EXPLICIT"
    CONTROL_CASE = "CONTROL_CASE"
    PACKAGE_EFFECTIVE_START = "PACKAGE_EFFECTIVE_START"


@dataclass(frozen=True)
class ResolvedCalculationDate:
    value: date
    source: CalculationDateSource


def _parse(raw: Any, *, label: str) -> date:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw.strip())
        except ValueError as exc:
            raise CalculationDateError(
                "INVALID_CALCULATION_DATE",
                f"The {label} calculation date {raw!r} is not a valid ISO-8601 date (YYYY-MM-DD).",
            ) from exc
    raise CalculationDateError(
        "INVALID_CALCULATION_DATE",
        f"The {label} calculation date must be an ISO-8601 date string, got {type(raw).__name__}.",
    )


def resolve_calculation_date(
    package: IPIRPackage | None,
    *,
    explicit: date | str | None = None,
    control_case: date | str | None = None,
) -> ResolvedCalculationDate:
    """Resolves and validates the calculation date for one probe/quote."""
    if explicit is not None:
        resolved = ResolvedCalculationDate(_parse(explicit, label="explicit"), CalculationDateSource.EXPLICIT)
    elif control_case is not None:
        resolved = ResolvedCalculationDate(
            _parse(control_case, label="control-case"), CalculationDateSource.CONTROL_CASE
        )
    elif package is not None and package.effective_period is not None and package.effective_period.start:
        resolved = ResolvedCalculationDate(
            package.effective_period.start, CalculationDateSource.PACKAGE_EFFECTIVE_START
        )
    else:
        raise CalculationDateError(
            "MISSING_CALCULATION_DATE",
            "No calculation date is available: none was supplied and the package declares no effective start.",
        )

    if package is not None:
        period = package.effective_period
        if resolved.value < period.start or (period.end is not None and resolved.value > period.end):
            raise CalculationDateError(
                "CALCULATION_DATE_OUT_OF_PERIOD",
                f"Calculation date {resolved.value} ({resolved.source.value}) is outside the effective period of "
                f"package '{package.id}' ({period.start} to {period.end or 'open'}).",
            )
    return resolved
