from typing import Literal

from pydantic import Field

from matcheyes.domain.base import DomainModel

Period = Literal[1, 2]

NOMINAL_PERIOD_MS = 45 * 60 * 1000


class MatchInstant(DomainModel):
    """A point on the match clock: period plus milliseconds since that period's kick-off.

    Stoppage time is simply clock_ms beyond the nominal 45 minutes, so 45+2' in the first half
    is (period=1, clock_ms=2_820_000).
    """

    period: Period
    clock_ms: int = Field(ge=0)

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.period, self.clock_ms)

    @property
    def display_minute(self) -> str:
        """Broadcast-style minute, e.g. "63'" or "45+2'"."""
        minute = self.clock_ms // 60_000
        base = 0 if self.period == 1 else 45
        if minute >= 45:
            return f"{base + 45}+{minute - 45 + 1}'"
        return f"{base + minute + 1}'"

    @classmethod
    def at_minute(cls, minute: float) -> "MatchInstant":
        """Nominal instant for a match minute (0-90), ignoring stoppage time."""
        if not 0 <= minute <= 90:
            raise ValueError(f"minute must be within 0-90, got {minute}")
        if minute < 45:
            return cls(period=1, clock_ms=round(minute * 60_000))
        return cls(period=2, clock_ms=round((minute - 45) * 60_000))
