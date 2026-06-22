"""Global daily cost ceiling for the daemon.

The guard reuses Phase 5-1's ``yuutrace.cli.db.get_usage_summary`` (with
``range="day"``) to sum ``yuu.cost`` events written to the trace DB. It is
checked at two points:

1. **send-time** (daemon send handler) — blocks new sends when the daily
   total already exceeds ``daily_limit_usd``. Returns HTTP 402 to the
   caller; no actor turn is started.
2. **per-step** (agent loop budget check) — the existing
   ``budget.is_exceeded()`` checkpoint in ``_run_agent_turn`` already
   breaks the loop as soon as the in-memory Budget crosses any of its
   unit limits. ``DailyBudgetGuard`` is additive: the send-time gate is
   the soft-stop that prevents a new turn from ever starting once the
   daily ceiling is crossed.

There is intentionally **no** 60-second polling loop and no
``actor_manager.stop()``. A mid-stream stop leaves a half-baked assistant
message and no clean frontend signal; reusing the budget checkpoint gives
the same protection at a natural break point and emits ``budget.exceeded``,
which the SSE projector already renders as an ``error`` event.
"""

from __future__ import annotations

import sqlite3


class DailyBudgetGuard:
    """Checks daily cost from ``traces.db`` against a configured limit.

    Each call opens a fresh ``sqlite3`` connection
    (``check_same_thread=False``). The guard is invoked at send-time only
    (not in the hot loop), so per-call connection cost is acceptable and
    keeps the guard stateless across daemon restarts.
    """

    def __init__(self, traces_db_path: str, daily_limit_usd: float) -> None:
        self._db_path = traces_db_path
        self._limit = float(daily_limit_usd)

    def is_exceeded(self) -> bool:
        """True when today's spend has reached the configured limit.

        ``limit <= 0`` (the default) disables the guard: it never blocks.
        """
        if self._limit <= 0:
            return False
        return self._today_cost() >= self._limit

    @property
    def limit(self) -> float:
        """The configured daily ceiling (``0`` means disabled)."""
        return self._limit

    @property
    def current_cost(self) -> float:
        """Today's total cost so far, for the send-time error payload."""
        if self._limit <= 0:
            return 0.0
        return self._today_cost()

    def _today_cost(self) -> float:
        # Imported lazily so yuutrace is only required when the guard is
        # actually consulted (skipped on the disabled / limit <= 0 fast path).
        from yuutrace.cli.db import get_usage_summary, time_range

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        # ``get_usage_summary`` indexes rows by column name (``row["cost"]``),
        # so the connection must use the ``sqlite3.Row`` row factory. The
        # daemon's own TraceService connection sets the same factory when it
        # initialises the DB; a freshly-opened guard connection does not.
        conn.row_factory = sqlite3.Row
        try:
            start_ns, end_ns = time_range("day")
            summary = get_usage_summary(conn, start_ns=start_ns, end_ns=end_ns)
            return float(summary["cost"])
        finally:
            conn.close()
