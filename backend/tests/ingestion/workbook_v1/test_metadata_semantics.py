"""Semantic (not merely format) validation of RG_METADATA currency and
jurisdiction: a workbook may never become VERIFIED with an arbitrary
well-formatted value, and an unsupported value is rejected -- never
normalized into a supported one."""

from __future__ import annotations

import io

import openpyxl
import pytest

from app.ingestion.workbook_v1.compiler import compile_workbook
from app.ingestion.workbook_v1.vocabulary import (
    RECOGNIZED_CURRENCIES,
    SUPPORTED_CURRENCIES,
    SUPPORTED_JURISDICTIONS,
)


def _variant(canonical: bytes, **changes: str | None) -> bytes:
    wb = openpyxl.load_workbook(io.BytesIO(canonical))
    ws = wb["RG_METADATA"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value in changes:
            row[1].value = changes[row[0].value]
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _rejection(receipt) -> str:
    assert receipt.status == "REJECTED"
    assert receipt.package is None
    return receipt.errors[0].code


def test_supported_scope_is_usd_and_arizona():
    assert SUPPORTED_CURRENCIES == {"USD"}
    assert SUPPORTED_JURISDICTIONS == {"US": frozenset({"AZ"})}


def test_valid_usd_arizona_workbook_still_verifies(canonical_bytes):
    assert compile_workbook(_variant(canonical_bytes, currency="USD", state="AZ"), "ok.xlsx").status == "VERIFIED"


@pytest.mark.parametrize("currency", ["ZZZ", "XXX", "ABC"])
def test_unrecognized_currency_is_rejected(canonical_bytes, currency):
    receipt = compile_workbook(_variant(canonical_bytes, currency=currency), "c.xlsx")
    assert _rejection(receipt) == "UNSUPPORTED_CURRENCY"
    assert "not a recognized ISO 4217" in receipt.errors[0].message


@pytest.mark.parametrize("currency", ["EUR", "GBP", "CAD"])
def test_recognized_but_unsupported_currency_is_rejected_not_converted(canonical_bytes, currency):
    assert currency in RECOGNIZED_CURRENCIES
    receipt = compile_workbook(_variant(canonical_bytes, currency=currency), "c.xlsx")
    assert _rejection(receipt) == "UNSUPPORTED_CURRENCY"
    assert "recognized ISO 4217 code but not supported" in receipt.errors[0].message


@pytest.mark.parametrize("currency", ["usd", "Usd"])
def test_currency_is_not_silently_case_normalized(canonical_bytes, currency):
    assert _rejection(compile_workbook(_variant(canonical_bytes, currency=currency), "c.xlsx")) == "UNSUPPORTED_CURRENCY"


@pytest.mark.parametrize("state", ["XX", "ZZ", "Arizona"])
def test_unrecognized_state_is_rejected(canonical_bytes, state):
    receipt = compile_workbook(_variant(canonical_bytes, state=state), "s.xlsx")
    assert _rejection(receipt) == "UNSUPPORTED_JURISDICTION_STATE"
    assert "missing or not a recognized" in receipt.errors[0].message


@pytest.mark.parametrize("state", ["CA", "TX", "DC"])
def test_recognized_but_unconfigured_state_is_rejected(canonical_bytes, state):
    receipt = compile_workbook(_variant(canonical_bytes, state=state), "s.xlsx")
    assert _rejection(receipt) == "UNSUPPORTED_JURISDICTION_STATE"
    assert "recognized code but not supported" in receipt.errors[0].message


def test_lowercase_state_is_not_silently_normalized(canonical_bytes):
    assert _rejection(compile_workbook(_variant(canonical_bytes, state="az"), "s.xlsx")) == "UNSUPPORTED_JURISDICTION_STATE"


def test_missing_state_is_rejected_for_a_us_product(canonical_bytes):
    assert _rejection(compile_workbook(_variant(canonical_bytes, state=None), "s.xlsx")) == "UNSUPPORTED_JURISDICTION_STATE"


@pytest.mark.parametrize("country", ["CA", "GB", "us"])
def test_unsupported_country_is_rejected(canonical_bytes, country):
    assert _rejection(compile_workbook(_variant(canonical_bytes, country=country), "c.xlsx")) == "UNSUPPORTED_JURISDICTION_COUNTRY"
