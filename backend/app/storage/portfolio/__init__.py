from app.core.config import get_settings
from app.storage.portfolio.bigquery_repository import BigQueryPortfolioRepository
from app.storage.portfolio.csv_loader import load_synthetic_portfolio_csv
from app.storage.portfolio.interfaces import BasePortfolioRepository
from app.storage.portfolio.local_repository import LocalPortfolioRepository
from app.storage.portfolio.models import PortfolioPolicyRecord, PortfolioSummaryStats
from app.storage.portfolio.sql_translator import (
    ParameterizedFilter,
    translate_predicates_to_bigquery_where,
)


def get_portfolio_repository() -> BasePortfolioRepository:
    """Factory function returning the configured portfolio repository adapter."""
    settings = get_settings()
    if settings.bigquery_enabled:
        return BigQueryPortfolioRepository()
    return LocalPortfolioRepository()


__all__ = [
    "BasePortfolioRepository",
    "LocalPortfolioRepository",
    "BigQueryPortfolioRepository",
    "PortfolioPolicyRecord",
    "PortfolioSummaryStats",
    "ParameterizedFilter",
    "translate_predicates_to_bigquery_where",
    "get_portfolio_repository",
    "load_synthetic_portfolio_csv",
]
