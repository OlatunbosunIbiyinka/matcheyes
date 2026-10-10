"""Field-attributed leak findings: engine content, echoed model text, provenance, truth naming."""

import time

from pydantic import SecretStr

from matcheyes.agents.llm import LLMSettings, OpenAICompatibleModel
from matcheyes.agents.reasoning import AgentTask, RuleBasedReasoner, Step
from matcheyes.orchestration.investigation import Orchestrator
from matcheyes_eval.leakage import (
    ENGINE_DERIVED,
    ENGINE_EXPOSURE,
    LEXICAL,
    NAMES_TRUTH,
    UNATTRIBUTED,
    UNTRACED,
    Prompt,
    classify,
    exchange_prompts,
    fields_of,
)
from matcheyes_eval.llm_eval import FORBIDDEN_VOCABULARY, Metered, leaked, truth_terms
from matcheyes_eval.stage2 import Case
from matcheyes_synth.scenarios import CATALOGUE, scenario
from tests.agents.support import RED_CARD, strongest, workspace
from tests.synth.generated import generated

ENGINE_TEXT = {"instructions": "rules", "step": '"assess"', "case": "{}", "evidence": "[]"}


def prompt(call: int, reply: str = "", **fields: str) -> Prompt:
    base = {**ENGINE_TEXT, "tested": "[]", "assessment": "null", "feedback": ""}
    return Prompt(call, "challenge", {**base, **fields}, reply)


def verdicts(prompts: list[Prompt], truth: tuple[str, ...] = ()) -> list[tuple[str, str, str]]:
    return [(f.field, f.term, f.verdict) for f in classify(prompts, [], truth)]


def test_a_term_in_engine_authored_content_is_a_confirmed_exposure() -> None:
    assert verdicts([prompt(0, case='{"home": "planted Rovers"}')]) == [
        ("case", "planted", ENGINE_EXPOSURE)
    ]


def test_an_echoed_term_from_an_earlier_reply_is_a_lexical_hit() -> None:
    calls = [
        prompt(0, reply="an untriggered change"),
        prompt(1, assessment="an untriggered change"),
    ]
    [finding] = classify(calls, [])
    assert (finding.field, finding.term, finding.verdict) == ("assessment", "untriggered", LEXICAL)
    assert finding.origin_calls == (0,) and "untriggered" in finding.context


def test_an_echoed_term_no_earlier_reply_wrote_is_unresolved() -> None:
    later = [prompt(0, assessment="a twin"), prompt(1, reply="a twin")]
    assert verdicts(later) == [("assessment", "twin", UNTRACED)]
    unordered = classify(later, [], ordered=False)
    assert [f.verdict for f in unordered] == [LEXICAL]


def test_an_echoed_term_naming_the_hidden_truth_is_unresolved() -> None:
    calls = [prompt(0, reply="untriggered"), prompt(1, feedback="was untriggered")]
    assert verdicts(calls, truth=("untriggered",)) == [("feedback", "untriggered", NAMES_TRUTH)]


def test_an_echoed_term_the_engine_also_exposed_is_unresolved() -> None:
    calls = [prompt(0, reply="decoy", evidence='["decoy"]'), prompt(1, assessment="decoy")]
    assert verdicts(calls) == [
        ("evidence", "decoy", ENGINE_EXPOSURE),
        ("assessment", "decoy", ENGINE_DERIVED),
    ]


def test_a_term_split_across_fields_is_unattributed() -> None:
    calls = [prompt(0, case="ground", evidence="truth")]
    assert verdicts(calls) == [("(whole prompt)", "ground truth", UNATTRIBUTED)]


def test_item_specific_forbidden_terms_are_checked() -> None:
    assert verdicts([prompt(0, case="S05_RED_CARD")]) == []
    assert [f.verdict for f in classify([prompt(0, case="s05_red_card")], ["S05_red_card"])] == [
        ENGINE_EXPOSURE
    ]


def test_truth_terms_name_the_planted_and_counterfactual_truth() -> None:
    spec = scenario(RED_CARD)
    planted = truth_terms(Case(spec, 1, "planted", generated(RED_CARD).observable), "A")
    assert planted[:2] == (RED_CARD.lower(), "planted")
    assert {i.kind.value for i in spec.interventions} <= set(planted)
    assert "intervention" in planted and "planted" in planted
    twin = truth_terms(Case(spec, 1, "twin", generated(RED_CARD).observable), "B")
    assert {"twin", "counterfactual", "untriggered twins"} <= set(twin)
    untriggered = [
        s
        for s in CATALOGUE
        if any(i.trigger.value == "manager_instruction" for i in s.interventions)
    ]
    assert untriggered
    s = untriggered[0]
    assert "untriggered" in truth_terms(Case(s, 1, "planted", generated(RED_CARD).observable))


def test_fields_cover_everything_the_adapter_sends() -> None:
    """Every forbidden term in the messages actually sent is attributed to a field."""
    ws = workspace(RED_CARD)
    metered = Metered(RuleBasedReasoner(), [RED_CARD], time.perf_counter)
    Orchestrator(ws, metered).investigate(strongest(ws).candidate_id)
    task, _ = next((t, r) for t, r in metered.exchanges if t.step is Step.CHALLENGE)
    assert task.assessment is not None
    planted = AgentTask.model_validate(
        {
            **task.model_dump(mode="json"),
            "assessment": {
                **task.assessment.model_dump(mode="json"),
                "summary": "an intervention",
            },
            "feedback": "the scenario said so",
        }
    )
    settings = LLMSettings(endpoint="https://x.test/v1/", model="m", api_key=SecretStr("k"))
    adapter = OpenAICompatibleModel(settings)
    sent = "\n".join(m["content"] for m in adapter.messages(planted))
    in_fields = {
        t
        for text in fields_of(planted.model_dump(mode="json")).values()
        for t in leaked(text, FORBIDDEN_VOCABULARY)
    }
    assert set(leaked(sent, FORBIDDEN_VOCABULARY)) == in_fields == {"intervention", "scenario"}


def test_metered_keeps_every_exchange_in_call_order() -> None:
    ws = workspace(RED_CARD)
    metered = Metered(RuleBasedReasoner(), [RED_CARD], time.perf_counter)
    Orchestrator(ws, metered).investigate(strongest(ws).candidate_id)
    prompts = exchange_prompts(metered.exchanges)
    assert [p.step for p in prompts] == [c.step.value for c in metered.calls]
    assert [p.reply for p in prompts] == [c.raw for c in metered.calls]
    assert classify(prompts, [RED_CARD]) == []
