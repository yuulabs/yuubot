"""Benchmark sandbox latency and verify shutdown does not hang.

Historically this reproduced a shutdown hang caused by cancelling an
in-flight ``execute_sandbox()`` call while it was blocked inside
``asyncio.to_thread``. The fix moved the wait path into cancellable async
polling, so the process should now exit promptly after ``main()`` returns.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time

from yuubot.sandbox.executor import execute_sandbox

BENCH_CODE = "return_result(sum(range(10000)))"
HANG_CODE = "while True: pass"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure sandbox latency, then verify that shutdown stays responsive "
            "even with a pending sandbox task."
        )
    )
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument(
        "--hang-timeout",
        type=float,
        default=3600.0,
        help="Timeout passed into the final hanging sandbox call.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=0.2,
        help="How long to wait so the hanging background call definitely starts.",
    )
    parser.add_argument(
        "--no-hang",
        action="store_true",
        help="Run the benchmark only and exit normally.",
    )
    return parser.parse_args()


async def bench_latency(warmup: int, iterations: int) -> list[float]:
    samples: list[float] = []
    total = warmup + iterations
    for index in range(total):
        started = time.perf_counter()
        result = await execute_sandbox(BENCH_CODE, timeout=5.0)
        elapsed = time.perf_counter() - started
        if result.error:
            raise RuntimeError(f"benchmark failed at run {index + 1}: {result.error}")
        if index >= warmup:
            samples.append(elapsed)
            print(
                f"bench {index - warmup + 1}/{iterations}: {elapsed * 1000:.2f} ms",
                flush=True,
            )
    return samples


def summarize(samples: list[float]) -> dict[str, float]:
    ms = sorted(sample * 1000 for sample in samples)
    return {
        "mean_ms": statistics.mean(ms),
        "median_ms": statistics.median(ms),
        "min_ms": ms[0],
        "max_ms": ms[-1],
    }


async def trigger_shutdown_hang(hang_timeout: float, settle_seconds: float) -> None:
    print(
        "starting hanging sandbox call: "
        f"code={HANG_CODE!r} timeout={hang_timeout}s",
        flush=True,
    )
    task = asyncio.create_task(execute_sandbox(HANG_CODE, timeout=hang_timeout))
    await asyncio.sleep(settle_seconds)
    print(
        "benchmark finished; returning from main now. "
        "If the shutdown bug is fixed, the process should exit promptly.",
        flush=True,
    )
    print(f"pending task done? {task.done()}", flush=True)


async def amain() -> None:
    args = parse_args()
    print(f"pid={os.getpid()}", flush=True)
    print(
        f"warmup={args.warmup} iterations={args.iterations} "
        f"hang_timeout={args.hang_timeout}s no_hang={args.no_hang}",
        flush=True,
    )

    stats = summarize(await bench_latency(args.warmup, args.iterations))
    print(
        "summary: "
        + ", ".join(f"{key}={value:.2f}" for key, value in stats.items()),
        flush=True,
    )

    if args.no_hang:
        print("benchmark-only mode; exiting normally.", flush=True)
        return

    await trigger_shutdown_hang(args.hang_timeout, args.settle_seconds)


if __name__ == "__main__":
    asyncio.run(amain())
