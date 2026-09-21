"""Demo-only, reversible fault injection for live resilience acceptance.

`RATING_ENGINE_FAULT_MODE` is unset (off) by default. `subset503:<fraction>`
makes a deterministic fraction of quotes (chosen by a hash of the request's
rating inputs, so a retry of the same quote fails again) report a transient
"temporarily unavailable" failure - as an HTTP 503 on `/quote` and as a
per-item `TEMPORARILY_UNAVAILABLE` error on `/quote/batch`. Removing the
variable (a new revision) restores normal behaviour; no IAM or data changes are
involved. A real vendor engine would have nothing like this.
"""

import hashlib
import json
import logging
import os
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)
FAULT_ENV_VAR = "RATING_ENGINE_FAULT_MODE"


def _fraction() -> float:
    raw = os.environ.get(FAULT_ENV_VAR, "").strip()
    if not raw:
        return 0.0
    mode, _, arg = raw.partition(":")
    if mode != "subset503":
        logger.error("Unrecognised %s value; fault injection stays OFF.", FAULT_ENV_VAR)
        return 0.0
    try:
        value = float(arg)
    except ValueError:
        return 0.0
    return value if 0.0 < value <= 1.0 else 0.0


def is_active() -> bool:
    return _fraction() > 0.0


def should_fail(effective_date: date, inputs: dict[str, Any]) -> bool:
    fraction = _fraction()
    if fraction <= 0.0:
        return False
    digest = hashlib.sha256(
        json.dumps([effective_date.isoformat(), inputs], sort_keys=True, default=str).encode()
    ).digest()
    return int.from_bytes(digest[:4], "big") / 2**32 < fraction
