from app.connectors.errors import ConnectorException
from app.connectors.registry import select_connector
from app.models.mission import (
    AssuranceMission,
    ComparisonMode,
    ValidationIssue,
)


class MissionValidationService:
    """Production-grade validator for sources and assurance missions."""

    @staticmethod
    def validate_mission(mission: AssuranceMission) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if not mission.name.strip():
            issues.append(
                ValidationIssue(
                    field="name",
                    code="REQUIRED",
                    message="Mission name is required.",
                )
            )

        if not mission.objective.product.strip():
            issues.append(
                ValidationIssue(
                    field="product",
                    code="REQUIRED",
                    message="Target insurance product is required.",
                )
            )

        if not mission.objective.jurisdiction.strip():
            issues.append(
                ValidationIssue(
                    field="jurisdiction",
                    code="REQUIRED",
                    message="Target jurisdiction is required.",
                )
            )

        # Mode-specific source requirements
        if mission.mode == ComparisonMode.RELEASE_CONFORMANCE:
            if not mission.source_a or not mission.source_a.source_id:
                issues.append(
                    ValidationIssue(
                        field="source_a",
                        code="REQUIRED",
                        message="Authoritative Pricing Intent (Source A) is required for Release Conformance mode.",
                    )
                )
            if not mission.source_b or not mission.source_b.source_id:
                issues.append(
                    ValidationIssue(
                        field="source_b",
                        code="REQUIRED",
                        message="Target Rating Implementation (Source B) is required for Release Conformance mode.",
                    )
                )

        elif mission.mode == ComparisonMode.EQUIVALENCE:
            if not mission.source_a or not mission.source_a.source_id:
                issues.append(
                    ValidationIssue(
                        field="source_a",
                        code="REQUIRED",
                        message="Source A must be explicitly selected for Equivalence comparison mode.",
                    )
                )
            if not mission.source_b or not mission.source_b.source_id:
                issues.append(
                    ValidationIssue(
                        field="source_b",
                        code="REQUIRED",
                        message="Source B must be explicitly selected for Equivalence comparison mode.",
                    )
                )

        # Connector-backed Source B: fail closed at mission-create time,
        # never at execution time. A mission may never carry an arbitrary
        # URL — only a connector_id/engine_version pair that is actually
        # registered (app.connectors.registry.select_connector).
        if mission.source_b is not None and mission.source_b.source_type == "API_CONNECTOR":
            connector_id = mission.source_b.connector_id
            engine_version = mission.source_b.engine_version
            if not connector_id or not engine_version:
                issues.append(
                    ValidationIssue(
                        field="source_b",
                        code="CONNECTOR_SELECTION_REQUIRED",
                        message="A connector-backed Source B requires both connector_id and engine_version.",
                    )
                )
            else:
                try:
                    select_connector(connector_id, engine_version)
                except ConnectorException as exc:
                    issues.append(
                        ValidationIssue(
                            field="source_b",
                            code=exc.error.code,
                            message=exc.error.message,
                        )
                    )

        return issues

