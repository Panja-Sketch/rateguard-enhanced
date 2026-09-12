from pathlib import Path
from typing import Any

from app.engines.portfolio import PortfolioAnalyzer
from app.ipir.package import IPIRPackage
from app.storage.portfolio.csv_loader import load_synthetic_portfolio_csv


def analyze_portfolio_exposure_tool(
    csv_file_path: str, canonical_package_json: str, target_package_json: str
) -> dict[str, Any]:
    """Deterministic tool wrapper for 50,000 policy portfolio financial exposure analysis."""
    canonical_pkg = IPIRPackage.model_validate_json(canonical_package_json)
    target_pkg = IPIRPackage.model_validate_json(target_package_json)

    csv_path = Path(csv_file_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Portfolio CSV file not found at '{csv_file_path}'")

    policies = load_synthetic_portfolio_csv(csv_path)

    analyzer = PortfolioAnalyzer()
    res = analyzer.analyze(policies, canonical_pkg, target_pkg)

    return res.model_dump(mode="json")
