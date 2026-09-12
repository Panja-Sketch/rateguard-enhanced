from google.cloud import pubsub_v1

from app.core.config import get_settings
from app.messaging.interfaces import BaseMessagePublisher
from app.messaging.models import AssuranceJob


class PubSubPublisher(BaseMessagePublisher):
    """Production Google Cloud Pub/Sub message publisher."""

    def __init__(
        self,
        publisher_client: pubsub_v1.PublisherClient | None = None,
        project_id: str | None = None,
        topic_id: str | None = None,
    ) -> None:
        settings = get_settings()
        self.project_id = project_id or settings.google_cloud_project
        self.topic_id = topic_id or settings.pubsub_topic
        self._publisher = publisher_client
        self._topic_path = pubsub_v1.PublisherClient.topic_path(self.project_id, self.topic_id)

    @property
    def publisher(self) -> pubsub_v1.PublisherClient:
        if self._publisher is None:
            self._publisher = pubsub_v1.PublisherClient()
        return self._publisher

    def publish_assurance_job(self, job: AssuranceJob) -> str:
        data = job.model_dump_json().encode("utf-8")
        future = self.publisher.publish(
            self._topic_path,
            data,
            run_id=job.run_id,
            job_id=job.job_id,
        )
        msg_id = future.result(timeout=30)
        return str(msg_id)
