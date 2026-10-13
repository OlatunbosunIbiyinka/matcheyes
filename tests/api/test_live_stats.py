"""Snapshot statistics on the live stream (ADR-0016 Step 2): ordering, anchoring, editions,
caching and failure, with the cues exactly as they were."""

import json
from functools import cache, partial
from typing import Any

import pytest

from matcheyes.api import live as live_module
from matcheyes.api.live import LiveMatch, Message, StatsCache, prefix_matches
from matcheyes.broadcast.compiler import compile_timeline
from matcheyes.broadcast.stats import (
    ANALYTICS_ERROR,
    NOT_ENOUGH_DATA,
    STATS_VERSION,
    SnapshotStats,
    snapshot_stats,
    stats_message,
)
from matcheyes.domain.match import ObservableMatch
from matcheyes.lifecycle.contracts import SnapshotOutcome
from matcheyes.lifecycle.evaluate import SnapshotEvaluation
from matcheyes.lifecycle.snapshot import Snapshot
from tests.api.support import app, request, serving, sse
from tests.broadcast.test_stats import corrupted, snapshots
from tests.lifecycle.support import evaluator, in_progress, reference

STATS_KEYS = {"edition", "stats_version", "match_id", "snapshot_id", "watermark", "period"}
STATS_KEYS |= {"minute", "as_of", "clock_ms", "status", "reason", "measures", "teams"}


def replayed(match: ObservableMatch | None = None, **options: Any) -> LiveMatch:
    live = LiveMatch(match or in_progress(), speed=0, loop=False, evaluator=evaluator(), **options)
    live._replay(0)
    return live


@cache
def integrated() -> LiveMatch:
    return replayed()


@cache
def baseline() -> LiveMatch:
    return replayed(stats=False)


def messages(live: LiveMatch) -> list[Message]:
    return next(iter(live.edition.surfaces.values())).messages


def payloads(live: LiveMatch) -> list[dict[str, Any]]:
    return [json.loads(m.data) for m in messages(live) if m.event == "stats"]


# --- ordering and anchoring -----------------------------------------------------------------


def test_each_ingest_publishes_its_clock_then_one_stats_per_new_snapshot_then_its_cues() -> None:
    for s in integrated().edition.surfaces.values():
        assert [m.index for m in s.messages] == list(range(len(s.messages)))
        previous = None
        for m in s.messages:
            if m.event == "stats":
                assert previous in ("clock", "stats")
            previous = m.event
        for i, m in enumerate(s.messages):
            if m.event == "clock":
                group = []
                for following in s.messages[i + 1 :]:
                    if following.event != "stats":
                        break
                    group.append(json.loads(following.data))
                assert group, "a tick without statistics"
                assert group[-1]["snapshot_id"] == json.loads(m.data)["snapshot_id"]


def test_there_is_exactly_one_stats_message_per_snapshot_in_snapshot_order() -> None:
    headers = [r.header for r in reference().state.snapshots]
    served = payloads(integrated())
    assert [p["snapshot_id"] for p in served] == [h.snapshot_id for h in headers]
    for p, h in zip(served, headers, strict=True):
        assert set(p) == STATS_KEYS
        assert (p["edition"], p["stats_version"], p["match_id"]) == (0, STATS_VERSION, h.match_id)
        assert p["watermark"] == h.watermark and p["minute"] == h.label
        assert p["period"] == h.as_of.period and p["clock_ms"] == h.as_of.clock_ms
        assert p["as_of"] == h.as_of.display_minute


def test_served_statistics_equal_the_offline_statistics_of_each_snapshot() -> None:
    offline = [stats_message(0, snapshot_stats(s)) for s in snapshots(in_progress())]
    assert [m.data for m in messages(integrated()) if m.event == "stats"] == offline


def test_every_surface_carries_byte_identical_statistics() -> None:
    surfaces = list(integrated().edition.surfaces.values())
    assert len(surfaces) == 6
    first = [m.data for m in surfaces[0].messages if m.event == "stats"]
    for s in surfaces[1:]:
        assert [m.data for m in s.messages if m.event == "stats"] == first


def test_cues_and_ticks_are_exactly_as_without_statistics() -> None:
    with_stats, without = integrated().edition.surfaces, baseline().edition.surfaces
    for key in with_stats:
        kept = [(m.event, m.data) for m in with_stats[key].messages if m.event != "stats"]
        assert kept == [(m.event, m.data) for m in without[key].messages]
        offline = compile_timeline(
            in_progress().info,
            reference().log.events(),
            reference().state,
            integrated().profiles[key],
        )
        cues = [m.data for m in with_stats[key].messages if m.event == "cue"]
        assert cues == [c.model_dump_json() for c in offline.cues]
    assert not any(m.event == "stats" for m in messages(baseline()))
    assert baseline().stats_cache is None


def test_served_values_are_null_with_not_enough_data_below_the_sample_or_when_undefined() -> None:
    found = {"on_ball_share": 0, "ppda": 0}
    for p in payloads(integrated()):
        for team in p["teams"]:
            for key in found:
                v = team["values"][key]
                if v["value"] is None:
                    assert v["display"] == NOT_ENOUGH_DATA
                    assert v["status"] == "insufficient_sample"
                    found[key] += 1
    assert all(found.values()), found


def test_served_statistics_round_trip_and_carry_no_insight_fields() -> None:
    for p in payloads(integrated()):
        stats = SnapshotStats.model_validate({k: v for k, v in p.items() if k != "edition"})
        assert stats_message(0, stats) == json.dumps(p, separators=(",", ":"), ensure_ascii=False)
    text = " ".join(m.data for m in messages(integrated()) if m.event == "stats")
    for internal in ("narrative", "storyline_id", "verdict", "insight_id", "evidence", "trace"):
        assert f'"{internal}"' not in text


# --- editions and cache -----------------------------------------------------------------------


def test_a_restarted_edition_republishes_identical_statistics_from_the_cache() -> None:
    live = replayed()
    assert live.stats_cache is not None
    built = live.stats_cache.calls
    assert built == len(reference().state.snapshots) and live.stats_cache.hits == 0
    first = payloads(live)
    live._replay(1)
    assert live.edition.number == 1
    assert (live.stats_cache.calls, live.stats_cache.hits) == (built, built)
    again = payloads(live)
    assert [p["edition"] for p in again] == [1] * len(again)
    assert [p | {"edition": 0} for p in again] == first
    kept = [(m.event, m.index) for m in messages(live)]
    assert kept == [(m.event, m.index) for m in messages(integrated())]


def test_cached_statistics_carry_no_edition() -> None:
    assert "edition" not in SnapshotStats.model_fields
    live = integrated()
    assert live.stats_cache is not None
    assert all(isinstance(s, SnapshotStats) for s in live.stats_cache.cache.values())


def test_a_disabled_cache_builds_every_time_and_publishes_the_same_messages() -> None:
    live = LiveMatch(in_progress(), speed=0, loop=False, evaluator=evaluator())
    live.stats_cache = StatsCache(limit=0)
    live._replay(0)
    live._replay(1)
    snapshots_count = len(reference().state.snapshots)
    assert (live.stats_cache.calls, live.stats_cache.hits) == (2 * snapshots_count, 0)
    assert live.stats_cache.cache == {}
    assert [p | {"edition": 0} for p in payloads(live)] == payloads(integrated())


def test_the_stats_cache_is_keyed_by_snapshot_bounded_and_checks_what_it_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[str] = []
    stats = payloads(integrated())[:3]
    by_id = {p["snapshot_id"]: p for p in stats}

    def builder(snapshot: Snapshot) -> SnapshotStats:
        sid = snapshot.header.snapshot_id
        built.append(sid)
        return SnapshotStats.model_validate({k: v for k, v in by_id[sid].items() if k != "edition"})

    monkeypatch.setattr(live_module, "STATS_CACHE_LIMIT", 2)
    cache_ = StatsCache(builder)
    snaps = {s.header.snapshot_id: s for s in snapshots(in_progress())}
    a, b, c = (p["snapshot_id"] for p in stats)
    for sid in (a, a, b, c, a):
        assert cache_(sid, partial(snaps.__getitem__, sid)).snapshot_id == sid
    assert built == [a, b, c, a] and len(cache_.cache) <= 2
    assert (cache_.calls, cache_.hits) == (4, 1)
    with pytest.raises(ValueError, match="another snapshot"):
        cache_("not-" + a, lambda: snaps[a])


def test_stats_and_editions_survive_the_server_resuming_mid_statistics() -> None:
    with serving(app()) as address:
        path = f"/matches/{in_progress().info.match_id}/stream?audience=fan"
        full = [m for m in sse(request(address, path)[2]) if "id" in m]
        at = next(i for i, m in enumerate(full) if m["event"] == "stats")
        resumed = sse(request(address, path, headers={"Last-Event-ID": full[at]["id"]})[2])
    assert [m for m in resumed if "id" in m] == full[at + 1 :]
    assert [m["id"] for m in full] == [f"0:{i}" for i in range(len(full))]
    assert sum(m["event"] == "stats" for m in full) == len(reference().state.snapshots)


# --- failure ----------------------------------------------------------------------------------


def _unavailable(live: LiveMatch) -> None:
    served = payloads(live)
    assert len(served) == len(reference().state.snapshots)
    for p in served:
        assert (p["status"], p["reason"], p["teams"]) == ("unavailable", ANALYTICS_ERROR, [])
    expected = [p["snapshot_id"] for p in payloads(integrated())]
    assert [p["snapshot_id"] for p in served] == expected


def _cues(live: LiveMatch) -> list[str]:
    return [m.data for m in messages(live) if m.event != "stats"]


def test_an_analytics_exception_is_served_as_analytics_error_without_its_text() -> None:
    def boom(snapshot: Snapshot) -> SnapshotStats:
        raise RuntimeError("secret detail from /home/someone")

    live = LiveMatch(in_progress(), speed=0, loop=False, evaluator=evaluator())
    live.stats_cache = StatsCache(boom)
    live._replay(0)
    _unavailable(live)
    text = " ".join(m.data for m in messages(live))
    assert "secret" not in text and "RuntimeError" not in text and "someone" not in text
    assert _cues(live) == _cues(baseline())


def test_statistics_for_another_snapshot_are_never_served() -> None:
    first = payloads(integrated())[0]
    wrong = SnapshotStats.model_validate({k: v for k, v in first.items() if k != "edition"})
    live = LiveMatch(in_progress(), speed=0, loop=False, evaluator=evaluator())
    live.stats_cache = StatsCache(lambda snapshot: wrong)
    live._replay(0)
    served = payloads(live)
    assert served[0]["status"] == "valid"
    assert all(p["status"] == "unavailable" for p in served[1:])
    assert _cues(live) == _cues(baseline())


def test_a_prefix_that_no_longer_matches_its_header_is_not_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(live_module, "prefix_matches", lambda log, header: False)
    live = replayed()
    _unavailable(live)
    assert live.stats_cache is not None and live.stats_cache.calls == 0
    assert _cues(live) == _cues(baseline())


def test_the_prefix_check_rejects_a_changed_digest_and_a_watermark_beyond_the_log() -> None:
    engine = reference()
    header = engine.state.snapshots[-1].header
    assert prefix_matches(engine.log, header)
    assert not prefix_matches(engine.log, header.model_copy(update={"log_digest": "b" * 64}))
    beyond = header.model_copy(update={"watermark": engine.log.watermark + 1})
    assert not prefix_matches(engine.log, beyond)


def test_an_invalid_prefix_is_served_with_its_codes_and_no_values() -> None:
    live = replayed(corrupted(index=300))
    served = payloads(live)
    invalid = [p for p in served if p["status"] == "invalid"]
    assert invalid and served[0]["status"] == "valid"
    for p in invalid:
        assert p["teams"] == [] and p["reason"] == "player_not_on_pitch"
    first = served.index(invalid[0])
    assert all(p["status"] == "invalid" for p in served[first:])


def test_a_reasoning_failure_does_not_change_the_statistics() -> None:
    def failing(snapshot: Snapshot) -> SnapshotEvaluation:
        return SnapshotEvaluation(
            header=snapshot.header, outcome=SnapshotOutcome.FAILED, failure="RuntimeError"
        )

    live = LiveMatch(in_progress(), speed=0, loop=False, evaluator=failing)
    live._replay(0)
    assert live.edition.state is not None
    assert all(r.outcome is SnapshotOutcome.FAILED for r in live.edition.state.snapshots)
    assert [m.data for m in messages(live) if m.event == "stats"] == [
        m.data for m in messages(integrated()) if m.event == "stats"
    ]
