"""The recorded backend reuses the validity and Stage 2 analytics its evaluation computed, exactly
as the reference backend does (ADR-0016): the stream, lifecycle and transcript handling are those
of the plain recorded evaluator, byte for byte, and transcript failures never touch statistics."""

import json
import os
import socket
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import replace
from functools import cache
from pathlib import Path
from typing import Any

import pytest

import matcheyes.__main__ as cli
from matcheyes.agents.recorded import RecordedModel
from matcheyes.api import live as live_module
from matcheyes.api.catalog import load_catalog
from matcheyes.api.live import CachingEvaluator, LiveMatch, StatsCache
from matcheyes.broadcast import stats as stats_module
from matcheyes.broadcast.stats import snapshot_stats, stats_message
from matcheyes.domain.match import ObservableMatch
from matcheyes.lifecycle import evaluate as evaluate_module
from matcheyes.lifecycle.evaluate import AnalysedEvaluation, evaluate_and_analyse, evaluate_snapshot
from matcheyes.lifecycle.recording import (
    MODEL_CONFIG,
    match_snapshots,
    recorded_analysis,
    recorded_evaluator,
    unavailable_insights,
)
from matcheyes.lifecycle.snapshot import Snapshot
from tests.lifecycle.support import in_progress
from tests.lifecycle.test_recording import recorded
from tests.support.imports import SRC_ROOT

DEPLOY = SRC_ROOT.parent / "deploy"
DEMO = "m-b71a80f94d25"
Analyse = Callable[[Snapshot], AnalysedEvaluation]


@pytest.fixture(scope="module")
def transcript(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    path = tmp_path_factory.mktemp("recorded") / "m.transcript.json.gz"
    return path, recorded()[0].transcript({"deployment": "d"}).write(path)


def stream(live: LiveMatch) -> dict[Any, list[tuple[str, int, str]]]:
    return {
        key: [(m.event, m.index, m.data) for m in s.messages]
        for key, s in live.edition.surfaces.items()
    }


def stats_data(live: LiveMatch) -> list[str]:
    return [
        m.data for m in next(iter(live.edition.surfaces.values())).messages if m.event == "stats"
    ]


@cache
def offline(match_id: str, edition: int = 0) -> tuple[str, ...]:
    match = in_progress() if match_id == in_progress().info.match_id else demo()
    return tuple(stats_message(edition, snapshot_stats(s)) for s in match_snapshots(match))


@cache
def demo() -> ObservableMatch:
    return load_catalog(DEPLOY / "matches")[DEMO]


def replayed(match: ObservableMatch, evaluator: Any, number: int = 0) -> LiveMatch:
    live = LiveMatch(match, speed=0, loop=False, evaluator=evaluator)
    live._replay(number)
    return live


@pytest.fixture
def stats_calls(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    counts: Counter[str] = Counter()
    for name in ("validate_prefix", "team_analytics"):
        original = getattr(stats_module, name)

        def counted(*args: Any, _name: str = name, _fn: Any = original) -> Any:
            counts[_name] += 1
            return _fn(*args)

        monkeypatch.setattr(stats_module, name, counted)
    return counts


@pytest.fixture
def offline_only(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No network and no model credentials: a recorded replay must need neither."""
    for name in list(os.environ):
        if name.startswith("MATCHEYES_LLM_"):
            monkeypatch.delenv(name)

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("a recorded replay opened a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    yield


def assert_reused_everywhere(live: LiveMatch, match_id: str, edition: int = 0) -> None:
    assert stats_data(live) == list(offline(match_id, edition))
    assert live.stats_cache is not None
    count = len(offline(match_id))
    assert (live.stats_cache.calls, live.stats_cache.reused) == (count, count)


# --- the pinned public demo --------------------------------------------------------------------


def test_the_pinned_demo_backend_reuses_analytics_with_an_identical_stream() -> None:
    backend = cli._backends({DEMO: demo()}, DEPLOY / "recordings")[DEMO]
    pins = json.loads((DEPLOY / "recordings" / "pins.json").read_text("utf-8"))
    assert backend.reasoner.kind == "recorded-model"
    assert backend.reasoner.transcript_sha256 == pins[DEMO]
    evaluator = backend.evaluator
    assert isinstance(evaluator, CachingEvaluator) and evaluator.base is None
    model = RecordedModel.load(DEPLOY / "recordings" / f"{DEMO}{cli.TRANSCRIPT_SUFFIX}", pins[DEMO])
    assert model.usable and not model.problems

    new = replayed(demo(), evaluator)
    old = replayed(demo(), CachingEvaluator(recorded_evaluator(model)))
    old_evaluator = old.evaluator
    assert isinstance(old_evaluator, CachingEvaluator)
    assert evaluator.cache == old_evaluator.cache  # every snapshot's evaluation
    assert len(evaluator.cache) == len(offline(DEMO)) == 98
    assert stream(new) == stream(old)
    assert new.edition.state == old.edition.state
    assert_reused_everywhere(new, DEMO)
    assert old.stats_cache is not None and old.stats_cache.reused == 0


# --- equivalence on the stub-model recording ---------------------------------------------------


@pytest.fixture(scope="module")
def full_model(transcript: tuple[Path, str]) -> RecordedModel:
    path, sha = transcript
    return RecordedModel.load(path, sha)


@pytest.fixture(scope="module")
def plain(full_model: RecordedModel) -> LiveMatch:
    return replayed(in_progress(), CachingEvaluator(recorded_evaluator(full_model)))


def test_the_analysing_recorded_evaluator_equals_the_recorded_evaluator_on_every_snapshot(
    full_model: RecordedModel,
) -> None:
    analyse, evaluate = recorded_analysis(full_model), recorded_evaluator(full_model)
    for snapshot in match_snapshots(in_progress()):
        result = analyse(snapshot)
        assert result.evaluation == evaluate(snapshot)
        assert result.analytics.snapshot_id == snapshot.header.snapshot_id
        assert result.analytics.log_digest == snapshot.header.log_digest
    assert analyse.func is evaluate_and_analyse and evaluate.func is evaluate_snapshot
    assert analyse.keywords["config"] == evaluate.keywords["config"] == MODEL_CONFIG
    assert analyse.keywords["model"].name == evaluate.keywords["model"].name


def test_recorded_reuse_keeps_the_stream_and_lifecycle_and_skips_repeated_work(
    full_model: RecordedModel, plain: LiveMatch, stats_calls: Counter[str]
) -> None:
    live = replayed(in_progress(), CachingEvaluator(analyse=recorded_analysis(full_model)))
    assert stats_calls == Counter()
    assert stream(live) == stream(plain)
    assert live.edition.state == plain.edition.state
    assert_reused_everywhere(live, in_progress().info.match_id)
    text = " ".join(stats_data(live))
    for field in ("transcript", "request", "response", "sha256", "deployment", "reasoner"):
        assert f'"{field}' not in text


# --- transcript and reasoning failures ---------------------------------------------------------


def _damaged(transcript: tuple[Path, str], kind: str) -> RecordedModel:
    path = transcript[0]
    if kind == "missing":
        return RecordedModel.load(path.with_name("absent.transcript.json.gz"))
    if kind == "corrupted":
        broken = path.with_name("broken.transcript.json.gz")
        broken.write_bytes(b"not a transcript")
        return RecordedModel.load(broken)
    if kind == "mispinned":
        return RecordedModel.load(path, "0" * 64)
    full = recorded()[0].transcript({"deployment": "d"})
    half = full.model_copy(update={"entries": full.entries[::2]})
    cut = path.with_name("half.transcript.json.gz")
    return RecordedModel.load(cut, half.write(cut))


@pytest.mark.parametrize("kind", ["missing", "corrupted", "mispinned", "miss"])
def test_transcript_failures_keep_unavailable_insights_and_reused_statistics(
    kind: str, transcript: tuple[Path, str], stats_calls: Counter[str], offline_only: None
) -> None:
    model = _damaged(transcript, kind)
    assert model.usable == (kind == "miss")
    live = replayed(in_progress(), CachingEvaluator(analyse=recorded_analysis(model)))
    assert stats_calls == Counter()
    old = replayed(in_progress(), CachingEvaluator(recorded_evaluator(model)))
    assert stream(live) == stream(old)
    assert live.edition.state == old.edition.state
    evaluator = live.evaluator
    assert isinstance(evaluator, CachingEvaluator)
    assert unavailable_insights(list(evaluator.cache.values())) > 0
    assert_reused_everywhere(live, in_progress().info.match_id)


def test_reasoning_failing_after_stage_2_keeps_reused_statistics(
    full_model: RecordedModel, monkeypatch: pytest.MonkeyPatch, stats_calls: Counter[str]
) -> None:
    def fails(*_: Any) -> None:
        raise RuntimeError("reasoning down")

    monkeypatch.setattr(evaluate_module, "analyse_contextual", fails)
    live = replayed(in_progress(), CachingEvaluator(analyse=recorded_analysis(full_model)))
    assert stats_calls == Counter()
    old = replayed(in_progress(), CachingEvaluator(recorded_evaluator(full_model)))
    assert stream(live) == stream(old)
    assert live.edition.state is not None
    assert {r.outcome.value for r in live.edition.state.snapshots} == {"failed"}
    assert_reused_everywhere(live, in_progress().info.match_id)


def _strip_stage2(result: AnalysedEvaluation) -> AnalysedEvaluation:
    return replace(result, analytics=replace(result.analytics, summaries=None, series=None))


def _other_snapshot(result: AnalysedEvaluation) -> AnalysedEvaluation:
    return replace(result, analytics=replace(result.analytics, snapshot_id="snap-elsewhere"))


def _other_prefix(result: AnalysedEvaluation) -> AnalysedEvaluation:
    return replace(result, analytics=replace(result.analytics, log_digest="b" * 64))


@pytest.mark.parametrize("damage", [_strip_stage2, _other_snapshot, _other_prefix])
def test_unavailable_or_mismatched_analytics_fall_back_to_the_full_build(
    damage: Callable[[AnalysedEvaluation], AnalysedEvaluation],
    full_model: RecordedModel,
    plain: LiveMatch,
) -> None:
    analyse = recorded_analysis(full_model)
    live = replayed(in_progress(), CachingEvaluator(analyse=lambda s: damage(analyse(s))))
    assert stream(live) == stream(plain)
    assert stats_data(live) == list(offline(in_progress().info.match_id))
    assert live.stats_cache is not None and live.stats_cache.reused == 0


# --- cache bounds ------------------------------------------------------------------------------


def test_eviction_and_missing_entries_stay_bounded_and_fall_back(
    full_model: RecordedModel, plain: LiveMatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live_module, "EVALUATION_CACHE_LIMIT", 3)
    evaluator = CachingEvaluator(analyse=recorded_analysis(full_model))
    live = replayed(in_progress(), evaluator)
    assert len(evaluator.cache) <= 3 and set(evaluator.analytics) == set(evaluator.cache)
    assert stream(live) == stream(plain)
    monkeypatch.setattr(live_module, "EVALUATION_CACHE_LIMIT", 400)
    warm = CachingEvaluator(analyse=recorded_analysis(full_model))
    replayed(in_progress(), warm)
    warm.analytics.clear()
    again = LiveMatch(in_progress(), speed=0, loop=False, evaluator=warm)
    again.stats_cache = StatsCache()
    again._replay(1)
    assert again.stats_cache.reused == 0
    assert stats_data(again) == list(offline(in_progress().info.match_id, 1))


def test_a_plain_and_an_analysing_evaluator_cannot_both_be_given(
    full_model: RecordedModel,
) -> None:
    with pytest.raises(ValueError, match="not both"):
        CachingEvaluator(recorded_evaluator(full_model), analyse=recorded_analysis(full_model))
