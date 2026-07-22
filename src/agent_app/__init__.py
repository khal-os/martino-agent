"""Namastex — a production-shaped Agno agent template."""

from ._version import __version__
from .agents import get_agent, get_agents
from .config import get_settings

__all__ = ["__version__", "get_agent", "get_agents", "get_settings"]
