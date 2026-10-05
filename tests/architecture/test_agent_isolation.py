"""Stage 4 hidden-truth boundary (ADR-0010): the reasoning system investigates as if the match
were real. Agents, tools and orchestration can reach only the observable match and the
deterministic analyses; nothing they receive or can request carries generator state.

The engine-wide textual scan for matcheyes_synth / matcheyes_eval is in test_truth_separation;
these tests add the Stage 4 specifics: what a model is shown, what it can ask for, and which
modules may touch the network or the environment. The Stage 6 personalization layer is held to
the same import and dynamic-code rules, with no network exception.
"""

import json
from pathlib import Path

import pytest

from matcheyes.agents.contracts import HypothesisKind, ToolName
from matcheyes.agents.reasoning import AgentTask, Step
from matcheyes.agents.tools import TOOL_SPECS
from matcheyes.domain.match import ObservableMatch
from matcheyes.orchestration.investigation import investigate_match
from matcheyes_synth.truth import GroundTruth, HiddenTeamState, PlayerAttributes
from tests.agents.support import RED_CARD, case_for, strongest, workspace
from tests.support.imports import imported_modules, python_files
from tests.synth.generated import generated

REASONING_FILES = (
    python_files("matcheyes/agents")
    + python_files("matcheyes/orchestration")
    + python_files("matcheyes/personalization")
)
NETWORK_MODULES = ("urllib", "http", "socket", "requests", "httpx", "aiohttp", "ssl")
PROCESS_MODULES = ("subprocess", "os", "importlib", "pickle", "shelve", "ctypes")
NETWORK_ALLOWED = {"llm.py"}


def _hidden_vocabulary() -> set[str]:
    names = set(HiddenTeamState.model_fields) | set(PlayerAttributes.model_fields)
    names |= set(GroundTruth.model_fields)
    names |= {"scenario_id", "intervention_id", "expected_insights", "primary_cause"}
    names |= {"supporting_mechanisms", "answer_key", "planted"}
    return names - {"player_id", "match_id", "team_id"}


@pytest.mark.parametrize("path", REASONING_FILES, ids=lambda p: p.name)
def test_reasoning_layers_import_no_network_or_process_modules(path: Path) -> None:
    for module in imported_modules(path):
        root = module.split(".")[0]
        assert root not in PROCESS_MODULES, f"{path.name} imports {module}"
        if path.name not in NETWORK_ALLOWED:
            assert root not in NETWORK_MODULES, f"{path.name} imports {module}"


@pytest.mark.parametrize("path", REASONING_FILES, ids=lambda p: p.name)
def test_reasoning_layers_have_no_dynamic_code_execution(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for call in ("eval(", "exec(", "__import__(", "compile(", "getattr(__builtins__"):
        assert call not in text, f"{path.name} uses {call}"


def test_the_tool_set_is_closed_and_offers_no_hidden_data() -> None:
    assert set(TOOL_SPECS) == set(ToolName)
    hidden = _hidden_vocabulary()
    for spec in TOOL_SPECS.values():
        schema = json.dumps(spec.arguments.model_json_schema()).lower()
        assert not any(f'"{name.lower()}"' in schema for name in hidden), spec.name
        assert "scenario" not in spec.decision.lower()


def test_hypotheses_are_explanations_not_generator_labels() -> None:
    labels = {k.value for k in HypothesisKind}
    assert not labels & {"press_surge", "deep_block", "fatigue_decay", "impact_substitution"}


def test_what_a_model_is_shown_contains_no_hidden_truth() -> None:
    generated_match = generated(RED_CARD)
    ws = workspace(RED_CARD)
    task = AgentTask(step=Step.PLAN, case=case_for(ws, strongest(ws)))
    shown = task.model_dump_json().lower()
    spec = generated_match.truth.scenario
    assert spec.scenario_id.lower() not in shown
    for intervention in spec.interventions:
        assert f'"{intervention.intervention_id.lower()}"' not in shown
    for name in _hidden_vocabulary():
        assert f'"{name.lower()}"' not in shown, name


def test_investigation_depends_only_on_the_observable_round_trip(tmp_path: Path) -> None:
    """Writing the observable match to disk and reloading it (which drops every truth object)
    gives a byte-identical investigation: nothing else reaches the reasoning system."""
    from matcheyes.ingestion.io import load_observable_match, write_observable_match

    match = generated(RED_CARD).observable
    write_observable_match(match, tmp_path)
    reloaded = load_observable_match(tmp_path)

    def outcome(m: ObservableMatch) -> list[str]:
        return [r.final.model_dump_json() for r in investigate_match(m).records]

    assert outcome(reloaded) == outcome(match)
