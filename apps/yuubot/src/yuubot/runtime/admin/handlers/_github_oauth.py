"""GitHub OAuth handlers for admin-managed integrations."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import msgspec
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from yuubot.core.integrations.impls.github.models import (
    GitHubConfig,
    GitHubOAuthTokenResponse,
)
from yuubot.core.secrets import Secret
from yuubot.resources.records import IntegrationRecord
from yuubot.resources.root import Resources
from yuubot.resources.store.models import IntegrationORM

from ._helpers import _error


@dataclass(frozen=True)
class GitHubOAuthClient:
    token_url: str
    client: httpx.AsyncClient

    async def exchange_code(
        self,
        *,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> GitHubOAuthTokenResponse:
        response = await self.client.post(
            self.token_url,
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        token = msgspec.convert(
            response.json(),
            type=GitHubOAuthTokenResponse,
            strict=False,
        )
        if token.error:
            description = token.error_description or token.error
            raise ValueError(description)
        if not token.access_token:
            raise ValueError("GitHub OAuth response did not include an access token")
        return token

    async def close(self) -> None:
        await self.client.aclose()


def _create_github_oauth_client(token_url: str) -> GitHubOAuthClient:
    return GitHubOAuthClient(
        token_url=token_url,
        client=httpx.AsyncClient(),
    )


def make_github_oauth_start_handler(*, resources: Resources):
    async def github_oauth_start(request: Request) -> Response:
        record = await _github_record(resources, request.path_params["id"])
        if record is None:
            return _error("not_found", "GitHub integration not found", 404)
        config = record.typed_config(GitHubConfig)
        if not config.client_id:
            return _error("validation_error", "GitHub OAuth client_id is not configured", 400)

        state = secrets.token_urlsafe(32)
        await resources.repository.update(
            IntegrationORM,
            record.id,
            config={**record.config, "oauth_state": Secret(state)},
        )

        redirect_uri = _callback_url(request, record.id)
        params = {
            "client_id": config.client_id,
            "redirect_uri": redirect_uri,
            "scope": config.oauth_scope,
            "state": state,
        }
        authorize_url = f"{config.oauth_authorize_url}?{urlencode(params)}"
        return RedirectResponse(authorize_url, status_code=302)

    return github_oauth_start


def make_github_oauth_callback_handler(
    *,
    resources: Resources,
    _create_oauth_client_fn=_create_github_oauth_client,
):
    async def github_oauth_callback(request: Request) -> Response:
        record = await _github_record(resources, request.path_params["id"])
        if record is None:
            return _error("not_found", "GitHub integration not found", 404)

        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")
        if not code or not state:
            return _error("validation_error", "GitHub OAuth callback missing code or state", 400)

        config = record.typed_config(GitHubConfig)
        if state != config.oauth_state.reveal():
            return _error("validation_error", "GitHub OAuth state mismatch", 400)
        if not config.client_id:
            return _error("validation_error", "GitHub OAuth client_id is not configured", 400)
        client_secret = config.client_secret.reveal()
        if not client_secret:
            return _error("validation_error", "GitHub OAuth client_secret is not configured", 400)

        oauth_client = _create_oauth_client_fn(config.oauth_access_token_url)
        try:
            token = await oauth_client.exchange_code(
                client_id=config.client_id,
                client_secret=client_secret,
                code=code,
                redirect_uri=_callback_url(request, record.id),
            )
        except ValueError as exc:
            return _error("github_oauth_failed", str(exc), 400)
        except httpx.HTTPError as exc:
            return _error("github_oauth_failed", str(exc), 502)
        finally:
            await oauth_client.close()

        await resources.repository.update(
            IntegrationORM,
            record.id,
            config={
                **record.config,
                "access_token": Secret(token.access_token),
                "oauth_state": Secret(""),
            },
        )
        return RedirectResponse(f"/integrations/{record.id}?github=connected", status_code=302)

    return github_oauth_callback


async def _github_record(
    resources: Resources,
    integration_id: str,
) -> IntegrationRecord | None:
    record = await resources.repository.get(IntegrationORM, integration_id)
    if record is None or record.name != "github":
        return None
    return record


def _callback_url(request: Request, integration_id: str) -> str:
    return str(request.url_for("github_oauth_callback", id=integration_id))
