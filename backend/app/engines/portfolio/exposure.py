from app.engines.portfolio.analyzer import PortfolioAnalyzer
from app.engines.portfolio.models import PortfolioExposureResult, SyntheticPolicy
from app.ipir.package import IPIRPackage


def aggregate_portfolio_exposure(
    policies: list[SyntheticPolicy],
    canonical_pkg: IPIRPackage,
    target_pkg: IPIRPackage,
) -> PortfolioExposureResult:
    """Helper function to aggregate portfolio exposure using PortfolioAnalyzer."""
    return PortfolioAnalyzer().analyze(policies, canonical_pkg, target_pkg)
