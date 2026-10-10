"""Recorded model runs: record once, replay deterministically, and fail safely on any doubt."""

import json
from pathlib import Path

import pytest

from matcheyes.agents import recorded as recorded_module
from matcheyes.agents.contracts import Verdict
from matcheyes.agents.reasoning import (
    AgentTask,
    ModelUnavailableError,
    RuleBasedReasoner,
    Step,
)
from matcheyes.agents.recorded import (
    TRANSCRIPT_FORMAT,
    RecordedModel,
    RecordingModel,
    Transcript,
    TranscriptEntry,
    canonical_request,
    request_key,
)
from matcheyes.orchestration.investigation import InvestigationRecord, Orchestrator
from tests.agents.support import (
    RED_CARD,
    ScriptedModel,
    case_for,
    strongest,
    unavailable,
    workspace,
)


def _ticks() -> object:
    t = iter(range(10_000))
    return lambda: float(next(t))


def _investigate(model: object) -> InvestigationRecord:
    ws = workspace(RED_CARD)
    return Orchestrator(ws, model).investigate(strongest(ws).candidate_id)  # type: ignore[arg-type]


def _record() -> tuple[RecordingModel, InvestigationRecord]:
    recorder = RecordingModel(RuleBasedReasoner(), clock=_ticks())  # type: ignore[arg-type]
    return recorder, _investigate(recorder)


def _canonical(record: InvestigationRecord) -> str:
    return record.model_dump_json(exclude={"trace"})


def _rewrite(transcript: Transcript, step: Step, **update: object) -> Transcript:
    entries = list(transcript.entries)
    index = next(i for i, e in enumerate(entries) if e.step is step)
    entries[index] = entries[index].model_copy(update=update)
    return transcript.model_copy(update={"entries": tuple(entries)})


def _task() -> AgentTask:
    ws = workspace(RED_CARD)
    return AgentTask(step=Step.PLAN, case=case_for(ws, strongest(ws)))


def test_a_recorded_run_replays_to_the_same_investigation(tmp_path: Path) -> None:
    recorder, live = _record()
    transcript = recorder.transcript({"deployment": "test"})
    assert {e.step for e in transcript.entries} >= {Step.PLAN, Step.ASSESS, Step.CHALLENGE}
    assert transcript.reasoner == RuleBasedReasoner().name
    sha = transcript.write(tmp_path / "t.json")
    replayed = RecordedModel.load(tmp_path / "t.json", expected_sha256=sha)
    assert replayed.problems == []
    assert replayed.name == RuleBasedReasoner().name
    assert replayed.transcript_sha256 == sha
    assert replayed.metadata == {"deployment": "test"}
    assert _canonical(_investigate(replayed)) == _canonical(live)


def test_replay_is_deterministic_and_the_transcript_is_content_addressed(tmp_path: Path) -> None:
    first, _ = _record()
    second, _ = _record()
    assert first.transcript().sha256 == second.transcript().sha256
    model = RecordedModel(first.transcript())
    assert _canonical(_investigate(model)) == _canonical(_investigate(model))
    path = tmp_path / "t.json"
    sha = first.transcript().write(path)
    assert Transcript.model_validate_json(path.read_text("utf-8")).sha256 == sha


def test_entries_are_keyed_by_the_hash_of_the_canonical_request() -> None:
    recorder, _ = _record()
    for entry in recorder.transcript().entries:
        assert request_key(AgentTask.model_validate(json.loads(entry.request)["task"])) == entry.key
    task = _task()
    assert request_key(task) != request_key(task, "other")
    assert json.loads(canonical_request(task, "ns"))["namespace"] == "ns"


def test_a_missing_entry_is_a_model_failure_never_a_fallback() -> None:
    recorder, _ = _record()
    transcript = recorder.transcript()
    no_plan = transcript.model_copy(
        update={"entries": tuple(e for e in transcript.entries if e.step is not Step.PLAN)}
    )
    record = _investigate(RecordedModel(no_plan))
    assert record.final.verdict is Verdict.UNAVAILABLE
    with pytest.raises(ModelUnavailableError, match="transcript miss"):
        RecordedModel(no_plan).respond(_task())


@pytest.mark.parametrize(
    ("update", "problem"),
    [
        ({"response": '{"hypotheses": ["tactical_change"], "requests": []}'}, "response hash"),
        ({"request": "{}"}, "request hash"),
    ],
)
def test_a_tampered_entry_is_dropped_and_becomes_unavailable(
    update: dict[str, object], problem: str
) -> None:
    recorder, _ = _record()
    tampered = _rewrite(recorder.transcript(), Step.PLAN, **update)
    model = RecordedModel(tampered)
    assert any(problem in p for p in model.problems)
    assert _investigate(model).final.verdict is Verdict.UNAVAILABLE


def test_a_consistently_rewritten_entry_is_caught_by_the_pinned_transcript_hash(
    tmp_path: Path,
) -> None:
    recorder, _ = _record()
    original = recorder.transcript()
    forged_text = '{"hypotheses": ["tactical_change"], "requests": []}'
    forged = _rewrite(
        original,
        Step.PLAN,
        response=forged_text,
        response_sha256=recorded_module._sha256(forged_text),
    )
    path = tmp_path / "t.json"
    forged.write(path)
    model = RecordedModel.load(path, expected_sha256=original.sha256)
    assert model.problems == ["transcript hash mismatch"]
    assert not model.usable
    assert _investigate(model).final.verdict is Verdict.UNAVAILABLE


def test_an_entry_whose_step_disagrees_with_its_request_is_dropped() -> None:
    recorder, _ = _record()
    transcript = recorder.transcript()
    plan = next(e for e in transcript.entries if e.step is Step.PLAN)
    entries = tuple(
        e.model_copy(update={"step": Step.ASSESS}) if e is plan else e for e in transcript.entries
    )
    model = RecordedModel(transcript.model_copy(update={"entries": entries}))
    assert any("step mismatch" in p for p in model.problems)


def test_an_entry_with_a_malformed_request_is_dropped() -> None:
    request = "not json"
    entry = TranscriptEntry(
        key=recorded_module._sha256(request),
        step=Step.PLAN,
        request=request,
        response="{}",
        response_sha256=recorded_module._sha256("{}"),
    )
    model = RecordedModel(Transcript(reasoner="r", entries=(entry,)))
    assert any("malformed request" in p for p in model.problems)


def test_duplicate_keys_drop_every_copy() -> None:
    recorder, _ = _record()
    transcript = recorder.transcript()
    plan = next(e for e in transcript.entries if e.step is Step.PLAN)
    doubled = transcript.model_copy(update={"entries": (*transcript.entries, plan)})
    model = RecordedModel(doubled)
    assert sum("duplicate key" in p for p in model.problems) == 2
    assert _investigate(model).final.verdict is Verdict.UNAVAILABLE


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"format": "matcheyes.transcript/1"}',
        json.dumps({"format": "other/9", "reasoner": "r", "entries": []}),
        json.dumps(
            {
                "format": TRANSCRIPT_FORMAT,
                "reasoner": "r",
                "entries": [{"key": "short", "step": "plan", "request": "", "response": ""}],
            }
        ),
        json.dumps({"format": TRANSCRIPT_FORMAT, "reasoner": "r", "entries": [], "extra": 1}),
    ],
)
def test_a_malformed_transcript_is_unusable_as_a_whole(tmp_path: Path, content: str) -> None:
    path = tmp_path / "t.json"
    path.write_text(content, encoding="utf-8")
    model = RecordedModel.load(path)
    assert not model.usable
    with pytest.raises(ModelUnavailableError, match="unusable"):
        model.respond(_task())


def test_a_missing_or_oversized_file_is_unusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not RecordedModel.load(tmp_path / "absent.json").usable
    recorder, _ = _record()
    path = tmp_path / "t.json"
    recorder.transcript().write(path)
    monkeypatch.setattr(recorded_module, "MAX_TRANSCRIPT_BYTES", 10)
    model = RecordedModel.load(path)
    assert not model.usable
    assert model.problems[-1] == "ValueError"


def test_namespaces_keep_repeated_runs_apart() -> None:
    recorder = RecordingModel(RuleBasedReasoner())
    first, second = recorder.scoped("run-0"), recorder.scoped("run-1")
    task = _task()
    first.respond(task)
    second.respond(task)
    transcript = recorder.transcript()
    assert len(transcript.entries) == 2
    replay = RecordedModel(transcript)
    assert replay.scoped("run-1").respond(task) == RuleBasedReasoner().respond(task)
    with pytest.raises(ModelUnavailableError, match="miss"):
        replay.respond(task)
    assert replay.scoped("run-1").name == replay.name


def test_a_repeated_request_gets_the_first_answer_and_failures_are_not_recorded() -> None:
    script = ScriptedModel({Step.PLAN: [unavailable(), "first", "second"]})
    recorder = RecordingModel(script)  # type: ignore[arg-type]
    task = _task()
    with pytest.raises(ModelUnavailableError):
        recorder.respond(task)
    assert recorder.transcript().entries == ()
    assert recorder.respond(task) == "first"
    assert recorder.respond(task) == "first"
    assert len(script.tasks) == 2
    assert recorder.name == "scripted"


def test_transcripts_carry_only_the_request_the_model_saw() -> None:
    recorder, _ = _record()
    for entry in recorder.transcript().entries:
        request = json.loads(entry.request)
        assert set(request) == {"namespace", "task"}
        assert AgentTask.model_validate(request["task"]).step is entry.step
