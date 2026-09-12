"""Opt-in live smoke test for the real Gemini PRIORITIZE_DIFFERENCES decision path.

This script is NEVER executed by pytest — it lives outside `tests/`, and
`pyproject.toml` scopes pytest discovery to `testpaths = ["tests"]`, so pytest
never even collects this file. It makes exactly ONE real Gemini API call using
whatever credentials are already configured in the environment (Vertex AI via
`GOOGLE_GENAI_USE_VERTEXAI=true` + Application Default Credentials, or an API
key via `GOOGLE_API_KEY`/`GEMINI_API_KEY`) through the same GeminiDecisionClient
the production supervisor uses — nothing here is a separate code path.

Usage (requires explicit opt-in — the script refuses to run without it):

    python scripts/live_smoke_gemini.py --yes-live-call

Exits 0 only when a real Gemini decision was actually applied (a successful,
schema-valid response with at least one validated selected id). Exits nonzero
for every deterministic-fallback outcome (disabled, no credentials, timeout,
quota, malformed/schema-invalid response, or a response with no valid
candidate ids) — this command's entire purpose is proving the live model path
actually works, so a silent fallback must never be reported as success.

Never prints the prompt/system-instruction text (it's synthetic and
non-sensitive here, but the point of this script is to prove wiring, not to
double as a prompt-inspection tool) and never prints a credential value —
only an `auth_mode` label ("VERTEX_AI" / "API_KEY" / "NONE").
"""

import argparse
import sys

from app.agents.config import get_agent_config
from app.agents.decision_schemas import DifferencePrioritizationDecision
from app.agents.gemini_client import AUTH_MODE_NONE, GeminiDecisionClient

# Exactly 2 small synthetic candidate diffs — Gemini may only select finding_ids
# from this fixed list, matching the real supervisor's PRIORITIZE_DIFFERENCES
# validation contract.
_SYNTHETIC_DIFFS = [
    {"finding_id": "FND-SMOKE01", "severity": "CRITICAL", "title": "Roof age factor table value changed"},
    {"finding_id": "FND-SMOKE02", "severity": "LOW", "title": "Effective date format cosmetic change"},
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--yes-live-call",
        action="store_true",
        help="Required explicit opt-in. Without this flag the script refuses to run.",
    )
    args = parser.parse_args()

    if not args.yes_live_call:
        print("Refusing to run: pass --yes-live-call to explicitly opt into one real Gemini call.")
        return 2

    config = get_agent_config()
    client = GeminiDecisionClient(config)

    if not config.agent_enabled:
        print("Refusing to run: RATEGUARD_AGENT_ENABLED is false (Gemini disabled).")
        return 2

    auth_mode, _ = client._resolve_auth_mode()
    if auth_mode == AUTH_MODE_NONE:
        print(
            "Refusing to run: no live credentials configured. Set GOOGLE_API_KEY / "
            "GEMINI_API_KEY, or GOOGLE_GENAI_USE_VERTEXAI=true with Application "
            "Default Credentials configured."
        )
        return 2

    print(f"Making exactly 1 live Gemini call (model={config.gemini_model}, auth_mode={auth_mode})...")

    candidate_ids = {d["finding_id"] for d in _SYNTHETIC_DIFFS}
    decision, evidence = client.decide(
        "PRIORITIZE_DIFFERENCES",
        DifferencePrioritizationDecision,
        system_instruction=(
            "You are the RateGuard Assurance Supervisor prioritizing which already-detected "
            "semantic pricing differences deserve focused boundary testing. You MUST only "
            "select finding_ids from the provided list — never invent a finding."
        ),
        prompt=(
            f"Deterministically-identified semantic pricing differences (JSON): {_SYNTHETIC_DIFFS}\n"
            "Select which finding_ids deserve investigative priority for boundary testing."
        ),
    )

    print("--- Live Smoke Result ---")
    print(f"model_id:            {evidence.model_id}")
    print(f"auth_mode:           {evidence.auth_mode}")
    print(f"decision_type:       {evidence.decision_type}")
    print(f"invocation_id set:   {bool(evidence.invocation_id)}")
    print(f"response_id present: {evidence.response_id is not None}")
    print(f"latency_ms:          {evidence.latency_ms}")
    print(
        "token_usage present: "
        f"{evidence.input_tokens is not None and evidence.output_tokens is not None}"
    )
    print(f"schema_valid:        {evidence.schema_valid}")
    print(f"success:             {evidence.success}")
    print(f"failure_category:    {evidence.failure_category}")

    if decision is None or not evidence.success:
        print("RESULT: deterministic fallback was used — the live model path was NOT proven.")
        return 1

    selected = [i for i in decision.selected_difference_ids if i in candidate_ids]
    print(f"selected_difference_ids (validated against allowlist): {selected}")

    if not selected:
        print("RESULT: model responded but selected no valid candidate ids — treating as failure.")
        return 1

    print("RESULT: real Gemini decision path confirmed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
