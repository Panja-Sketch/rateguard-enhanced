"""Batch dispatch: Pub/Sub in deployed environments, a bounded thread pool in
local mode and tests. Both invoke the very same idempotent `BatchProcessor`."""

from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

logger = logging.getLogger(__name__)

IMPACT_TOPIC_DEFAULT = "impact-batches"


class ImpactBatchMessage(BaseModel):
    """Pub/Sub payload: opaque references only - never rows, inputs or premiums."""

    schema_version: int = 1
    tenant_id: str
    job_id: str
    batch_no: int


class BatchDispatcher(ABC):
    @abstractmethod
    def dispatch(self, tenant_id: str, job_id: str, batch_no: int) -> None: ...

    def close(self) -> None:  # pragma: no cover - trivial
        return None


class PubSubBatchDispatcher(BatchDispatcher):
    def __init__(self, project_id: str, topic_id: str = IMPACT_TOPIC_DEFAULT, publisher=None) -> None:
        from google.cloud import pubsub_v1

        self._publisher = publisher or pubsub_v1.PublisherClient()
        self._topic_path = self._publisher.topic_path(project_id, topic_id)

    def dispatch(self, tenant_id: str, job_id: str, batch_no: int) -> None:
        msg = ImpactBatchMessage(tenant_id=tenant_id, job_id=job_id, batch_no=batch_no)
        future = self._publisher.publish(
            self._topic_path, msg.model_dump_json().encode("utf-8"), job_id=job_id, batch_no=str(batch_no)
        )
        future.result(timeout=30)


class LocalBatchDispatcher(BatchDispatcher):
    """Runs batches on a bounded thread pool using the same processor."""

    def __init__(self, process_fn, max_workers: int) -> None:
        self._process = process_fn
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="impact-batch")

    def dispatch(self, tenant_id: str, job_id: str, batch_no: int) -> None:
        owner = f"local-{uuid.uuid4().hex[:8]}"

        def _run() -> None:
            try:
                self._process(tenant_id, job_id, batch_no, owner)
            except Exception:  # noqa: BLE001 - a failed batch is re-dispatched by the coordinator
                logger.exception("IMPACT_LOCAL_BATCH_FAILED job=%s batch=%s", job_id, batch_no)

        self._pool.submit(_run)

    def close(self) -> None:
        self._pool.shutdown(wait=True)


def decode_batch_message(raw: bytes) -> ImpactBatchMessage:
    return ImpactBatchMessage.model_validate(json.loads(raw.decode("utf-8")))
