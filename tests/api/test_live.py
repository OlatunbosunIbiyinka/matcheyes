"""The live driver: one server-owned replay per match; the clock never changes a cue."""

import pytest

from matcheyes.api import live as live_module
from matcheyes.api.live import CachingEvaluator, LiveMatch, match_seconds
from matcheyes.broadcast.compiler import compile_timeline
from matcheyes.domain.events import PeriodEnd
from matcheyes.lifecycle.evaluate import SnapshotEvaluation
from matcheyes.lifecycle.snapshot import Snapshot
from matcheyes.personalization.contracts import Audience
from tests.api.support import wait_done
from tests.lifecycle.support import evaluator, failed, in_progress, reference
from tests.synth.generated import generated


def _offline_ids(live: LiveMatch, key: tuple[Audience, str | None]) -> list[str]:
    ref = reference()
    tl = compile_timeline(in_progress().info, ref.log.events(), ref.state, live.profiles[key])
    return [c.cue_id for c in tl.cues]


def test_match_time_runs_on_through_half_time() -> None:
    events = generated("S05_red_card_reorganisation").observable.events
    seconds = match_seconds(events)
    assert seconds == sorted(seconds)
    end = next(i for i, e in enumerate(events) if isinstance(e, PeriodEnd) and e.period == 1)
    assert seconds[end + 1] >= seconds[end]
    assert match_seconds(in_progress().events)[0] == 0.0


def test_six_surfaces_one_replay_and_paced_replays_equal_the_offline_compile() -> None:
    live = LiveMatch(in_progress(), speed=100_000, loop=False, evaluator=evaluator())
    assert len(live.profiles) == 6
    live.start()
    live.start()
    wait_done(live)
    live.stop()
    assert live.edition.done and len(live.replay_seconds) == 1
    for key in live.profiles:
        number, done, tl = live.timeline(key)
        assert (number, done) == (0, True)
        assert [c.cue_id for c in tl.cues] == _offline_ids(live, key)


def test_a_looping_replay_publishes_the_same_cues_in_every_edition() -> None:
    live = LiveMatch(in_progress(), speed=0, loop=True, pause=0, evaluator=evaluator())
    key = (Audience.FAN, None)
    live.start()
    wait_done(live, edition=0)
    with live.condition:
        live.condition.wait_for(lambda: live.edition.number >= 1, timeout=300)
    wait_done(live, edition=1)
    live.stop()
    assert live.stopped
    number, _, tl = live.timeline(key)
    assert number >= 1
    assert [c.cue_id for c in tl.cues] == _offline_ids(live, key)[: len(tl.cues)]


def test_stopping_mid_replay_ends_the_thread() -> None:
    live = LiveMatch(in_progress(), speed=1, loop=True, evaluator=evaluator())
    live.start()
    live.stop()
    assert live.stopped and not live.edition.done


def test_surfaces_and_speed_are_validated() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        LiveMatch(in_progress(), speed=-1)
    live = LiveMatch(in_progress(), speed=0, evaluator=evaluator())
    assert live.surface(Audience.BROADCASTER, in_progress().info.away.team_id)
    with pytest.raises(KeyError):
        live.surface(Audience.ANALYST, None)
    with pytest.raises(KeyError):
        live.surface(Audience.FAN, "someone-else")


def test_the_evaluation_cache_is_keyed_by_snapshot_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def base(snapshot: Snapshot) -> SnapshotEvaluation:
        calls.append(snapshot.header.snapshot_id)
        return failed(snapshot.header.watermark)

    class Header:
        def __init__(self, sid: str) -> None:
            self.snapshot_id, self.watermark = sid, 1

    class Fake:
        def __init__(self, sid: str) -> None:
            self.header = Header(sid)

    monkeypatch.setattr(live_module, "EVALUATION_CACHE_LIMIT", 2)
    cache = CachingEvaluator(base)
    for sid in ("a", "a", "b", "c", "a"):
        cache(Fake(sid))  # type: ignore[arg-type]
    assert calls == ["a", "b", "c", "a"]
    assert len(cache.cache) <= 2
