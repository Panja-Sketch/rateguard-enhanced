import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = BACKEND_ROOT.parent
SAMPLES_ROOT = REPO_ROOT / "data" / "samples" / "workbook_v1"

_SCRIPTS_DIR = BACKEND_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


@pytest.fixture(scope="session", autouse=True)
def _ensure_samples_generated():
    """The workbook_v1 sample generator (`generate_workbook_v1_samples.py`)
    is the single source of truth for every fixture `.xlsx` used by this
    test module -- nothing here is hand-edited binary. Regenerates once per
    test session so the suite is self-contained even on a fresh checkout."""
    import generate_workbook_v1_samples as gen

    gen.main()
    return SAMPLES_ROOT


def read_sample(relative_path: str) -> bytes:
    return (SAMPLES_ROOT / relative_path).read_bytes()


@pytest.fixture()
def canonical_bytes() -> bytes:
    return read_sample("canonical/AZ_HO3_GOLDEN_workbook.xlsx")


@pytest.fixture()
def defective_bytes() -> bytes:
    return read_sample("defective/AZ_HO3_GOLDEN_workbook.xlsx")


def negative_sample(name: str) -> bytes:
    return read_sample(f"negative/{name}")
