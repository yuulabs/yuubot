"""Integration registry and built-in integration definitions."""

from .github import GitHubConfig, GitHubIntegration
from .records import IntegrationRecord
from .registry import Integration, IntegrationFactory, IntegrationRegistry, IntegrationSpec, default_registry
from .tavily_web import TavilyWebConfig, TavilyWebIntegration

__all__ = [
    "GitHubConfig",
    "GitHubIntegration",
    "Integration",
    "IntegrationFactory",
    "IntegrationRecord",
    "IntegrationRegistry",
    "IntegrationSpec",
    "TavilyWebConfig",
    "TavilyWebIntegration",
    "default_registry",
]
