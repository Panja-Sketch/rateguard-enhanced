import sys
from pathlib import Path

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.agents.config import get_agent_config  # noqa: E402


def main() -> None:
    config = get_agent_config()

    print("\n========================================================")
    print("      RateGuard Google GenAI SDK / Gemini Connectivity Test   ")
    print("========================================================\n")
    print(f"Configured Model:     {config.gemini_model}")
    print(f"Google Cloud Project: {config.google_cloud_project}")
    print("Agent Framework:      Google GenAI SDK (structured-decision supervisor)")
    print(f"Agent Enabled:        {config.agent_enabled}\n")

    try:
        from google import genai

        client = genai.Client()
        prompt = "Hello! Briefly confirm RateGuard AI agent connectivity in 1 sentence."
        response = client.models.generate_content(
            model=config.gemini_model, contents=prompt
        )

        if response and response.text:
            print("Gemini API Status:   SUCCESS")
            print(f"Response:            {response.text.strip()}")
        else:
            print("Gemini API Status:   NO_TEXT_RETURNED")
    except Exception as e:
        print("Gemini API Status:   OFFLINE / FALLBACK_MODE")
        print(f"Reason:              {e}")
        print("\nRateGuard fallback mode active: Deterministic tools handle assurance.")

    print("\n========================================================\n")


if __name__ == "__main__":
    main()

