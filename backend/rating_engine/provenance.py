"""Non-sensitive deployment provenance for the engine.

Everything here is safe to publish: an implementation version, the git commit
the image was built from, the immutable image digest and the Cloud Run
revision. No secret, credential, caller identity or request data is included.
"""

from __future__ import annotations

import os
from typing import Any

from rating_engine.engines.versions import (
    EFFECTIVE_FROM,
    IMPLEMENTATION_VERSION,
    PRODUCT_ID,
    SUPPORTED_TRANSACTION_TYPES,
    known_engine_versions,
)
from rating_engine.models import BATCH_MAX_ITEMS, BATCH_SCHEMA_VERSION

SERVICE_NAME = "RateGuard Demo Insurer Rating Engine (black-box REST reference engine)"


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def provenance() -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "implementation_version": IMPLEMENTATION_VERSION,
        # Baked into the image at build time (Dockerfile ARG), so it cannot drift
        # from the code that is actually running.
        "source_commit": _env("ENGINE_SOURCE_COMMIT") or "unknown",
        "image_digest": _env("RATEGUARD_IMAGE_DIGEST"),
        "deployment_revision": _env("K_REVISION"),
    }


def capabilities_document() -> dict[str, Any]:
    return {
        "quote_batch": {"schema_version": BATCH_SCHEMA_VERSION, "max_items": BATCH_MAX_ITEMS},
        "engine_versions": known_engine_versions(),
        "products": [PRODUCT_ID],
        "transaction_types": list(SUPPORTED_TRANSACTION_TYPES),
        "effective_from": EFFECTIVE_FROM.isoformat(),
        "provenance": provenance(),
    }
