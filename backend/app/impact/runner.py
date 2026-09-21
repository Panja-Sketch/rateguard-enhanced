"""Supervisor-facing facade: runs one connector-backed impact scan for a mission."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.impact.aggregate import ImpactAggregate
from app.impact.runtime import build_coordinator, summarize_for_log
from app.ipir.package import IPIRPackage

logger = logging.getLogger(__name__)


class ConnectorImpactRunner:
    """Bound to one mission execution (tenant, attempt, liveness callback)."""

    def __init__(
        self,
        tenant_id: str,
        attempt_number: int,
        heartbeat: Callable[[], None],
        coordinator_factory: Callable[[], tuple[Any, Any]] = build_coordinator,
    ) -> None:
        self._tenant_id = tenant_id
        self._attempt = attempt_number
        self._heartbeat = heartbeat
        self._factory = coordinator_factory

    def run(
        self,
        *,
        mission,
        package: IPIRPackage,
        connector_id: str,
        engine_version: str,
        cancellation_check: Callable[[], bool],
    ) -> ImpactAggregate:
        coordinator, dispatcher = self._factory()
        try:
            job, snapshot = coordinator.plan(
                tenant_id=self._tenant_id,
                mission_id=mission.mission_id,
                attempt_number=self._attempt,
                dataset=mission.objective.portfolio_dataset,
                sample_limit=mission.objective.max_portfolio_sample_size,
                package=package,
                source_ref={
                    "source_id": mission.source_a.source_id,
                    "source_type": mission.source_a.source_type,
                },
                connector_id=connector_id,
                engine_version=engine_version,
                product=mission.objective.product,
                jurisdiction=mission.objective.jurisdiction,
            )
            aggregate = coordinator.run(
                job,
                snapshot,
                declared_input_ids={inp.id for inp in package.inputs},
                cancellation_check=cancellation_check,
                heartbeat=self._heartbeat,
            )
        finally:
            dispatcher.close()
        logger.info("IMPACT_FINISHED %s", summarize_for_log(aggregate))
        return aggregate
