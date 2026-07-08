#!/usr/bin/env python3
"""Wait for Codex usage limits to clear, then run a /goal command."""

from __future__ import annotations

import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta

USAGE_LIMIT_RE = re.compile(
    r"you(?:'|')ve hit your usage limit",
    re.IGNORECASE,
)
RECOVERY_TIME_RE = re.compile(
    r"try again at (.+?)\.",
    re.IGNORECASE,
)
ORDINAL_RE = re.compile(r"(\d+)(?:st|nd|rd|th)", re.IGNORECASE)
FALLBACK_RETRY_SECONDS = 30 * 60
RECOVERY_JITTER_SECONDS = (5 * 60, 10 * 60)


def log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def run_codex(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["codex", *args],
        text=True,
        capture_output=True,
        check=False,
    )


def codex_status_output() -> str:
    result = run_codex(["exec", "/status"])
    chunks = [result.stdout, result.stderr]
    return "\n".join(chunk for chunk in chunks if chunk)


def has_usage_limit(output: str) -> bool:
    return bool(USAGE_LIMIT_RE.search(output))


def parse_recovery_time(output: str) -> datetime | None:
    match = RECOVERY_TIME_RE.search(output)
    if match is None:
        return None

    raw = ORDINAL_RE.sub(r"\1", match.group(1).strip())
    for fmt in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def sleep_until(target: datetime) -> None:
    now = datetime.now()
    if target <= now:
        return
    seconds = (target - now).total_seconds()
    log(f"sleeping {seconds:.0f}s until {target:%Y-%m-%d %H:%M:%S}")
    time.sleep(seconds)


def wait_for_quota() -> None:
    while True:
        log("checking codex status")
        output = codex_status_output()

        if not has_usage_limit(output):
            log("usage limit not detected; proceeding")
            return

        recovery_at = parse_recovery_time(output)
        if recovery_at is not None:
            jitter = random.uniform(*RECOVERY_JITTER_SECONDS)
            target = recovery_at + timedelta(seconds=jitter)
            log(
                "usage limit active; quota resets around "
                f"{recovery_at:%Y-%m-%d %H:%M:%S}, "
                f"will retry at {target:%Y-%m-%d %H:%M:%S}"
            )
            sleep_until(target)
            continue

        log(
            "usage limit active but recovery time not found; "
            f"retrying in {FALLBACK_RETRY_SECONDS // 60} minutes"
        )
        time.sleep(FALLBACK_RETRY_SECONDS)


def run_goal(prompt: str) -> int:
    log(f"running codex goal: {prompt!r}")
    return subprocess.run(["codex", "--yolo", "exec", f"/goal {prompt}"]).returncode


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python3 auto-retry.py <prompt>", file=sys.stderr)
        return 2

    prompt = " ".join(argv[1:])
    wait_for_quota()
    return run_goal(prompt)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
