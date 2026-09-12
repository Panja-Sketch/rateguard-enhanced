"""Gemini structured-decision supervisor for RateGuard AI's Mission V2
assurance pipeline (Google GenAI SDK, not Google ADK / a multi-agent
framework -- see AssuranceSupervisor's own docstring). Deterministic
engines do all arithmetic and comparison; Gemini is only ever asked a
bounded, schema-validated question at a fixed set of decision points."""

from app.agents.config import AgentConfig, get_agent_config
from app.agents.supervisor import AssuranceSupervisor

__all__ = [
    "AgentConfig",
    "AssuranceSupervisor",
    "get_agent_config",
]
