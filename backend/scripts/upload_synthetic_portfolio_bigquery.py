import argparse
import csv
import sys
from decimal import Decimal
from pathlib import Path

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from google.cloud import bigquery  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.storage.portfolio.models import SYNTHETIC_POLICIES_SCHEMA  # noqa: E402


def upload_portfolio(replace_demo_data: bool = False) -> None:
    """Reads existing seed-42 50K CSV and uploads it to BigQuery using exact Decimal conversion and explicit schema."""
    settings = get_settings()
    project_id = settings.google_cloud_project
    dataset_id = settings.bigquery_dataset
    table_name = settings.bigquery_portfolio_table
    table_fqn = f"{project_id}.{dataset_id}.{table_name}"

    root_dir = Path(__file__).resolve().parent.parent.parent
    csv_file = root_dir / "data" / "portfolio" / "az_ho3_2026_synthetic_50k.csv"

    if not csv_file.exists():
        print(f"Error: CSV portfolio file not found at {csv_file}")
        sys.exit(1)

    client = bigquery.Client(project=project_id)

    # Check current row count and enforce strict duplicate / partial-count guards
    check_query = f"SELECT COUNT(1) as cnt FROM `{table_fqn}`"
    current_count = 0
    try:
        cnt_res = list(client.query(check_query).result())[0]["cnt"]
        current_count = int(cnt_res)
    except Exception:
        current_count = 0

    if current_count == 50000 and not replace_demo_data:
        print(f"Table `{table_fqn}` already contains {current_count:,} records. Upload skipped.")
        return

    if current_count > 0 and current_count != 50000 and not replace_demo_data:
        print(
            f"Error: Table `{table_fqn}` contains an unexpected partial count of {current_count:,} records. "
            "Upload stopped to prevent duplicate or corrupted data. Use --replace-demo-data to truncate and reload."
        )
        sys.exit(1)

    rows_to_insert = []
    with open(csv_file, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Convert string directly to Python Decimal without intermediate float conversion
            raw_prem_str = str(row["canonical_premium"]).strip()
            prem_dec = Decimal(raw_prem_str)

            # Enforce pre-upload monetary scale validation (scale <= 2)
            if prem_dec.as_tuple().exponent < -2:
                raise ValueError(
                    f"Over-precision monetary error in source data for policy '{row.get('policy_id')}': "
                    f"canonical_premium '{raw_prem_str}' has scale > 2. Upload stopped."
                )

            rows_to_insert.append(
                {
                    "policy_id": row["policy_id"],
                    "product_id": row["product_id"],
                    "state": row["state"],
                    "form": row["form"],
                    "transaction_type": row["transaction_type"],
                    "effective_date": row["effective_date"],
                    "territory": row["territory"],
                    "roof_age": int(row["roof_age"]),
                    "deductible": int(row["deductible"]),
                    "protection_class": int(row["protection_class"]),
                    "construction_type": row["construction_type"],
                    "dwelling_limit": int(row["dwelling_limit"]),
                    "multi_policy": str(row["multi_policy"]).lower() in ("true", "1"),
                    "claims_free": str(row["claims_free"]).lower() in ("true", "1"),
                    "claims_free_years": int(row.get("claims_free_years", 3)),
                    "canonical_premium": str(prem_dec),
                }
            )

    print(f"Loaded {len(rows_to_insert):,} policies from CSV. Starting BigQuery load job...")

    # Explicit schema definition prevents BigQuery JSON auto-inference from altering NUMERIC to FLOAT
    job_config = bigquery.LoadJobConfig(
        schema=SYNTHETIC_POLICIES_SCHEMA,
        write_disposition=(
            bigquery.WriteDisposition.WRITE_TRUNCATE
            if replace_demo_data
            else bigquery.WriteDisposition.WRITE_EMPTY
        ),
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )

    load_job = client.load_table_from_json(rows_to_insert, table_fqn, job_config=job_config)
    load_job.result()  # Wait for job to complete

    table = client.get_table(table_fqn)
    print(f"Successfully loaded {table.num_rows:,} records into `{table_fqn}`.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload synthetic 50K portfolio to BigQuery")
    parser.add_argument(
        "--replace-demo-data",
        action="store_true",
        help="Truncates existing table before uploading synthetic portfolio data",
    )
    args = parser.parse_args()
    upload_portfolio(replace_demo_data=args.replace_demo_data)


if __name__ == "__main__":
    main()
