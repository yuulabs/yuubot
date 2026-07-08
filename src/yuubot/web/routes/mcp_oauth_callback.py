"""MCP OAuth browser callback route."""

from html import escape as html_escape

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

from ...app import Yuubot


def register_mcp_oauth_callback_route(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/mcp-oauth/{attempt_id}/callback", response_class=HTMLResponse)
    async def api_mcp_oauth_callback(
        attempt_id: str,
        code: str = "",
        state: str | None = None,
        error: str = "",
        token: str = "",
    ) -> Response:
        if error:
            if attempt_id in app.runtime.auth_attempts:
                await app.update_auth_attempt(attempt_id, status="failed", error=error)
            return HTMLResponse("<html><body><h1>Authorization failed</h1><p>You can close this tab.</p></body></html>", status_code=400)
        try:
            await app.complete_mcp_oauth_callback(attempt_id, code, state, token)
        except KeyError:
            return HTMLResponse("<html><body><h1>Authorization attempt not found</h1><p>You can close this tab.</p></body></html>", status_code=404)
        except ValueError as exc:
            return HTMLResponse(f"<html><body><h1>Authorization failed</h1><p>{html_escape(str(exc))}</p></body></html>", status_code=400)
        return HTMLResponse("<html><body><h1>Authorization received</h1><p>You can close this tab and return to yuubot.</p></body></html>")
