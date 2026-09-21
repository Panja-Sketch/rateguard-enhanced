"""Portfolio snapshot loading and the allowlisted connector request builder.

The snapshot is the synthetic/de-identified portfolio CSV that ships with the
image. Its identity is the SHA-256 of the file bytes plus the row count; rows
keep file order, so a batch is the deterministic slice
`[batch_no * batch_size, (batch_no + 1) * batch_size)`. Only the approved
masked rating fields ever reach the connector — never the policy identifier.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_data_dir
from app.engines.portfolio.models import SyntheticPolicy
from app.storage.portfolio.csv_loader import load_synthetic_portfolio_csv

DEFAULT_DATASET = "az_ho3_2026_synthetic_50k.csv"

# The complete set of portfolio fields that may be used as rating inputs.
# Names, contact data, addresses, account numbers and the policy identifier are
# deliberately absent and can never be added by configuration.
APPROVED_RATING_FIELDS: tuple[str, ...] = (
    "territory",
    "roof_age",
    "deductible",
    "protection_class",
    "construction_type",
    "dwelling_limit",
    "multi_policy",
    "claims_free",
    "claims_free_years",
)


@dataclass(frozen=True)
class PortfolioSnapshot:
    dataset: str
    sha256: str
    rows: tuple[SyntheticPolicy, ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def identity(self) -> dict[str, object]:
        return {"dataset": self.dataset, "sha256": self.sha256, "row_count": self.row_count}


_CACHE: dict[tuple[str, int, int], PortfolioSnapshot] = {}
_LOCK = threading.Lock()


def _resolve_path(dataset: str) -> Path:
    base = get_data_dir() / "portfolio"
    candidate = base / Path(dataset).name  # never a caller-supplied directory
    return candidate if candidate.exists() else base / DEFAULT_DATASET


def load_snapshot(dataset: str = DEFAULT_DATASET) -> PortfolioSnapshot:
    """Loads (and caches per process) the snapshot for `dataset`."""
    path = _resolve_path(dataset)
    stat = path.stat()
    key = (str(path), stat.st_size, int(stat.st_mtime))
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        snapshot = PortfolioSnapshot(dataset=path.name, sha256=sha, rows=tuple(load_synthetic_portfolio_csv(path)))
        _CACHE[key] = snapshot
        return snapshot


def clear_snapshot_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def rating_inputs(policy: SyntheticPolicy, declared_input_ids: set[str]) -> dict[str, object]:
    """Approved masked rating fields the authoritative package actually declares.
    The same mapping feeds the local oracle and the connector request."""
    values = {name: getattr(policy, name) for name in APPROVED_RATING_FIELDS}
    return {k: v for k, v in values.items() if k in declared_input_ids}

