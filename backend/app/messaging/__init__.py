from app.core.config import get_settings
from app.messaging.interfaces import BaseMessagePublisher
from app.messaging.memory_publisher import InMemoryPublisher
from app.messaging.models import AssuranceJob
from app.messaging.pubsub_publisher import PubSubPublisher
from app.messaging.worker import AssuranceWorker


def get_message_publisher() -> BaseMessagePublisher:
    """Factory function returning configured message publisher adapter."""
    settings = get_settings()
    if settings.execution_mode.lower() == "local" or not settings.async_enabled:
        return InMemoryPublisher()
    return PubSubPublisher()


__all__ = [
    "AssuranceJob",
    "BaseMessagePublisher",
    "InMemoryPublisher",
    "PubSubPublisher",
    "AssuranceWorker",
    "get_message_publisher",
]
