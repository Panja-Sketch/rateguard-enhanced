from typing import Any

from pydantic import ValidationError

from app.ipir.package import IPIRPackage


def generate_ipir_json_schema() -> dict[str, Any]:
    """Generates the JSON Schema for the root IPIRPackage model."""
    return IPIRPackage.model_json_schema()


def validate_ipir_schema(pkg: IPIRPackage) -> list[str]:
    """Mandatory deterministic schema-validation checkpoint for a compiled IPIR
    package. Every extraction path (deterministic adapter, Gemini-selected
    extractor, or fallback) MUST pass through this before the result is
    trusted — it re-runs `IPIRPackage`'s full validator (duplicate node IDs,
    dangling output source_refs, etc.) against the package's own serialized
    form, so a package built via any non-validating path is still caught.
    Returns a list of human-readable issues; empty means valid.
    """
    issues: list[str] = []
    try:
        IPIRPackage.model_validate(pkg.model_dump(mode="json"))
    except ValidationError as exc:
        issues.extend(str(err["msg"]) for err in exc.errors())

    if not pkg.calculations:
        issues.append("IPIR package contains zero calculation nodes; nothing to price.")

    return issues
