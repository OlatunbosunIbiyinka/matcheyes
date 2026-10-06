"""Canonical snapshots: one per closed minute, a pure function of the contiguous prefix."""

import pytest

from matcheyes.analytics.timeline import BIN_MS
from matcheyes.domain.events import PeriodEnd
from matcheyes.domain.match import ObservableMatch
from matcheyes.lifecycle.contracts import SnapshotHeader
from matcheyes.lifecycle.snapshot import (
    ScheduledSnapshot,
    Scheduler,
    build_snapshot,
    prefix_digest,
    snapshot_id,
    snapshot_schedule,
)
from tests.lifecycle.support import in_progress
from tests.support.builders import minimal_match
from tests.synth.generated import generated


def _bin(match: ObservableMatch, index: int) -> tuple[int, int]:
    e = match.events[index]
    return (e.period, e.clock_ms // BIN_MS)


@pytest.mark.parametrize(
    "make", [minimal_match, lambda: generated("S01_control_balanced").observable]
)
def test_one_snapshot_per_closed_minute(make: object) -> None:
    match = make()  # type: ignore[operator]
    schedule = snapshot_schedule(match.events)
    marks = [s.watermark for s in schedule]
    assert marks == sorted(set(marks))
    minutes = {(e.period, e.clock_ms // BIN_MS) for e in match.events}
    assert len(schedule) <= len(minutes)
    for entry in schedule:
        last = match.events[entry.watermark - 1]
        assert _bin(match, entry.watermark - 1) == (entry.period, entry.minute)
        closes = entry.watermark == len(match.events) or isinstance(last, PeriodEnd)
        assert closes or _bin(match, entry.watermark) > (entry.period, entry.minute)
    assert schedule[-1].watermark == len(match.events)


def test_the_schedule_of_a_prefix_is_a_prefix_of_the_schedule() -> None:
    events = in_progress().events
    full = snapshot_schedule(events)
    for cut in (1, 50, 333, len(events) // 2, len(events) - 1):
        part = snapshot_schedule(events[:cut])
        assert full[: len(part)] == part


def test_incremental_scheduling_equals_batch() -> None:
    events = in_progress().events
    scheduler = Scheduler()
    incremental = [entry for e in events for entry in scheduler.feed(e)]
    assert tuple(incremental) == snapshot_schedule(events)
    assert scheduler.seen == len(events)


def test_an_open_minute_is_not_snapshotted() -> None:
    events = in_progress().events
    last = snapshot_schedule(events)[-1]
    assert last.watermark < len(events)
    assert last.minute < events[-1].clock_ms // BIN_MS


def test_quiet_minutes_share_one_snapshot() -> None:
    match = minimal_match()
    first, second = match.events[2], match.events[3]
    jump = second.model_copy(update={"clock_ms": first.clock_ms + 5 * BIN_MS})
    events = (*match.events[:3], jump)
    schedule = snapshot_schedule(events)
    assert [s.watermark for s in schedule] == sorted({s.watermark for s in schedule})
    assert len(schedule) == len({(e.period, e.clock_ms // BIN_MS) for e in events[:3]})


def test_snapshot_identity_is_deterministic_and_content_addressed() -> None:
    match = in_progress()
    entry = snapshot_schedule(match.events)[10]
    digest = prefix_digest(match.events[: entry.watermark])
    snap = build_snapshot(match.info, match.events, entry, digest)
    again = build_snapshot(match.info, match.events, entry, digest)
    assert snap.header == again.header
    assert snap.header.snapshot_id.startswith(f"snap-{entry.watermark:05d}-")
    assert snap.match.events == match.events[: entry.watermark]
    assert snap.header.as_of.clock_ms == (entry.minute + 1) * BIN_MS
    sheet = snap.header.info_digest
    other = ScheduledSnapshot(entry.period, entry.minute + 1, entry.watermark)
    assert snapshot_id(match.info.match_id, other, digest, sheet) != snap.header.snapshot_id
    assert snapshot_id(match.info.match_id, entry, "b" * 64, sheet) != snap.header.snapshot_id
    assert snapshot_id(match.info.match_id, entry, digest, "c" * 64) != snap.header.snapshot_id


def test_a_snapshot_header_carries_no_wall_clock_or_trace() -> None:
    assert set(SnapshotHeader.model_fields) == {
        "snapshot_id",
        "match_id",
        "watermark",
        "period",
        "minute",
        "as_of",
        "log_digest",
        "info_digest",
    }


def test_a_snapshot_needs_its_whole_prefix() -> None:
    match = in_progress()
    entry = snapshot_schedule(match.events)[10]
    with pytest.raises(ValueError, match="shorter"):
        build_snapshot(match.info, match.events[:5], entry, "0" * 64)
