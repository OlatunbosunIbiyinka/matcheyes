"""Stage 9 evaluation mechanics, with the reference reasoner standing in for the live model.

No network: these tests prove the record -> replay -> score pipeline, the hard invariants and the
transcript-integrity behaviour. Real-model results come only from a recorded live run."""

import json
from functools import cache

import pytest

from matcheyes.agents.reasoning import RuleBasedReasoner, Step
from matcheyes.agents.recorded import Transcript, TranscriptEntry
from matcheyes_eval.llm_eval import Item, build_items
from matcheyes_eval.stage2 import build_cases
from matcheyes_eval.stage9 import (
    Stage9Results,
    compose,
    evaluate_stage9,
    format_stage9,
    leak_sources,
    record_items,
    tamper_plan,
    transcript_metadata,
)
from matcheyes_synth.seeds import development_seeds

pytestmark = pytest.mark.integration

SHAPE = {"repeats": 2, "repeat_items": 2, "ablation_items": 2, "tamper_items": 2}


class StandIn(RuleBasedReasoner):
    """The reference, under another name, with a usage counter like the live adapter's."""

    def __init__(self) -> None:
        self.usage = type("U", (), {"calls": 0, "reported": 0, "prompt_tokens": 0})()
        self.usage.completion_tokens = 0  # type: ignore[attr-defined]

    @property
    def name(self) -> str:
        return "stand-in"


@cache
def items() -> tuple[Item, ...]:
    return tuple(build_items(build_cases(development_seeds(1)), matches_per_dataset=1, top=1))


@cache
def transcript() -> Transcript:
    live = StandIn()
    recorder, runs = record_items(items(), live, workers=4, **SHAPE)
    return recorder.transcript(transcript_metadata("stand-in", set(), runs, live.usage))


@cache
def results() -> Stage9Results:
    return evaluate_stage9(items(), transcript(), price_per_1k=(0.00025, 0.002), **SHAPE)


def test_every_replayed_run_equals_its_live_run() -> None:
    r = results()
    assert r.replayed_runs > len(items())
    assert r.replay_identical == r.replayed_runs
    assert r.replay_misses == 0


def test_the_hard_invariants_hold() -> None:
    assert results().invariants() == dict.fromkeys(results().invariants(), 0)
    assert results().model_texts_checked > 0


def test_the_reference_standing_in_agrees_with_the_reference() -> None:
    for m in results().llm.datasets.values():
        assert m.leading_agrees.hits == m.leading_agrees.n
    assert results().reasoner == "stand-in"


def test_tampering_runs_on_model_investigations_and_is_reported() -> None:
    r = results()
    assert tamper_plan(items(), 2)
    assert sum(t.injected for t in r.faults["C"].values()) > 0
    assert sum(t.missed for t in r.faults["C"].values()) == 0
    text = format_stage9(r)
    for heading in ("Hard invariants", "layer B", "layer C", "replay:", "LIVE MODEL"):
        assert heading in text


def test_configuration_b_prime_records_only_the_challenger() -> None:
    live = StandIn()
    recorder, runs = record_items(items(), live, workers=4, roles="challenger", **SHAPE)
    t = recorder.transcript(transcript_metadata("stand-in", set(), runs, live.usage))
    assert t.entries and {e.step for e in t.entries} == {Step.CHALLENGE}
    r = evaluate_stage9(items(), t, roles="challenger", **SHAPE)
    assert r.reasoner.startswith("investigator=") and r.reasoner.endswith("+challenger=stand-in")
    assert r.replay_identical == r.replayed_runs and r.replay_misses == 0
    assert r.invariants() == dict.fromkeys(r.invariants(), 0)
    assert "B' - reference reasoner as Investigator" in format_stage9(r)
    with pytest.raises(ValueError, match="roles"):
        compose("everything", StandIn())


def test_leak_sources_separate_engine_content_from_the_models_own_words() -> None:
    full = transcript()
    entry = next(e for e in full.entries if e.step is Step.CHALLENGE)
    request = json.loads(entry.request)
    echoed = json.loads(json.dumps(request))
    echoed["task"]["assessment"]["summary"] = "a specific intervention"
    engine = json.loads(json.dumps(request))
    engine["task"]["case"]["home_team"] = "planted"
    forged = [
        entry.model_copy(update={"request": json.dumps(r), "key": f"{n:064x}"})
        for n, r in enumerate((echoed, engine), start=1)
    ]
    sources = leak_sources(items(), full.model_copy(update={"entries": tuple(forged)}))
    assert sources == {"model's own echoed text only": 1, "engine-authored content": 1}
    assert leak_sources(items(), full) == {}


def test_recorded_leak_findings_are_attributed_traced_and_kept() -> None:
    from matcheyes_eval.leakage import (
        ENGINE_EXPOSURE,
        LEXICAL,
        NAMES_TRUTH,
        UNTRACED,
        transcript_findings,
    )

    full = transcript()
    entry = next(e for e in full.entries if e.step is Step.CHALLENGE)
    request = json.loads(entry.request)
    item = next(i for i in items() if i.item_id == request["namespace"].split("/")[0])
    term = "intervention"
    echoed = json.loads(json.dumps(request))
    echoed["task"]["assessment"]["summary"] = f"a specific {term}"
    origin = json.loads(json.dumps(request))
    origin["task"]["step"] = "assess"
    origin["task"]["assessment"] = None
    engine = json.loads(json.dumps(request))
    engine["namespace"] = request["namespace"] + "-engine"
    engine["task"]["case"]["home_team"] = "planted"
    lone = json.loads(json.dumps(echoed))
    lone["namespace"] = request["namespace"] + "-lone"

    def forge(n: int, r: dict[str, object], response: str = "{}") -> TranscriptEntry:
        return entry.model_copy(
            update={"request": json.dumps(r), "key": f"{n:064x}", "response": response}
        )

    forged = (
        forge(1, echoed),
        forge(2, origin, f'{{"summary": "an {term}"}}'),
        forge(3, engine),
        forge(4, lone),
    )
    findings = transcript_findings(items(), full.model_copy(update={"entries": forged}))
    by_ns = {ns.removeprefix(request["namespace"]): f.verdict for ns, f in findings}
    assert by_ns == {
        "": NAMES_TRUTH if term in item.truth else LEXICAL,
        "-engine": ENGINE_EXPOSURE,
        "-lone": UNTRACED,
    }
    assert transcript_findings(items(), full) == []
    r = results()
    assert r.leak_findings == [] and "field-attributed leak findings" in format_stage9(r)


def test_stage7_and_stage8_properties_hold_on_a_recorded_lifecycle() -> None:
    from matcheyes.lifecycle.recording import record_lifecycle
    from matcheyes_eval.stage2 import Case
    from matcheyes_eval.stage9 import evaluate_model_lifecycle, format_model_lifecycle
    from matcheyes_synth.scenarios import scenario
    from tests.lifecycle.support import SCENARIO, in_progress

    spec = scenario(SCENARIO)
    case = Case(spec, spec.default_seed, "planted", in_progress())
    recorder, _ = record_lifecycle(case.match, StandIn(), workers=4, roles="challenger")
    r = evaluate_model_lifecycle(case, recorder.transcript({"roles": "challenger"}), live=True)
    assert r.snapshots > 0 and r.revisions > 0 and r.unavailable_revisions == 0
    assert all(r.stage7.values()), r.stage7
    assert r.stage8 is not None
    for name, rate in r.stage8.properties.items():
        assert rate.total > 0 and rate.hits == rate.total, name
    assert r.stage8.stale_card_minutes == r.stage8.stale_status_minutes == 0
    assert "PASS" in format_model_lifecycle(r) and "FAIL" not in format_model_lifecycle(r)


def test_a_transcript_missing_an_entry_shows_as_misses_not_results() -> None:
    full = transcript()
    plan = next(e for e in full.entries if e.step is Step.PLAN)
    damaged = full.model_copy(update={"entries": tuple(e for e in full.entries if e is not plan)})
    r = evaluate_stage9(items(), damaged, **SHAPE)
    assert r.replay_misses >= 1
    assert r.replay_identical < r.replayed_runs
    assert any("model_unavailable" in k for k in r.unavailable_reasons)
