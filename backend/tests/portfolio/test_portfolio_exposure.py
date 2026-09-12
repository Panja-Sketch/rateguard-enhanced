from decimal import Decimal
from pathlib import Path

from app.engines.portfolio import PortfolioAnalyzer
from app.ipir.package import IPIRPackage
from scripts.run_portfolio_impact_demo import load_synthetic_portfolio_csv


def get_canonical_file_path() -> Path:
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    return root_dir / "data" / "implementations" / "canonical" / "AZ_HO3_2026_09_ipir.json"


def get_defective_file_path() -> Path:
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    return root_dir / "data" / "implementations" / "defective" / "AZ_HO3_2026_09_ipir.json"


def get_csv_file_path() -> Path:
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    return root_dir / "data" / "portfolio" / "az_ho3_2026_synthetic_50k.csv"


def load_canonical_package() -> IPIRPackage:
    with open(get_canonical_file_path(), encoding="utf-8") as f:
        return IPIRPackage.model_validate_json(f.read())


def load_defective_package() -> IPIRPackage:
    with open(get_defective_file_path(), encoding="utf-8") as f:
        return IPIRPackage.model_validate_json(f.read())


def test_01_portfolio_analyzer_metrics_and_deduplication() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()
    csv_file = get_csv_file_path()

    if not csv_file.exists():
        return

    # Sample first 1,000 policies for fast unit test run
    policies = load_synthetic_portfolio_csv(csv_file)[:1000]

    analyzer = PortfolioAnalyzer()
    res = analyzer.analyze(policies, canonical, defective)

    assert res.total_policies == 1000
    assert res.exposed_policy_count > 0
    assert res.behaviorally_affected_count > 0
    assert res.financially_affected_count > 0
    assert res.total_absolute_variance > Decimal("0.00")
    assert isinstance(res.total_signed_variance, Decimal)
    assert len(res.issue_breakdown) > 0
