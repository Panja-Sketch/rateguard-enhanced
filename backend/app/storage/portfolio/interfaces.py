from abc import ABC, abstractmethod

from app.engines.impact.models import ImpactPredicate
from app.engines.portfolio.models import PortfolioExposureResult
from app.storage.portfolio.models import PortfolioPolicyRecord, PortfolioSummaryStats


class BasePortfolioRepository(ABC):
    """Abstract interface for portfolio policy data access and exposure result storage."""

    @abstractmethod
    def load_policies(self, limit: int | None = None) -> list[PortfolioPolicyRecord]:
        """Loads policy records from repository."""
        pass

    @abstractmethod
    def query_impacted_policies(
        self,
        predicates: list[ImpactPredicate],
        limit: int | None = None,
    ) -> list[PortfolioPolicyRecord]:
        """Queries policies matching impact predicates."""
        pass

    @abstractmethod
    def save_exposure_result(
        self,
        result: PortfolioExposureResult,
        run_id: str,
    ) -> bool:
        """Persists portfolio financial exposure analysis result."""
        pass

    @abstractmethod
    def get_portfolio_summary(self) -> PortfolioSummaryStats:
        """Returns summary statistics for the portfolio dataset."""
        pass
