"""Canonical snapshots: one per closed match minute of the contiguous event prefix.

A minute closes when an event of a later minute arrives, or when its period ends. Several minutes
can close at once with no events between them (a quiet spell); they share the snapshot of the
first, because their prefixes are identical. The schedule and every snapshot are therefore a pure
function of the contiguous prefix: arrival order, duplicates and delays cannot change them.

Snapshot identity is derived from the match, the watermark, the closed minute and digests of the
team sheet and of the prefix. Wall-clock time plays no part.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

from matcheyes.analytics.timeline import BIN_MS
from matcheyes.domain.entities import Identifier, MatchInfo
from matcheyes.domain.events import MatchEvent, PeriodEnd
from matcheyes.domain.match import EVENT_ADAPTER, ObservableMatch
from matcheyes.domain.time import MatchInstant, Period
from matcheyes.ingestion.log import EMPTY_PREFIX_DIGEST, chain_digest
from matcheyes.lifecycle.contracts import SnapshotHeader


@dataclass(frozen=True)
class ScheduledSnapshot:
    period: Period
    minute: int
    watermark: int


@dataclass(frozen=True)
class Snapshot:
    """A header plus the observable match it describes: only the prefix events."""

    header: SnapshotHeader
    match: ObservableMatch


class Scheduler:
    """Reads the contiguous prefix one event at a time and reports the minutes that close."""

    def __init__(self) -> None:
        self.seen = 0
        self._open: tuple[Period, int] | None = None
        self._taken = 0

    def _take(self, bin_: tuple[Period, int], watermark: int) -> list[ScheduledSnapshot]:
        if watermark <= self._taken:
            return []
        self._taken = watermark
        return [ScheduledSnapshot(bin_[0], bin_[1], watermark)]

    def feed(self, event: MatchEvent) -> list[ScheduledSnapshot]:
        closed: list[ScheduledSnapshot] = []
        bin_ = (event.period, event.clock_ms // BIN_MS)
        if self._open is not None and bin_ > self._open:
            closed += self._take(self._open, self.seen)
        if self._open is None or bin_ > self._open:
            self._open = bin_
        self.seen += 1
        if isinstance(event, PeriodEnd):
            closed += self._take(self._open, self.seen)
            self._open = None
        return closed


def snapshot_schedule(events: Sequence[MatchEvent]) -> tuple[ScheduledSnapshot, ...]:
    scheduler = Scheduler()
    return tuple(entry for event in events for entry in scheduler.feed(event))


def prefix_digest(events: Sequence[MatchEvent]) -> str:
    digest = EMPTY_PREFIX_DIGEST
    for event in events:
        digest = chain_digest(digest, EVENT_ADAPTER.dump_json(event))
    return digest


def info_digest(info: MatchInfo) -> str:
    return hashlib.sha256(info.model_dump_json().encode("utf-8")).hexdigest()


def snapshot_id(
    match_id: Identifier, entry: ScheduledSnapshot, log_digest: str, team_sheet_digest: str
) -> Identifier:
    payload = json.dumps(
        {
            "match_id": match_id,
            "watermark": entry.watermark,
            "period": entry.period,
            "minute": entry.minute,
            "log_digest": log_digest,
            "info_digest": team_sheet_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"snap-{entry.watermark:05d}-{digest[:16]}"


def build_snapshot(
    info: MatchInfo, events: Sequence[MatchEvent], entry: ScheduledSnapshot, log_digest: str
) -> Snapshot:
    """The snapshot for `entry`, from the contiguous prefix `events` (at least `entry.watermark`
    long) and the log's digest of the first `entry.watermark` events."""
    prefix = tuple(events[: entry.watermark])
    if len(prefix) != entry.watermark:
        raise ValueError("the prefix is shorter than the snapshot watermark")
    sheet = info_digest(info)
    header = SnapshotHeader(
        snapshot_id=snapshot_id(info.match_id, entry, log_digest, sheet),
        match_id=info.match_id,
        watermark=entry.watermark,
        period=entry.period,
        minute=entry.minute,
        as_of=MatchInstant(period=entry.period, clock_ms=(entry.minute + 1) * BIN_MS),
        log_digest=log_digest,
        info_digest=sheet,
    )
    return Snapshot(header=header, match=ObservableMatch(info=info, events=prefix))
