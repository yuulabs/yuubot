"""LLM provider admin routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...llm import ProviderInput, model_card_wire
from ...llm.types import ModelCardInput, ProviderSnapshot
from ..request import bad_request, read_json
from ..responses import error_response, json_response


def register_provider_routes(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/providers")
    async def api_providers() -> Response:
        items: list[ProviderSnapshot] = []
        for record in sorted(app.provider_records.values(), key=lambda item: item.id):
            cards = await app.list_model_cards(record.id)
            items.append(app.provider_snapshot(record, cards))
        return json_response({"items": items})

    @api.get("/api/providers/{provider_id}")
    async def api_provider(provider_id: str) -> Response:
        record = app.provider_records.get(provider_id)
        if record is None:
            return error_response(404, "not_found", "provider not found")
        cards = await app.list_model_cards(provider_id)
        return json_response(app.redacted_provider_detail(record, cards))

    @api.put("/api/providers/{provider_id}")
    async def api_put_provider(provider_id: str, request: Request) -> Response:
        try:
            body = await read_json(request, ProviderInput)
            await app.put_provider(provider_id, body)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            if isinstance(exc, ValueError) and "cannot change protocol" in str(exc):
                return error_response(409, "conflict", str(exc))
            return bad_request(exc)
        return json_response(await app.bootstrap_snapshot())

    @api.delete("/api/providers/{provider_id}")
    async def api_delete_provider(provider_id: str) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        try:
            await app.delete_provider(provider_id)
        except ValueError as exc:
            return error_response(409, "conflict", str(exc))
        return json_response(await app.bootstrap_snapshot())

    @api.post("/api/providers/{provider_id}/validate")
    async def api_validate_provider(provider_id: str) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        try:
            result = await app.validate_provider(provider_id)
        except Exception as exc:
            return error_response(503, "provider_unavailable", str(exc))
        return json_response(msgspec.to_builtins(result))

    @api.get("/api/providers/{provider_id}/balance")
    async def api_provider_balance(provider_id: str) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        try:
            balance = await app.provider_balance(provider_id)
        except Exception as exc:
            return error_response(503, "provider_unavailable", str(exc))
        if balance is None:
            return json_response({"available": False})
        return json_response(msgspec.to_builtins(balance))

    @api.post("/api/providers/{provider_id}/catalog/refresh")
    async def api_refresh_provider_catalog(provider_id: str) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        try:
            cards = await app.refresh_provider_catalog(provider_id)
        except Exception as exc:
            return error_response(503, "provider_unavailable", str(exc))
        return json_response({"model_cards": [model_card_wire(card) for card in cards]})

    @api.get("/api/providers/{provider_id}/model-cards")
    async def api_provider_model_cards(provider_id: str) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        cards = await app.list_model_cards(provider_id)
        return json_response({"items": [model_card_wire(card) for card in cards]})

    @api.put("/api/providers/{provider_id}/model-cards/{selector}")
    async def api_put_provider_model_card(provider_id: str, selector: str, request: Request) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        try:
            body = await read_json(request, ModelCardInput)
            if body.selector != selector:
                raise ValueError("selector in path must match body.selector")
            card = await app.put_model_card(provider_id, body)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response(model_card_wire(card))

    @api.delete("/api/providers/{provider_id}/model-cards/{selector}")
    async def api_delete_provider_model_card(provider_id: str, selector: str) -> Response:
        if provider_id not in app.provider_records:
            return error_response(404, "not_found", "provider not found")
        try:
            await app.delete_model_card(provider_id, selector)
        except ValueError as exc:
            return error_response(409, "conflict", str(exc))
        return json_response(await app.bootstrap_snapshot())
