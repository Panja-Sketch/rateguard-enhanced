from abc import ABC, abstractmethod

from app.messaging.models import AssuranceJob


class BaseMessagePublisher(ABC):
    """Abstract interface for assurance job message publishing."""

    @abstractmethod
    def publish_assurance_job(self, job: AssuranceJob) -> str:
        """Publishes an assurance job message and returns the unique published message ID."""
        pass
