"""Tests for sandbox executor."""

import asyncio
import multiprocessing
import time

import pytest

from yuubot.sandbox.executor import (
    MAX_SOURCE_BYTES,
    SANDBOX_PROCESS_NAME,
    execute_sandbox,
)


# --- happy paths ---


def _sandbox_children() -> list[multiprocessing.Process]:
    return [
        child
        for child in multiprocessing.active_children()
        if child.name.startswith(SANDBOX_PROCESS_NAME)
    ]


@pytest.mark.asyncio
async def test_arithmetic() -> None:
    result = await execute_sandbox("return_result(2 + 3)")
    assert result.error is None
    assert result.results == ["5"]


@pytest.mark.asyncio
async def test_math_module() -> None:
    result = await execute_sandbox("return_result(math.sqrt(16))")
    assert result.error is None
    assert result.results == ["4.0"]


@pytest.mark.asyncio
async def test_re_module() -> None:
    result = await execute_sandbox(r"return_result(re.findall(r'\d+', 'a1b2c3'))")
    assert result.error is None
    assert result.results == ["['1', '2', '3']"]


@pytest.mark.asyncio
async def test_collections_counter() -> None:
    result = await execute_sandbox(
        "c = collections.Counter([1, 1, 2, 3, 3, 3])\n"
        "return_result(c.most_common(2))"
    )
    assert result.error is None
    assert result.results == ["[(3, 3), (1, 2)]"]


@pytest.mark.asyncio
async def test_itertools() -> None:
    result = await execute_sandbox(
        "return_result(list(itertools.combinations([1,2,3], 2)))"
    )
    assert result.error is None
    assert "[(1, 2)" in result.results[0]


@pytest.mark.asyncio
async def test_json_module() -> None:
    result = await execute_sandbox(
        "return_result(json.dumps({'a': 1, 'b': 2}))"
    )
    assert result.error is None
    assert '"a"' in result.results[0]


@pytest.mark.asyncio
async def test_allowed_import_works() -> None:
    result = await execute_sandbox(
        "import random\nreturn_result(1 <= random.randint(1, 6) <= 6)"
    )
    assert result.error is None
    assert result.results == ["True"]


@pytest.mark.asyncio
async def test_allowed_import_alias_works() -> None:
    result = await execute_sandbox(
        "import json as js\nreturn_result(js.dumps({'a': 1}))"
    )
    assert result.error is None
    assert result.results == ['{"a": 1}']


@pytest.mark.asyncio
async def test_statistics_module() -> None:
    result = await execute_sandbox(
        "return_result(statistics.mean([1, 2, 3, 4, 5]))"
    )
    assert result.error is None
    assert result.results == ["3"]


@pytest.mark.asyncio
async def test_funcdef() -> None:
    code = """\
def fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

return_result(fib(10))
"""
    result = await execute_sandbox(code)
    assert result.error is None
    assert result.results == ["55"]


@pytest.mark.asyncio
async def test_multiple_results() -> None:
    code = "return_result('a')\nreturn_result('b')\nreturn_result('c')"
    result = await execute_sandbox(code)
    assert result.error is None
    assert result.results == ["a", "b", "c"]


# --- stdout ---


@pytest.mark.asyncio
async def test_stdout_captured() -> None:
    result = await execute_sandbox("print('hello')\nreturn_result(42)")
    assert result.error is None
    assert result.stdout == "hello\n"
    assert result.results == ["42"]


@pytest.mark.asyncio
async def test_stdout_fallback_when_no_result() -> None:
    """stdout is captured even without return_result."""
    result = await execute_sandbox("print('hello world')")
    assert result.error is None
    assert result.stdout == "hello world\n"
    assert result.results == []


# --- errors ---


@pytest.mark.asyncio
async def test_import_denied() -> None:
    result = await execute_sandbox("import os")
    assert result.error is not None
    assert "import" in result.error.lower()


@pytest.mark.asyncio
async def test_from_import_denied() -> None:
    result = await execute_sandbox("from random import randint")
    assert result.error is not None
    assert "import" in result.error.lower()


@pytest.mark.asyncio
async def test_dunder_denied() -> None:
    result = await execute_sandbox("x = ().__class__")
    assert result.error is not None
    assert "dunder" in result.error.lower()


@pytest.mark.asyncio
async def test_runtime_exception() -> None:
    result = await execute_sandbox("return_result(1 / 0)")
    assert result.error is not None
    assert "ZeroDivisionError" in result.error


@pytest.mark.asyncio
async def test_source_too_large() -> None:
    code = "x = 1\n" * (MAX_SOURCE_BYTES // 4)
    result = await execute_sandbox(code)
    assert result.error is not None
    assert "too large" in result.error


@pytest.mark.asyncio
async def test_timeout() -> None:
    code = "while True: pass"
    result = await execute_sandbox(code, timeout=0.5)
    assert result.error is not None
    assert "timed out" in result.error


@pytest.mark.asyncio
async def test_timeout_reaps_child_process() -> None:
    before = {child.pid for child in _sandbox_children()}

    result = await execute_sandbox("while True: pass", timeout=0.2)

    assert result.error is not None
    assert "timed out" in result.error

    leaked: list[multiprocessing.Process] = []
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        leaked = [
            child
            for child in _sandbox_children()
            if child.pid not in before and child.is_alive()
        ]
        if not leaked:
            break
        await asyncio.sleep(0.05)

    assert not leaked


def test_pending_task_does_not_block_asyncio_shutdown() -> None:
    before = {child.pid for child in _sandbox_children()}

    async def main() -> None:
        asyncio.create_task(execute_sandbox("while True: pass", timeout=5.0))
        await asyncio.sleep(0.1)

    started = time.monotonic()
    asyncio.run(main())
    elapsed = time.monotonic() - started

    assert elapsed < 2.0

    leaked: list[multiprocessing.Process] = []
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        leaked = [
            child
            for child in _sandbox_children()
            if child.pid not in before and child.is_alive()
        ]
        if not leaked:
            break
        time.sleep(0.05)

    assert not leaked


@pytest.mark.asyncio
async def test_forbidden_call_open() -> None:
    result = await execute_sandbox("open('/etc/passwd')")
    assert result.error is not None
    assert "forbidden" in result.error.lower() or "open" in result.error.lower()


@pytest.mark.asyncio
async def test_syntax_error() -> None:
    result = await execute_sandbox("def (oops")
    assert result.error is not None
    assert "syntax" in result.error.lower()
