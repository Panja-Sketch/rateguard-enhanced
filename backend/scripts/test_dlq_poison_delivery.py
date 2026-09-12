"""Explicit, destructive-to-staging-only DLQ verification.

Deliberately publishes a malformed ("poison") message to the STAGING Pub/Sub
topic (assurance-runs-staging) and confirms it is retried up to the
subscription's configured maximum delivery attempts and then forwarded to the
staging dead-letter topic (assurance-runs-staging-dlq), rather than silently
discarded.

This is intentionally kept OUT of verify_candidate.py's normal acceptance
run: it deliberately creates a message designed to fail, needs direct Pub/Sub
publish access (not just HTTP calls to the candidate API/frontend), and can
take several minutes to observe the full retry-then-DLQ cycle at the
configured backoff timing. It must never be pointed at production resources
— it hard-refuses to run against anything other than the staging topic name.

Requires the `google-cloud-pubsub` client library and real credentials for
the target project — this script DOES perform live Pub/Sub calls once
explicitly authorized; it is not a dry run.

Usage:
    python scripts/test_dlq_poison_delivery.py --yes-poison-staging-dlq \\
        --project rateguard-ai \\
        --topic assurance-runs-staging \\
        --dlq-subscription assurance-runs-staging-dlq-inspect
"""

import argparse
import sys
import time

STAGING_TOPIC_ALLOWLIST = ("assurance-runs-staging",)
STAGING_DLQ_SUBSCRIPTION_ALLOWLIST = ("assurance-runs-staging-dlq-inspect",)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes-poison-staging-dlq", action="store_true", help="Required explicit opt-in.")
    parser.add_argument("--project", required=False, default="rateguard-ai")
    parser.add_argument("--topic", required=False, default="assurance-runs-staging")
    parser.add_argument("--dlq-subscription", required=False, default="assurance-runs-staging-dlq-inspect")
    parser.add_argument("--poll-timeout-seconds", type=float, default=900.0)
    args = parser.parse_args(argv)

    if not args.yes_poison_staging_dlq:
        print("Refusing to run: pass --yes-poison-staging-dlq to explicitly opt into publishing a poison message.")
        return 2

    if args.topic not in STAGING_TOPIC_ALLOWLIST:
        print(
            f"Refusing to run: topic '{args.topic}' is not in the staging-only allowlist "
            f"{STAGING_TOPIC_ALLOWLIST}. This script will never publish to a production topic."
        )
        return 2
    if args.dlq_subscription not in STAGING_DLQ_SUBSCRIPTION_ALLOWLIST:
        print(
            f"Refusing to run: DLQ subscription '{args.dlq_subscription}' is not in the staging-only "
            f"allowlist {STAGING_DLQ_SUBSCRIPTION_ALLOWLIST}."
        )
        return 2

    try:
        from google.cloud import pubsub_v1
    except ImportError:
        print("google-cloud-pubsub is not installed; cannot publish to a real topic.")
        return 2

    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(args.project, args.topic)

    poison_payload = b"this-is-not-valid-base64-json-!!!"
    print(f"Publishing one deliberately malformed message to {topic_path} ...")
    future = publisher.publish(topic_path, poison_payload)
    message_id = future.result(timeout=30)
    print(f"Published poison message_id={message_id}. It must be rejected by the worker every delivery attempt "
          "(non-2xx, TERMINAL_INVALID_MESSAGE) and, after the subscription's configured max-delivery-attempts, "
          "forwarded to the dead-letter topic.")

    subscriber = pubsub_v1.SubscriberClient()
    dlq_sub_path = subscriber.subscription_path(args.project, args.dlq_subscription)

    print(f"Polling {dlq_sub_path} for up to {args.poll_timeout_seconds:.0f}s for the forwarded message...")
    deadline = time.monotonic() + args.poll_timeout_seconds
    found = False
    while time.monotonic() < deadline and not found:
        response = subscriber.pull(
            request={"subscription": dlq_sub_path, "max_messages": 10}, timeout=30,
        )
        for received in response.received_messages:
            if received.message.message_id == message_id or received.message.data == poison_payload:
                found = True
            subscriber.acknowledge(
                request={"subscription": dlq_sub_path, "ack_ids": [received.ack_id]},
            )
        if not found:
            time.sleep(15)

    if found:
        print("PASS: poison message was forwarded to the staging dead-letter topic as expected.")
        return 0

    print(
        "FAIL: poison message was not observed on the staging DLQ inspection subscription within the "
        "poll timeout. Check the subscription's max-delivery-attempts / ack-deadline / retry-backoff "
        "configuration and the worker's TERMINAL_INVALID_MESSAGE handling."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
