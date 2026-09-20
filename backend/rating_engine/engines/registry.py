"""Maps a versioned engine identifier to its backing IPIR v0.2 golden
fixture (locked doc section 8.3: "The isolated demo service exposes exactly
two versioned behaviors"). Deliberately a small, closed, in-memory registry —
not the admin-managed connector registry (`app.connectors`, a later
session's CP8 work), which governs which *external* engines a mission may
select. This registry only resolves the two fixed demo behaviors this
service itself serves.
"""

from functools import lru_cache
from pathlib import Path

from app.core.config import get_data_dir
from app.ipir.v0_2.package import IPIRPackageV2

# Resolved via the shared data-dir logic (RATEGUARD_DATA_DIR, repo-root data/,
# or the container's /app/data) rather than a fixed number of parent hops,
# which points outside the image once packaged.
_DATA_DIR = get_data_dir()

ENGINE_FIXTURE_PATHS: dict[str, Path] = {
    "canonical-v1": _DATA_DIR
    / "implementations"
    / "v0_2"
    / "canonical"
    / "AZ_HO3_GOLDEN_ipir.json",
    "defective-v1": _DATA_DIR
    / "implementations"
    / "v0_2"
    / "defective"
    / "AZ_HO3_GOLDEN_ipir.json",
}


class UnknownEngineVersionError(Exception):
    """Raised when a QuoteRequest names an `engine_version` this service does
    not serve. Never falls back to a default engine — an unrecognized
    version must fail closed."""


@lru_cache(maxsize=len(ENGINE_FIXTURE_PATHS))
def load_engine_package(engine_version: str) -> IPIRPackageV2:
    path = ENGINE_FIXTURE_PATHS.get(engine_version)
    if path is None:
        raise UnknownEngineVersionError(
            f"Unknown engine_version '{engine_version}'. Supported: "
            f"{sorted(ENGINE_FIXTURE_PATHS)}."
        )
    return IPIRPackageV2.model_validate_json(path.read_text(encoding="utf-8"))


def known_engine_versions() -> list[str]:
    return sorted(ENGINE_FIXTURE_PATHS)
