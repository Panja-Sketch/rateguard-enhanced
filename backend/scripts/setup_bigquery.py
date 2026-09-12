import sys
from pathlib import Path

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from google.cloud import bigquery  # noqa: E402
from google.cloud.exceptions import NotFound  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.storage.portfolio.models import (  # noqa: E402
    EXPOSURE_RESULTS_SCHEMA,
    SYNTHETIC_POLICIES_SCHEMA,
)


def setup_bigquery() -> None:
    """Idempotently provisions BigQuery dataset and tables for RateGuard AI."""
    settings = get_settings()
    project_id = settings.google_cloud_project
    dataset_id = settings.bigquery_dataset
    location = settings.bigquery_location

    client = bigquery.Client(project=project_id)

    # 1. Dataset setup
    dataset_ref = bigquery.DatasetReference(project_id, dataset_id)
    try:
        client.get_dataset(dataset_ref)
        print(f"Dataset '{project_id}.{dataset_id}' already exists.")
    except NotFound:
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = location
        dataset.description = "RateGuard AI portfolio analytics dataset"
        client.create_dataset(dataset, timeout=30)
        print(f"Successfully created dataset '{project_id}.{dataset_id}' in location {location}.")

    # 2. Portfolio Table setup (`synthetic_policies`)
    portfolio_table_id = f"{project_id}.{dataset_id}.{settings.bigquery_portfolio_table}"
    try:
        client.get_table(portfolio_table_id)
        print(f"Table '{portfolio_table_id}' already exists.")
    except NotFound:
        table = bigquery.Table(portfolio_table_id, schema=SYNTHETIC_POLICIES_SCHEMA)
        table.description = "Deterministic synthetic portfolio policies for RateGuard analysis"
        client.create_table(table)
        print(f"Successfully created table '{portfolio_table_id}'.")

    # 3. Results Table setup (`portfolio_exposure_results`)
    results_table_id = f"{project_id}.{dataset_id}.{settings.bigquery_results_table}"
    try:
        client.get_table(results_table_id)
        print(f"Table '{results_table_id}' already exists.")
    except NotFound:
        results_table = bigquery.Table(results_table_id, schema=EXPOSURE_RESULTS_SCHEMA)
        results_table.description = "Summary financial exposure results for RateGuard assurance runs"
        client.create_table(results_table)
        print(f"Successfully created table '{results_table_id}'.")


if __name__ == "__main__":
    setup_bigquery()
