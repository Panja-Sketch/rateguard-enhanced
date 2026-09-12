import uuid

from app.engines.diff.models import SemanticDiffResult
from app.models.mission import MaterialFinding


def to_material_findings(raw_diff_result: SemanticDiffResult) -> list[MaterialFinding]:
    """Converts raw AST semantic differences into persisted MaterialFinding
    records. Shared by the eager mission pipeline (AssuranceSupervisor) and
    the on-demand Equivalence-mode alignment-options endpoint so both ever
    build findings the same way."""
    return [
        MaterialFinding(
            finding_id=f"FND-{uuid.uuid4().hex[:6].upper()}",
            category="SEMANTIC_DIFF",
            severity=diff.severity.value if hasattr(diff.severity, "value") else str(diff.severity),
            title=f"{diff.difference_type}: {diff.semantic_path}",
            description=diff.description,
            intent_value=str(diff.left_value),
            target_value=str(diff.right_value),
            affected_node=getattr(diff, "node_id", None),
        )
        for diff in raw_diff_result.differences
    ]
