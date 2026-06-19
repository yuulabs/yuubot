"""Tests for generated facade client error handling (ok=False raises RuntimeError)."""

from __future__ import annotations

import pytest

from yuubot.core.facade.client import render_client_module
from yuubot.core.facade.protocol import FacadeRpcRequest, FacadeRpcResponse, RpcError


# The generated _client.py imports from yb._client which doesn't exist
# in the test environment. We verify the template logic via source-level
# assertions plus end-to-end tests that inject mock modules into sys.modules.

def test_client_source_contains_ok_check() -> None:
    """The generated _client.py source includes an ok=False check that raises RuntimeError."""
    source = render_client_module()
    assert "if not response.ok:" in source
    assert 'raise RuntimeError(f"capability' in source
    assert "error.message" in source


def test_client_error_msg_includes_capability_id() -> None:
    """Verify the error message template includes the capability_id for debugging."""
    source = render_client_module()
    # The f-string should expand to: capability '...' failed: ...
    assert "{capability_id!r}" in source
    assert "failed:" in source


def test_rpc_error_message_used_in_client_error() -> None:
    """verify RpcError.message is used when present, with fallback for None."""
    # When error is None: msg = "unknown facade error"
    # When error.message is "x": msg = "x"
    source = render_client_module()
    assert '"unknown facade error"' in source
    assert "error.message" in source


async def test_facade_client_invoke_works_with_success_response() -> None:
    """End-to-end: generated invoke() succeeds when _request returns ok=True."""
    source = render_client_module(context_module="tests.test_facade_client_error_ok")

    import types
    import sys

    context_mod = types.ModuleType("tests.test_facade_client_error_ok")
    context_mod.__dict__["TOKEN"] = "test-token"
    context_mod.__dict__["ACTOR_ID"] = "test-actor"
    sys.modules["tests.test_facade_client_error_ok"] = context_mod

    yb_mod = types.ModuleType("yb")
    yb_client_mod = types.ModuleType("yb._client")

    async def mock_request(_request: FacadeRpcRequest) -> FacadeRpcResponse:
        return FacadeRpcResponse(
            ok=True,
            result={"echoed": True, "value": "hello"},
        )

    yb_client_mod.__dict__["request"] = mock_request
    yb_mod.__dict__["_client"] = yb_client_mod
    sys.modules["yb"] = yb_mod
    sys.modules["yb._client"] = yb_client_mod

    try:
        namespace: dict[str, object] = {}
        exec(source, namespace)
        invoke = namespace["invoke"]

        result = await invoke("echo.echo", {"value": "hello"})
        assert result == {"echoed": True, "value": "hello"}
    finally:
        sys.modules.pop("tests.test_facade_client_error_ok", None)
        sys.modules.pop("yb", None)
        sys.modules.pop("yb._client", None)


async def test_facade_client_invoke_raises_runtime_error_on_failure() -> None:
    """Generated invoke() raises RuntimeError when _request returns ok=False."""
    source = render_client_module(context_module="tests.test_facade_client_error_2")

    import types
    import sys

    context_mod = types.ModuleType("tests.test_facade_client_error_2")
    context_mod.__dict__["TOKEN"] = "test-token"
    context_mod.__dict__["ACTOR_ID"] = "test-actor"
    sys.modules["tests.test_facade_client_error_2"] = context_mod

    yb_mod = types.ModuleType("yb")
    yb_client_mod = types.ModuleType("yb._client")

    async def mock_request(_request: FacadeRpcRequest) -> FacadeRpcResponse:
        return FacadeRpcResponse(
            ok=False,
            error=RpcError(type="LookupError", message="capability 'echo.echo' is not provided"),
        )

    yb_client_mod.__dict__["request"] = mock_request
    yb_mod.__dict__["_client"] = yb_client_mod
    sys.modules["yb"] = yb_mod
    sys.modules["yb._client"] = yb_client_mod

    try:
        namespace: dict[str, object] = {}
        exec(source, namespace)
        invoke = namespace["invoke"]

        with pytest.raises(RuntimeError, match="capability 'echo.echo' is not provided"):
            await invoke("echo.echo", {"value": "hello"})
    finally:
        sys.modules.pop("tests.test_facade_client_error_2", None)
        sys.modules.pop("yb", None)
        sys.modules.pop("yb._client", None)


async def test_facade_client_invoke_raises_with_fallback_on_null_error() -> None:
    """Generated invoke() uses fallback message when error is None."""
    source = render_client_module(context_module="tests.test_facade_client_error_3")

    import types
    import sys

    context_mod = types.ModuleType("tests.test_facade_client_error_3")
    context_mod.__dict__["TOKEN"] = "test-token"
    context_mod.__dict__["ACTOR_ID"] = "test-actor"
    sys.modules["tests.test_facade_client_error_3"] = context_mod

    yb_mod = types.ModuleType("yb")
    yb_client_mod = types.ModuleType("yb._client")

    async def mock_request(_request: FacadeRpcRequest) -> FacadeRpcResponse:
        return FacadeRpcResponse(
            ok=False,
            error=None,
        )

    yb_client_mod.__dict__["request"] = mock_request
    yb_mod.__dict__["_client"] = yb_client_mod
    sys.modules["yb"] = yb_mod
    sys.modules["yb._client"] = yb_client_mod

    try:
        namespace: dict[str, object] = {}
        exec(source, namespace)
        invoke = namespace["invoke"]

        with pytest.raises(RuntimeError, match="unknown facade error"):
            await invoke("echo.echo", {"value": "hello"})
    finally:
        sys.modules.pop("tests.test_facade_client_error_3", None)
        sys.modules.pop("yb", None)
        sys.modules.pop("yb._client", None)
