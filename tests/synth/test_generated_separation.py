"""Requirement 3: hidden state drives events but never leaks into payloads, identifiers, paths,
metadata or filenames of the observable output."""

import json
import re
from pathlib import Path

import pytest

from matcheyes.domain.match import EVENT_ADAPTER
from matcheyes_synth.io import OBSERVABLE_DIR, write_generated_match
from matcheyes_synth.scenarios import scenario
from matcheyes_synth.truth import HiddenTeamState, PlayerAttributes
from tests.synth.generated import SCENARIO_IDS, generated

HIDDEN_KEYS = (
    set(HiddenTeamState.model_fields) | set(PlayerAttributes.model_fields) - {"player_id"}
) | {"scenario_id", "intervention_id", "insight_id", "decoy_id", "seed", "drivers", "ref"}


def _keys(node: object) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}
    return set()


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_observable_output_reveals_nothing_about_the_scenario(
    scenario_id: str, tmp_path: Path
) -> None:
    spec = scenario(scenario_id)
    match = generated(scenario_id)
    write_generated_match(match, tmp_path, held_out=False)
    observable_root = tmp_path / OBSERVABLE_DIR

    paths = [p.relative_to(observable_root).as_posix() for p in observable_root.rglob("*")]
    text = "".join(
        (observable_root / p).read_text("utf-8") for p in paths if p.endswith(("json", "jsonl"))
    )
    lowered = (text + " ".join(paths)).lower()

    forbidden = [spec.scenario_id, spec.title, spec.scenario_id.split("_", 1)[1]]
    forbidden += ["intervention", "decoy", "scenario", "answer_key", "truth"]
    for needle in forbidden:
        assert needle.lower() not in lowered, f"observable output contains {needle!r}"

    assert re.fullmatch(r"m-[0-9a-f]{12}", match.observable.info.match_id)
    assert all(p.startswith(match.observable.info.match_id) for p in paths)

    payloads = [json.loads(EVENT_ADAPTER.dump_json(e)) for e in match.observable.events]
    payloads.append(json.loads(match.observable.info.model_dump_json()))
    leaked = _keys(payloads) & HIDDEN_KEYS
    assert not leaked, f"hidden keys in observable payloads: {sorted(leaked)}"


def test_scenarios_sharing_clubs_publish_identical_team_metadata() -> None:
    """Team sheets do not vary with the scenario, so they cannot encode it."""
    a = generated("S02_press_surge").observable.info.home
    b = generated("S01_control_balanced").observable.info.home
    assert a == b
