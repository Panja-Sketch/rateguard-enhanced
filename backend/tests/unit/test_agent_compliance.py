import pytest
from pydantic import ValidationError

from app.agents.config import AgentConfig, validate_gemini_model


def test_gemini_model_validation_rejects_pre_35_models():
    """Verifies that validate_gemini_model explicitly rejects pre-Gemini 3.5 models."""
    with pytest.raises(ValueError, match="Gemini 3.5"):
        validate_gemini_model("gemini-1.5-pro")

    with pytest.raises(ValueError, match="Gemini 3.5"):
        validate_gemini_model("gemini-2.0-flash")

    with pytest.raises(ValueError, match="Gemini 3.5"):
        validate_gemini_model("gemini-2.5-flash")


def test_gemini_model_validation_accepts_35_plus():
    """Verifies that validate_gemini_model accepts Gemini 3.5+ models."""
    assert validate_gemini_model("gemini-3.5-flash") == "gemini-3.5-flash"
    assert validate_gemini_model("gemini-3.7-flash") == "gemini-3.7-flash"
    assert validate_gemini_model("gemini-3.0-pro") == "gemini-3.0-pro"


def test_agent_config_defaults_to_gemini_37():
    """Verifies AgentConfig Pydantic model defaults to gemini-3.7-flash."""
    config = AgentConfig()
    assert config.gemini_model == "gemini-3.7-flash"


def test_agent_config_rejects_old_models():
    """Verifies AgentConfig fails validation when instantiated with pre-3.5 model."""
    with pytest.raises(ValidationError):
        AgentConfig(gemini_model="gemini-1.5-flash")
