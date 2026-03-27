"""Tests for the sandbox_python tool function."""

import pytest

from yuubot.sandbox.tool import sandbox_python


@pytest.mark.asyncio
async def test_tool_happy_path() -> None:
    result = await sandbox_python.fn(code="return_result(2 ** 10)")
    assert "1024" in result
    assert "[ERROR]" not in result


@pytest.mark.asyncio
async def test_tool_allowed_import_works() -> None:
    result = await sandbox_python.fn(code="import math\nreturn_result(math.sqrt(81))")
    assert "9.0" in result
    assert "[ERROR]" not in result


@pytest.mark.asyncio
async def test_tool_error_returns_string() -> None:
    result = await sandbox_python.fn(code="import os")
    assert isinstance(result, str)
    assert "[ERROR]" in result


@pytest.mark.asyncio
async def test_tool_no_result() -> None:
    result = await sandbox_python.fn(code="x = 1")
    assert "[ERROR]" in result
    assert "return_result" in result


@pytest.mark.asyncio
async def test_tool_multiple_results() -> None:
    result = await sandbox_python.fn(code="return_result('a')\nreturn_result('b')")
    assert "[result 1]" in result
    assert "[result 2]" in result


@pytest.mark.asyncio
async def test_tool_stdout_only() -> None:
    result = await sandbox_python.fn(code="print('hello')")
    assert "[stdout]" in result
    assert "hello" in result
