"""The event log: idempotent, conflict-rejecting, with a contiguous watermark."""

import random

import pytest

from matcheyes.domain.match import EVENT_ADAPTER, ObservableMatch
from matcheyes.ingestion.log import (
    EMPTY_PREFIX_DIGEST,
    DataStatus,
    EventLog,
    IngestOutcome,
)
from matcheyes.lifecycle.snapshot import prefix_digest
from tests.support.builders import minimal_match

A = IngestOutcome


@pytest.fixture
def match() -> ObservableMatch:
    return minimal_match()


def _log(match: ObservableMatch) -> EventLog:
    return EventLog(match.info.match_id)


def test_in_order_events_advance_the_watermark(match: ObservableMatch) -> None:
    log = _log(match)
    assert [log.append(e) for e in match.events] == [A.ACCEPTED] * len(match.events)
    status = log.status()
    assert log.watermark == status.watermark == status.highest_sequence == len(match.events)
    assert status.data_status is DataStatus.CONTIGUOUS and status.missing_from is None
    assert log.events() == match.events


def test_an_exact_duplicate_is_a_no_op(match: ObservableMatch) -> None:
    log = _log(match)
    for e in match.events[:5]:
        log.append(e)
    before = (log.events(), log.digest(5))
    assert log.append(match.events[2]) is A.DUPLICATE
    assert log.append_json(EVENT_ADAPTER.dump_json(match.events[3]).decode()) is A.DUPLICATE
    assert (log.events(), log.digest(5)) == before
    assert log.status().duplicates == 2 and log.status().accepted == 5


def test_a_different_payload_under_an_existing_event_id_is_a_conflict(
    match: ObservableMatch,
) -> None:
    log = _log(match)
    for e in match.events[:5]:
        log.append(e)
    corrected = match.events[3].model_copy(update={"clock_ms": match.events[3].clock_ms + 1})
    assert log.append(corrected) is A.CONFLICT
    assert log.events()[3] == match.events[3]
    assert log.status().conflicts == 1


def test_a_reused_sequence_number_is_a_conflict(match: ObservableMatch) -> None:
    log = _log(match)
    for e in match.events[:5]:
        log.append(e)
    impostor = match.events[3].model_copy(update={"event_id": "fixture-minimal-001-new"})
    assert log.append(impostor) is A.CONFLICT
    assert log.events() == match.events[:5]


def test_events_of_another_match_and_invalid_lines_are_rejected(match: ObservableMatch) -> None:
    log = _log(match)
    assert log.append(match.events[0].model_copy(update={"match_id": "other"})) is A.REJECTED
    assert log.append_json("{not json") is A.REJECTED
    assert log.append_json('{"type": "pass"}') is A.REJECTED
    status = log.status()
    assert status.rejected == 3 and status.watermark == 0 and status.accepted == 0


def test_a_gap_holds_the_watermark_and_reports_incomplete_data(match: ObservableMatch) -> None:
    log = _log(match)
    missing = 4
    for e in match.events:
        if e.sequence != missing:
            log.append(e)
    status = log.status()
    assert status.watermark == missing - 1
    assert status.data_status is DataStatus.DATA_INCOMPLETE
    assert status.missing_from == missing
    assert status.buffered == len(match.events) - missing
    assert log.events() == match.events[: missing - 1]


def test_a_late_fill_releases_the_buffered_events(match: ObservableMatch) -> None:
    log = _log(match)
    for e in match.events:
        if e.sequence != 4:
            log.append(e)
    assert log.append(match.events[3]) is A.ACCEPTED
    assert log.watermark == len(match.events)
    assert log.status().data_status is DataStatus.CONTIGUOUS
    assert log.events() == match.events


def test_prefix_digests_do_not_depend_on_arrival_order(match: ObservableMatch) -> None:
    ordered = _log(match)
    for e in match.events:
        ordered.append(e)
    shuffled = list(match.events)
    random.Random(7).shuffle(shuffled)  # noqa: S311 - reproducible arrival order, not security
    other = _log(match)
    for e in shuffled + shuffled[:10]:
        other.append(e)
    assert other.events() == ordered.events()
    for w in range(len(match.events) + 1):
        assert other.digest(w) == ordered.digest(w) == prefix_digest(match.events[:w])
    assert ordered.digest(0) == EMPTY_PREFIX_DIGEST
    assert ordered.digest(3) != ordered.digest(4)


def test_a_digest_beyond_the_watermark_is_refused(match: ObservableMatch) -> None:
    log = _log(match)
    log.append(match.events[0])
    with pytest.raises(ValueError, match="beyond the contiguous prefix"):
        log.digest(2)
