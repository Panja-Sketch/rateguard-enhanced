"""Portfolio Blast Radius and Financial Exposure Analysis Engine."""

from app.core.config import get_data_dir
from app.engines.portfolio.analyzer import PortfolioAnalyzer
from app.engines.portfolio.errors import PortfolioAnalysisError
from app.engines.portfolio.exposure import aggregate_portfolio_exposure
from app.engines.portfolio.models import DefectExposure, PortfolioExposureResult, SyntheticPolicy
from app.engines.portfolio.predicate_evaluator import matches_predicate
from app.engines.portfolio.repricing import reprice_policy
from app.ipir.package import IPIRPackage
from app.storage.portfolio.csv_loader import load_synthetic_portfolio_csv


class PortfolioExposureAnalyzer:
    """High-level analyzer for portfolio blast radius evaluation."""

    def evaluate_portfolio(
        self,
        left_package: IPIRPackage,
        right_package: IPIRPackage,
        csv_filename: str = "az_ho3_2026_synthetic_50k.csv",
    ) -> PortfolioExposureResult:
        data_dir = get_data_dir()
        csv_path = data_dir / "portfolio" / csv_filename
        if not csv_path.exists():
            # Fallback to default filename if path missing
            csv_path = data_dir / "portfolio" / "az_ho3_2026_synthetic_50k.csv"
        policies = load_synthetic_portfolio_csv(csv_path)
        return PortfolioAnalyzer().analyze(policies, left_package, right_package)


__all__ = [
    "DefectExposure",
    "PortfolioAnalysisError",
    "PortfolioAnalyzer",
    "PortfolioExposureAnalyzer",
    "PortfolioExposureResult",
    "SyntheticPolicy",
    "aggregate_portfolio_exposure",
    "matches_predicate",
    "reprice_policy",
]
