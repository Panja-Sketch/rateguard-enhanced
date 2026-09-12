import logging
import uuid

from app.core.config import get_settings
from app.messaging.interfaces import BaseMessagePublisher
from app.messaging.models import AssuranceJob

logger = logging.getLogger(__name__)


class InMemoryPublisher(BaseMessagePublisher):
    """In-memory message publisher for unit testing and local development."""

    def __init__(self) -> None:
        self.published_jobs: list[tuple[str, AssuranceJob]] = []

    def publish_assurance_job(self, job: AssuranceJob) -> str:
        msg_id = f"MSG-MEM-{uuid.uuid4().hex[:8].upper()}"
        self.published_jobs.append((msg_id, job))

        settings = get_settings()
        if settings.execution_mode.lower() == "local":
            logger.info("Local execution mode active: automatically processing job '%s' via AssuranceWorker.", job.job_id)
            from app.messaging.worker import AssuranceWorker

            AssuranceWorker().process_job(job)

        return msg_id
