"""Stage 5 LLM-path harness, exercised with SIMULATED profiles (not language models)."""

from functools import cache

import pytest

from matcheyes.agents.reasoning import ReasoningModel
from matcheyes_eval.__main__ import main
from matcheyes_eval.llm_eval import (
    DATASETS,
    FORBIDDEN_VOCABULARY,
    Item,
    LLMResults,
    build_items,
    classify_variation,
    evaluate_llm,
    format_llm,
    leaked,
)
from matcheyes_eval.simulated import PROFILES, SimulatedModel
from matcheyes_eval.stage2 import build_cases
from matcheyes_synth.seeds import development_seeds

pytestmark = pytest.mark.integration


@cache
def items() -> tuple[Item, ...]:
    return tuple(build_items(build_cases(development_seeds(1)), matches_per_dataset=1, top=1))


@cache
def run(profile: str) -> LLMResults:
    p = PROFILES[profile]

    def model_for(item: Item, repeat: int) -> ReasoningModel:
        return SimulatedModel(p, f"{p.name}/{item.item_id}/{repeat}")

    return evaluate_llm(
        items(),
        model_for,
        f"SIMULATED-{p.name}",
        True,
        repeats=3,
        repeat_items=4,
        ablation_items=3,
    )


def test_items_are_blinded_and_cover_every_dataset() -> None:
    built = items()
    assert {i.dataset for i in built} == set(DATASETS)
    assert [i.item_id for i in built] == [f"item-{n:04d}" for n in range(len(built))]
    assert any(i.subtype == "prompt_injection" for i in built)
    assert all(not i.expected for i in built if i.dataset != "A")
    datasets = [i.dataset for i in built]
    assert datasets != sorted(datasets), "items should be shuffled"


def test_leaked_vocabulary_is_detected_as_whole_words() -> None:
    assert leaked('{"note": "a planted cause"}', FORBIDDEN_VOCABULARY) == ["planted"]
    assert leaked('{"team": "Brightwater"}', ("twin",)) == []
    assert leaked("S02_press_surge", ("s02_press_surge",)) == ["s02_press_surge"]


def test_no_truth_reaches_the_model() -> None:
    assert not run("faithful").leaks


def test_a_faithful_profile_varies_only_in_wording() -> None:
    r = run("faithful")
    assert r.simulated and "SIMULATED" in format_llm(r)
    assert r.pairs > 0
    assert set(r.variation) <= {"wording", "identical"}
    for m in r.datasets.values():
        assert m.final_audit_flagged.hits == 0
        assert m.miscalibrated_text.hits == 0


def test_an_overclaiming_profile_is_measured_as_worse_but_stays_contained() -> None:
    honest, over = run("faithful"), run("overclaiming")

    def total(r: LLMResults, attr: str) -> int:
        return sum(getattr(m, attr).hits for m in r.datasets.values())

    assert total(over, "miscalibrated_text") > total(honest, "miscalibrated_text")
    assert total(over, "proposals_downgraded") + total(over, "overclaim_proposed") > (
        total(honest, "proposals_downgraded") + total(honest, "overclaim_proposed")
    )
    assert total(over, "final_audit_flagged") == 0
    assert total(over, "lineage_broken") == 0


def test_an_unreliable_profile_shows_failures_and_degrades_safely() -> None:
    r = run("unreliable")
    failed = sum(m.failed_calls for m in r.datasets.values())
    retries = sum(m.retries for m in r.datasets.values())
    assert failed + retries > 0
    assert sum(m.final_audit_flagged.hits for m in r.datasets.values()) == 0


def test_variation_is_classified_by_what_changed() -> None:
    base: dict[str, object] = {
        "hypotheses": ["a"],
        "requested": ["r"],
        "selected": ["s"],
        "final_claim": ("t", "k"),
        "strength": "hypothesised",
        "uncertainty": ["u"],
        "narrative": "n",
        "wording": ["w"],
    }
    assert classify_variation(base, dict(base)) == "identical"
    assert classify_variation(base, {**base, "wording": ["x"]}) == "wording"
    assert classify_variation(base, {**base, "requested": ["q"]}) == "procedural"
    assert classify_variation(base, {**base, "strength": "supported"}) == "material"


def test_live_evaluation_refuses_to_run_without_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("MATCHEYES_LLM_ENDPOINT", "MATCHEYES_LLM_API_KEY", "MATCHEYES_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert main(["llm", "--profile", "live"]) == 2
    assert "NOT evaluated" in capsys.readouterr().out
