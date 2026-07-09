"""Integration registry and built-in integration definitions."""

from .coding_cli import CodexConfig, CodexIntegration, OpenCodeConfig, OpenCodeIntegration
from .github import GitHubConfig, GitHubIntegration
from .records import IntegrationRecord
from .registry import Integration, IntegrationFactory, IntegrationHealth, IntegrationRegistry, IntegrationSpec, default_registry, integration_health
from .web import WebConfig, WebIntegration

__all__ = [
    "CodexConfig",
    "CodexIntegration",
    "GitHubConfig",
    "GitHubIntegration",
    "Integration",
    "IntegrationFactory",
    "IntegrationHealth",
    "IntegrationRecord",
    "IntegrationRegistry",
    "IntegrationSpec",
    "OpenCodeConfig",
    "OpenCodeIntegration",
    "WebConfig",
    "WebIntegration",
    "default_registry",
    "integration_health",
]
