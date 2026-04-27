"""Local API clients available to Python-kernel agent functions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import attrs
import httpx

from yuubot.services.base import ServiceNotImplementedError, YuubotServiceError


class LocalApiError(YuubotServiceError):
    """Normalized local API failure."""

    code = "local_api_error"


@attrs.define(frozen=True)
class _ClientBase:
    base_url: str
    token: str = ""
    timeout_s: float = 15.0

    def _headers(self) -> dict[str, str]:
        headers = {"X-Yuubot-Agent": "1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
    ) -> Any:
        if not self.base_url:
            raise LocalApiError("local API base URL is not configured")
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_s) as client:
                response = await client.request(
                    method,
                    path,
                    json=dict(json or {}),
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise LocalApiError(str(exc)) from exc

        payload: Any
        try:
            payload = response.json()
        except ValueError:
            payload = {"text": response.text}

        if response.status_code == 501:
            detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
            raise ServiceNotImplementedError(str(detail))
        if response.status_code >= 400:
            raise LocalApiError(str(payload))
        return payload


@attrs.define(frozen=True)
class RecorderClient(_ClientBase):
    """Thin protocol client for recorder-local endpoints."""

    async def send_message(self, body: Mapping[str, Any]) -> Any:
        return await self.request("POST", "/send_msg", json=body)

    async def send_message_guaranteed(self, body: Mapping[str, Any]) -> Any:
        return await self.request("POST", "/send_msg_guaranteed", json=body)

    async def get_context(self, ctx_id: int) -> Any:
        return await self.request("GET", f"/ctx/{ctx_id}")


@attrs.define(frozen=True)
class DaemonClient(_ClientBase):
    """Thin protocol client for daemon-local service endpoints."""

    async def call_service(self, service: str, action: str, payload: Mapping[str, Any]) -> Any:
        return await self.request(
            "POST",
            f"/agent-fns/{service}/{action}",
            json=payload,
        )
