"""Statistics reuse the validity and Stage 2 analytics the evaluation already computed on the same
snapshot (ADR-0016): byte-identical to computing them again, never from another snapshot, and
falling back to the full build whenever nothing can be reused."""

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from functools import cache
from typing import Any

import pytest

from matcheyes.api import live as live_module
from matcheyes.api.live import CachingEvaluator, LiveMatch, StatsCache
from matcheyes.broadcast import stats as stats_module
from matcheyes.broadcast.stats import (
    ANALYTICS_ERROR,
    MEASURES,
    POOLED_SERIES,
    StatsStatus,
    snapshot_stats,
    snapshot_stats_from,
    stats_message,
)
from matcheyes.domain.match import ObservableMatch
from matcheyes.ingestion.invariants import validate_prefix
from matcheyes.lifecycle import evaluate as evaluate_module
from matcheyes.lifecycle.contracts import SnapshotOutcome
from matcheyes.lifecycle.evaluate import evaluate_and_analyse, evaluate_snapshot
from tests.api.test_live_stats import integrated
from tests.broadcast.test_stats import corrupted, snapshots
from tests.lifecycle.support import in_progress, reference

WATCHED = {
    "stats.validate": (stats_module, "validate_prefix"),
    "stats.analytics": (stats_module, "team_analytics"),
    "eval.validate": (evaluate_module, "validate_prefix"),
    "eval.analyse": (evaluate_module, "analyse_match"),
}


def counting(name: str, fn: Callable[..., Any], counts: Counter[str]) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        counts[name] += 1
        return fn(*args, **kwargs)

    return wrapper


def watched_replay(match: ObservableMatch, **patches: Any) -> tuple[LiveMatch, Counter[str]]:
    """A replay with the reference evaluation cache, counting validation and analytics calls."""
    counts: Counter[str] = Counter()
    with pytest.MonkeyPatch.context() as mp:
        for short, (module, name) in WATCHED.items():
            original = patches.get(short, getattr(module, name))
            mp.setattr(module, name, counting(short, original, counts))
        live = LiveMatch(match, speed=0, loop=False)
        live._replay(0)
    return live, counts


@cache
def reused() -> tuple[LiveMatch, Counter[str]]:
    return watched_replay(in_progress())


def stats_data(live: LiveMatch) -> list[str]:
    return [
        m.data for m in next(iter(live.edition.surfaces.values())).messages if m.event == "stats"
    ]


def offline(match: ObservableMatch, edition: int = 0) -> list[str]:
    return [stats_message(edition, snapshot_stats(s)) for s in snapshots(match)]


def copied(evaluator: CachingEvaluator) -> CachingEvaluator:
    copy = CachingEvaluator()
    copy.cache, copy.analytics = dict(evaluator.cache), dict(evaluator.analytics)
    return copy


def filled() -> CachingEvaluator:
    evaluator = reused()[0].evaluator
    assert isinstance(evaluator, CachingEvaluator)
    return evaluator


# --- equivalence ---------------------------------------------------------------------------------


def test_reused_statistics_equal_the_full_build_byte_for_byte_on_every_snapshot() -> None:
    live, _ = reused()
    assert stats_data(live) == offline(in_progress())
    assert live.stats_cache is not None
    count = len(reference().state.snapshots)
    assert (live.stats_cache.calls, live.stats_cache.reused, live.stats_cache.hits) == (
        count,
        count,
        0,
    )


def test_reused_statistics_equal_stage_2_for_all_eight_measures() -> None:
    shown = 0
    for snapshot, data in zip(snapshots(in_progress()), stats_data(reused()[0]), strict=True):
        summaries = evaluate_module.analyse_match(snapshot.match).summaries
        payload = json.loads(data)
        for team, summary in zip(payload["teams"], summaries, strict=True):
            assert team["team_id"] == summary.team_id
            for measure in MEASURES:
                value = team["values"][measure.key]["value"]
                if value is not None:
                    assert value == getattr(summary, measure.key), measure.key
                    shown += 1
    assert shown > 0


def test_the_whole_stream_and_lifecycle_are_unchanged_by_reuse() -> None:
    live = reused()[0]
    for key, surface in live.edition.surfaces.items():
        expected = integrated().edition.surfaces[key].messages
        assert [(m.event, m.index, m.data) for m in surface.messages] == [
            (m.event, m.index, m.data) for m in expected
        ]
    assert live.edition.state == reference().state


# --- no repeated work ----------------------------------------------------------------------------


def test_validation_and_analytics_run_once_per_snapshot_in_the_evaluation_only() -> None:
    _, counts = reused()
    count = len(reference().state.snapshots)
    assert counts["eval.validate"] == count and counts["eval.analyse"] == count
    assert counts["stats.validate"] == 0 and counts["stats.analytics"] == 0


def test_only_the_summaries_and_pooled_series_are_kept_for_reuse() -> None:
    evaluator = filled()
    assert set(evaluator.analytics) == set(evaluator.cache)
    for key, kept in evaluator.analytics.items():
        assert kept.snapshot_id == key and kept.summaries is not None and kept.series is not None
        assert {name for name, _ in kept.series} == set(POOLED_SERIES) and len(kept.series) == 4


# --- invalid prefixes and failures ---------------------------------------------------------------


def test_invalid_prefixes_reuse_the_evaluations_validity_codes() -> None:
    match = corrupted(index=300)
    live, counts = watched_replay(match)
    served = [json.loads(d) for d in stats_data(live)]
    assert stats_data(live) == offline(match)
    invalid = [p for p in served if p["status"] == "invalid"]
    assert invalid and all(
        p["reason"] == "player_not_on_pitch" and p["teams"] == [] for p in invalid
    )
    assert counts["stats.validate"] == 0 and counts["stats.analytics"] == 0
    assert live.stats_cache is not None and live.stats_cache.reused == len(served)
    assert live.edition.state is not None
    outcomes = [r.outcome for r in live.edition.state.snapshots]
    assert [p["status"] == "invalid" for p in served] == [
        o is SnapshotOutcome.INVALID for o in outcomes
    ]


def test_reasoning_failing_after_analytics_keeps_reusable_valid_statistics() -> None:
    def contextual_fails(*_: Any) -> None:
        raise RuntimeError("reasoning down")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(evaluate_module, "analyse_contextual", contextual_fails)
        result = evaluate_and_analyse(snapshots(in_progress())[-1])
        live, counts = watched_replay(in_progress())
    assert result.evaluation.outcome is SnapshotOutcome.FAILED
    assert result.evaluation.failure == "RuntimeError"
    assert result.analytics.summaries is not None
    assert live.edition.state is not None
    assert {r.outcome for r in live.edition.state.snapshots} == {SnapshotOutcome.FAILED}
    assert stats_data(live) == offline(in_progress())
    assert counts["stats.analytics"] == 0
    assert live.stats_cache is not None and live.stats_cache.reused == len(stats_data(live))


def test_stage_2_failing_in_the_evaluation_falls_back_to_the_full_build() -> None:
    def analysis_fails(*_: Any) -> None:
        raise ZeroDivisionError("analysis down")

    live, counts = watched_replay(in_progress(), **{"eval.analyse": analysis_fails})
    assert live.edition.state is not None
    assert {r.outcome for r in live.edition.state.snapshots} == {SnapshotOutcome.FAILED}
    assert stats_data(live) == offline(in_progress())
    assert live.stats_cache is not None and live.stats_cache.reused == 0
    assert counts["stats.analytics"] == len(stats_data(live))


def test_analytics_failing_everywhere_is_still_analytics_error_without_values() -> None:
    def fails(*_: Any) -> None:
        raise ZeroDivisionError("secret")

    live, _ = watched_replay(in_progress(), **{"eval.analyse": fails, "stats.analytics": fails})
    served = [json.loads(d) for d in stats_data(live)]
    assert served and all(
        (p["status"], p["reason"], p["teams"]) == ("unavailable", ANALYTICS_ERROR, [])
        for p in served
    )
    assert "secret" not in " ".join(stats_data(live))


def test_a_validation_error_propagates_exactly_as_before() -> None:
    def broken(*_: Any) -> None:
        raise RuntimeError("validator down")

    snapshot = snapshots(in_progress())[0]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(evaluate_module, "validate_prefix", broken)
        with pytest.raises(RuntimeError, match="validator down"):
            evaluate_snapshot(snapshot)
        with pytest.raises(RuntimeError, match="validator down"):
            evaluate_and_analyse(snapshot)


def test_missing_or_failed_analytics_are_never_shown_as_valid() -> None:
    snapshot = snapshots(in_progress())[-1]
    ok = validate_prefix(snapshot.match)
    assert ok.ok
    empty = snapshot_stats_from(snapshot, ok, (), {})
    assert (empty.status, empty.reason, empty.teams) == (
        StatsStatus.UNAVAILABLE,
        ANALYTICS_ERROR,
        (),
    )
    summaries, _ = stats_module.team_analytics(snapshot.match)
    no_series = snapshot_stats_from(snapshot, ok, summaries, {})
    assert no_series.status is StatsStatus.UNAVAILABLE
    bad = validate_prefix(snapshots(corrupted())[0].match)
    invalid = snapshot_stats_from(
        snapshot, bad, summaries, stats_module.team_analytics(snapshot.match)[1]
    )
    assert invalid.status is StatsStatus.INVALID and invalid.teams == ()
    assert invalid.reason == "player_not_on_pitch"


# --- fallback and boundaries ---------------------------------------------------------------------


def _replayed(evaluator: Any, number: int = 0, limit: int | None = None) -> LiveMatch:
    live = LiveMatch(in_progress(), speed=0, loop=False, evaluator=evaluator)
    live.stats_cache = StatsCache(limit=limit)
    live._replay(number)
    return live


def test_evaluators_that_expose_nothing_use_the_full_build() -> None:
    for evaluator in (CachingEvaluator(evaluate_snapshot), integrated().evaluator):
        live = _replayed(evaluator)
        assert live.stats_cache is not None and live.stats_cache.reused == 0
        assert stats_data(live) == offline(in_progress())
    assert CachingEvaluator(evaluate_snapshot).analytics == {}


def test_missing_reusable_entries_fall_back_and_publish_the_same_statistics() -> None:
    evaluator = copied(filled())
    evaluator.analytics.clear()
    live = _replayed(evaluator, number=1)
    assert live.stats_cache is not None
    assert (live.stats_cache.reused, live.stats_cache.calls) == (
        0,
        len(reference().state.snapshots),
    )
    assert stats_data(live) == offline(in_progress(), edition=1)


def test_analytics_of_another_snapshot_or_prefix_are_never_reused() -> None:
    evaluator = copied(filled())
    keys = list(evaluator.analytics)
    other = evaluator.analytics[keys[0]]
    for i, key in enumerate(keys):
        kept = evaluator.analytics[key]
        if i == 0:
            evaluator.analytics[key] = replace(kept, snapshot_id="snap-elsewhere")
        elif i % 2:
            evaluator.analytics[key] = replace(kept, log_digest="b" * 64)
        else:
            evaluator.analytics[key] = replace(other, snapshot_id=key)  # another prefix's digest
    live = _replayed(evaluator, number=1)
    assert live.stats_cache is not None and live.stats_cache.reused == 0
    assert stats_data(live) == offline(in_progress(), edition=1)


def test_a_later_edition_reuses_only_identical_snapshots_and_carries_its_own_edition() -> None:
    live = _replayed(copied(filled()), number=3)
    served = [json.loads(d) for d in stats_data(live)]
    assert {p["edition"] for p in served} == {3}
    assert stats_data(live) == offline(in_progress(), edition=3)
    assert live.stats_cache is not None and live.stats_cache.reused == len(served)


def test_eviction_keeps_both_caches_bounded_and_the_statistics_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(live_module, "EVALUATION_CACHE_LIMIT", 3)
    evaluator = CachingEvaluator()
    live = _replayed(evaluator)
    assert len(evaluator.cache) <= 3 and set(evaluator.analytics) == set(evaluator.cache)
    assert stats_data(live) == offline(in_progress())


def test_cache_enabled_and_disabled_publish_equivalent_statistics() -> None:
    disabled = LiveMatch(in_progress(), speed=0, loop=False, evaluator=copied(filled()))
    disabled.stats_cache = StatsCache(limit=0)
    disabled._replay(0)
    first = stats_data(disabled)
    disabled._replay(1)
    count = len(reference().state.snapshots)
    assert (disabled.stats_cache.calls, disabled.stats_cache.reused) == (2 * count, 2 * count)
    assert first == stats_data(reused()[0])
    assert stats_data(disabled) == offline(in_progress(), edition=1)


def test_the_cached_snapshot_identity_is_the_snapshot_header() -> None:
    evaluator = filled()
    headers = {r.header.snapshot_id: r.header for r in reference().state.snapshots}
    for key, kept in evaluator.analytics.items():
        assert kept.log_digest == headers[key].log_digest
