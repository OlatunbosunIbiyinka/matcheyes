"""Match timeline as consecutive one-minute bins: first half (with stoppage), then second half."""

from collections.abc import Sequence

from pydantic import Field

from matcheyes.domain.base import DomainModel
from matcheyes.domain.events import MatchEvent
from matcheyes.domain.time import MatchInstant, Period

BIN_MS = 60_000


class MinuteBin(DomainModel):
    index: int = Field(ge=0)
    period: Period
    minute: int = Field(ge=0, description="Whole minutes since this period's kick-off.")

    @property
    def start(self) -> MatchInstant:
        return MatchInstant(period=self.period, clock_ms=self.minute * BIN_MS)

    @property
    def label(self) -> str:
        return self.start.display_minute


class Timeline:
    """Maps events to bins. Bins cover every minute in which a period had any event."""

    def __init__(self, events: Sequence[MatchEvent]) -> None:
        last_minute: dict[Period, int] = {}
        for event in events:
            minute = event.clock_ms // BIN_MS
            last_minute[event.period] = max(last_minute.get(event.period, 0), minute)
        bins: list[MinuteBin] = []
        self._offset: dict[Period, int] = {}
        for period in sorted(last_minute):
            self._offset[period] = len(bins)
            bins.extend(
                MinuteBin(index=len(bins) + m, period=period, minute=m)
                for m in range(last_minute[period] + 1)
            )
        self.bins: tuple[MinuteBin, ...] = tuple(bins)

    def __len__(self) -> int:
        return len(self.bins)

    def index_of(self, period: Period, clock_ms: int) -> int:
        index = self._offset[period] + clock_ms // BIN_MS
        if index >= len(self.bins) or self.bins[index].period != period:
            raise ValueError(f"instant P{period} {clock_ms} ms is outside the timeline")
        return index

    def index_of_event(self, event: MatchEvent) -> int:
        return self.index_of(event.period, event.clock_ms)

    def period_start_indices(self) -> tuple[int, ...]:
        return tuple(self._offset[p] for p in sorted(self._offset))
