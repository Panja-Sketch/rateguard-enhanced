from typing import Any, NamedTuple

from app.engines.impact.models import ImpactPredicate

# Supported SQL-safe field mapping
ALLOWED_FIELDS: dict[str, str] = {
    "policy_id": "STRING",
    "product_id": "STRING",
    "state": "STRING",
    "form": "STRING",
    "transaction_type": "STRING",
    "effective_date": "DATE",
    "territory": "STRING",
    "roof_age": "INT64",
    "deductible": "INT64",
    "protection_class": "INT64",
    "construction_type": "STRING",
    "dwelling_limit": "INT64",
    "multi_policy": "BOOL",
    "claims_free": "BOOL",
    "claims_free_years": "INT64",
    "canonical_premium": "NUMERIC",
}

# Operator mapping
OPERATOR_MAP: dict[str, str] = {
    "EQ": "=",
    "==": "=",
    "NE": "!=",
    "!=": "!=",
    "GT": ">",
    ">": ">",
    "GTE": ">=",
    ">=": ">=",
    "LT": "<",
    "<": "<",
    "LTE": "<=",
    "<=": "<=",
}


class ParameterizedFilter(NamedTuple):
    """Container for parameterized SQL snippet and positional BigQuery parameters."""

    where_clause: str
    query_params: list[dict[str, Any]]  # Maps name, type, value for ScalarQueryParameter


def translate_predicates_to_bigquery_where(
    predicates: list[ImpactPredicate],
    param_prefix: str = "p",
) -> ParameterizedFilter:
    """Translates a list of ImpactPredicate models into a safe parameterized BigQuery WHERE clause.

    Prevent SQL injection by strictly validating field names, mapping operators,
    and outputting ScalarQueryParameter specifications.
    """
    if not predicates:
        return ParameterizedFilter(where_clause="", query_params=[])

    predicate_clauses: list[str] = []
    params: list[dict[str, Any]] = []
    param_counter = 1

    for pred in predicates:
        clause_parts: list[str] = []
        for clause in pred.clauses:
            field = clause.field
            if field not in ALLOWED_FIELDS:
                continue

            op_str = (
                clause.operator.value if hasattr(clause.operator, "value") else str(clause.operator)
            )
            sql_op = OPERATOR_MAP.get(op_str)
            if not sql_op:
                continue

            param_name = f"{param_prefix}_{param_counter}"
            param_counter += 1

            field_type = ALLOWED_FIELDS[field]
            val = clause.value

            # Format value for query parameter
            if field_type == "DATE" and hasattr(val, "isoformat"):
                val = val.isoformat()

            clause_parts.append(f"{field} {sql_op} @{param_name}")
            params.append(
                {
                    "name": param_name,
                    "type": field_type,
                    "value": val,
                }
            )

        if clause_parts:
            # Combine clauses within an ImpactPredicate with AND
            predicate_clauses.append(f"({' AND '.join(clause_parts)})")

    if not predicate_clauses:
        return ParameterizedFilter(where_clause="", query_params=[])

    # Combine distinct ImpactPredicates with OR
    where_sql = f"WHERE {' OR '.join(predicate_clauses)}"
    return ParameterizedFilter(where_clause=where_sql, query_params=params)
