"""Model-backed lifecycles: record once, replay identically, never fall back silently."""

from functools import cache
from pathlib import Path

import pytest

from matcheyes.agents.reasoning import RuleBasedReasoner
from matcheyes.agents.recorded import RecordedModel, RecordingModel, Transcript
from matcheyes.broadcast.compiler import compile_timeline
from matcheyes.lifecycle.contracts import LifecycleState
from matcheyes.lifecycle.engine import replay
from matcheyes.lifecycle.evaluate import SnapshotEvaluation, evaluate_snapshot
from matcheyes.lifecycle.recording import (
    MODEL_CONFIG,
    lifecycle_from,
    match_snapshots,
    ordered_events,
    record_lifecycle,
    recorded_evaluator,
    recorded_reasoner,
    replayed_lifecycle,
    unavailable_insights,
)
from matcheyes.personalization.contracts import Audience, PersonalizationProfile
from tests.lifecycle.support import in_progress, reference

pytestmark = pytest.mark.integration


class StandIn(RuleBasedReasoner):
    """A deterministic stand-in for a hosted model: same answers, its own name."""

    @property
    def name(self) -> str:
        return "stand-in"


@cache
def recorded() -> tuple[RecordingModel, dict[str, SnapshotEvaluation]]:
    return record_lifecycle(in_progress(), StandIn(), workers=4)


@cache
def recorded_state() -> LifecycleState:
    return lifecycle_from(in_progress(), recorded()[1])


def _written(tmp_path: Path, transcript: Transcript) -> tuple[Path, str]:
    path = tmp_path / "m.transcript.json.gz"
    return path, transcript.write(path)


def test_every_snapshot_of_the_replay_is_recorded() -> None:
    snapshots = match_snapshots(in_progress())
    recorder, evaluations = recorded()
    assert [s.header.snapshot_id for s in snapshots] == list(evaluations)
    assert [s.header.snapshot_id for s in snapshots] == [
        r.header.snapshot_id for r in reference().state.snapshots
    ]
    assert recorder.transcript().entries
    assert unavailable_insights(list(evaluations.values())) == 0


def test_the_recorded_lifecycle_matches_the_reference_storylines() -> None:
    state = recorded_state()
    ref = reference().state
    assert [s.storyline_id for s in state.storylines] == [s.storyline_id for s in ref.storylines]
    assert [len(s.revisions) for s in state.storylines] == [
        len(s.revisions) for s in ref.storylines
    ]


def test_the_replayed_lifecycle_is_identical_to_the_recorded_one(tmp_path: Path) -> None:
    path, sha = _written(tmp_path, recorded()[0].transcript({"deployment": "d"}))
    model = RecordedModel.load(path, expected_sha256=sha)
    assert model.usable and model.reasoner == "stand-in"
    first = replayed_lifecycle(in_progress(), model).model_dump_json()
    assert first == recorded_state().model_dump_json()
    assert replayed_lifecycle(in_progress(), model).model_dump_json() == first


def test_offline_and_live_compiles_of_the_replayed_lifecycle_agree(tmp_path: Path) -> None:
    path, _ = _written(tmp_path, recorded()[0].transcript())
    match = in_progress()
    engine = replay(match.info, ordered_events(match), recorded_evaluator(RecordedModel.load(path)))
    profile = PersonalizationProfile(audience=Audience.FAN)
    a = compile_timeline(match.info, engine.log.events(), engine.state, profile)
    b = compile_timeline(match.info, engine.log.events(), recorded_state(), profile)
    assert a.model_dump_json() == b.model_dump_json()


def _first_card_snapshot() -> str:
    match = in_progress()
    profile = PersonalizationProfile(audience=Audience.FAN)
    cues = compile_timeline(match.info, reference().log.events(), recorded_state(), profile).cues
    return next(c.snapshot_id for c in cues if c.kind.value == "insight")


def test_missing_entries_make_investigations_unavailable_and_are_published(
    tmp_path: Path,
) -> None:
    """Entries needed only after the first card is shown are missing: the card must be retracted
    when its storyline can no longer be verified, never kept on stale recorded output."""
    transcript = recorded()[0].transcript()
    path, _ = _written(tmp_path, transcript)
    full = RecordedModel.load(path)
    cut = _first_card_snapshot()
    kept: set[str] = set()
    for snapshot in match_snapshots(in_progress()):
        probe = RecordingModel(full)
        evaluate_snapshot(snapshot, probe, MODEL_CONFIG)
        kept |= {e.key for e in probe.transcript().entries}
        if snapshot.header.snapshot_id == cut:
            break
    damaged = transcript.model_copy(
        update={"entries": tuple(e for e in transcript.entries if e.key in kept)}
    )
    assert 0 < len(damaged.entries) < len(transcript.entries)
    path, _ = _written(tmp_path, damaged)
    model = RecordedModel.load(path)
    match = in_progress()
    evaluations = [recorded_evaluator(model)(s) for s in match_snapshots(match)]
    assert unavailable_insights(evaluations) > 0
    state = replayed_lifecycle(match, model)
    finals = [r.final for s in state.storylines for r in s.revisions if r.final is not None]
    assert any(f.verdict.value == "unavailable" for f in finals)
    assert state.model_dump_json() != recorded_state().model_dump_json()
    profile = PersonalizationProfile(audience=Audience.FAN)
    engine = replay(match.info, ordered_events(match), recorded_evaluator(model))
    cues = compile_timeline(match.info, engine.log.events(), engine.state, profile).cues
    shown = [c for c in cues if c.kind.value in ("insight", "revision")]
    assert not any(getattr(c.source, "verdict", None) == "unavailable" for c in shown)
    revisions = {(s.storyline_id, r.number): r for s in state.storylines for r in s.revisions}
    causes = [
        revisions[(c.source.storyline_id, c.source.cause_revision)]
        for c in cues
        if c.kind.value == "retraction" and getattr(c.source, "cause_revision", None)
    ]
    assert any(r.final is not None and r.final.verdict.value == "unavailable" for r in causes)


def test_a_challenger_only_recording_replays_in_the_same_roles(tmp_path: Path) -> None:
    match = in_progress()
    recorder, evaluations = record_lifecycle(match, StandIn(), workers=4, roles="challenger")
    transcript = recorder.transcript({"roles": "challenger"})
    assert transcript.entries and {e.step.value for e in transcript.entries} == {"challenge"}
    path, sha = _written(tmp_path, transcript)
    model = RecordedModel.load(path, sha)
    assert recorded_reasoner(model).name.endswith("+challenger=stand-in")
    assert (
        replayed_lifecycle(match, model).model_dump_json()
        == lifecycle_from(match, evaluations).model_dump_json()
    )


def test_a_tampered_or_mispinned_transcript_replays_nothing(tmp_path: Path) -> None:
    path, sha = _written(tmp_path, recorded()[0].transcript())
    wrong = RecordedModel.load(path, expected_sha256="0" * 64)
    assert not wrong.usable and "transcript hash mismatch" in wrong.problems
    state = replayed_lifecycle(in_progress(), wrong)
    finals = [r.final for s in state.storylines for r in s.revisions if r.final is not None]
    assert finals and all(f.verdict.value == "unavailable" for f in finals)
    assert sha != "0" * 64
