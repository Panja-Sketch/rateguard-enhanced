import sys
from pathlib import Path

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from google.api_core.exceptions import AlreadyExists  # noqa: E402
from google.cloud import pubsub_v1  # noqa: E402

from app.core.config import get_settings  # noqa: E402


def setup_pubsub() -> None:
    """Idempotently provisions Pub/Sub topic and subscription for RateGuard AI."""
    settings = get_settings()
    project_id = settings.google_cloud_project
    topic_id = settings.pubsub_topic
    subscription_id = settings.pubsub_subscription

    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()

    topic_path = publisher.topic_path(project_id, topic_id)
    subscription_path = subscriber.subscription_path(project_id, subscription_id)

    # 1. Topic setup
    try:
        publisher.create_topic(request={"name": topic_path})
        print(f"Successfully created Pub/Sub topic: '{topic_path}'")
    except AlreadyExists:
        print(f"Pub/Sub topic '{topic_path}' already exists.")

    # 2. Subscription setup
    try:
        subscriber.create_subscription(
            request={
                "name": subscription_path,
                "topic": topic_path,
                "ack_deadline_seconds": 60,
            }
        )
        print(f"Successfully created Pub/Sub subscription: '{subscription_path}'")
    except AlreadyExists:
        print(f"Pub/Sub subscription '{subscription_path}' already exists.")


if __name__ == "__main__":
    setup_pubsub()

