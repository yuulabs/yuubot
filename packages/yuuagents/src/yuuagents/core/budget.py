from __future__ import annotations

from attrs import define, field


@define
class Budget:
    """Cumulative usage tracker with per-unit limits.

    Owned by the orchestrator (yuubot), NOT by Agent.
    """

    limits: dict[str, float] = field(factory=dict)
    _usage: dict[str, float] = field(factory=dict, init=False)

    def charge(self, unit: str, amount: float) -> None:
        self._usage[unit] = self._usage.get(unit, 0.0) + amount

    def is_exceeded(self) -> bool:
        return any(self._usage.get(u, 0.0) >= limit for u, limit in self.limits.items())

    def reset_steps(self) -> None:
        self._usage.pop("steps", None)

    @property
    def usage(self) -> dict[str, float]:
        return dict(self._usage)
