from __future__ import annotations

import asyncio

import pytest

from yuubot.runtime.streams import TextStream


@pytest.mark.asyncio
async def test_subscribe_replay_delivers_chunks_written_before_subscription() -> None:
    stream = TextStream()
    stream.write("answer\n")
    chunks: list[str] = []

    async def collect() -> None:
        async for chunk in stream.subscribe(replay=True):
            chunks.append(chunk)
            return

    await asyncio.wait_for(collect(), timeout=0.1)
    assert chunks == ["answer\n"]


@pytest.mark.asyncio
async def test_subscribe_without_replay_waits_for_future_writes() -> None:
    stream = TextStream()
    stream.write("answer\n")
    chunks: list[str] = []

    async def collect() -> None:
        async for chunk in stream.subscribe():
            chunks.append(chunk)
            return

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(collect(), timeout=0.05)
    assert chunks == []


@pytest.mark.asyncio
async def test_slow_subscriber_queue_drops_oldest_chunks() -> None:
    stream = TextStream(subscriber_queue_size=2)
    chunks: list[str] = []
    first_chunk_received = asyncio.Event()
    release_subscriber = asyncio.Event()

    async def collect() -> None:
        async for chunk in stream.subscribe():
            chunks.append(chunk)
            if len(chunks) == 1:
                first_chunk_received.set()
                await release_subscriber.wait()
            if len(chunks) == 3:
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)

    stream.write("1")
    await asyncio.wait_for(first_chunk_received.wait(), timeout=0.1)

    stream.write("2")
    stream.write("3")
    stream.write("4")
    try:
        release_subscriber.set()
        await asyncio.wait_for(task, timeout=0.1)
    finally:
        task.cancel()

    assert chunks == ["1", "3", "4"]


def test_text_stream_trims_to_max_bytes() -> None:
    stream = TextStream(16)
    stream.write("abcdefghijklmnop")
    stream.write("qrstuvwxyz")

    assert len("".join(stream.chunks).encode()) <= 16
    assert stream.tail(max_bytes=16).endswith("qrstuvwxyz")
