"""The analytics, agent, orchestration, personalization, lifecycle, broadcast and API layers must
not know the synthetic world they are evaluated on.

Static checks that no source in those layers mentions a scenario ID, a synthetic club, the
generator package or a hidden-state vocabulary term. Behavioural separation (no imports of
matcheyes_synth) is enforced in test_truth_separation.py; this catches special-casing by name.
"""

import re
from pathlib import Path

import pytest

from matcheyes_synth.league import CLUBS
from matcheyes_synth.scenarios import CATALOGUE
from tests.support.imports import python_files

INFERENCE_FILES = [
    *python_files("matcheyes/analytics"),
    *python_files("matcheyes/agents"),
    *python_files("matcheyes/orchestration"),
    *python_files("matcheyes/personalization"),
    *python_files("matcheyes/lifecycle"),
    *python_files("matcheyes/broadcast"),
    *python_files("matcheyes/api"),
]
FORBIDDEN_WORDS = (
    "matcheyes_synth",
    "matcheyes_eval",
    "scenario",
    "intervention",
    "answer_key",
    "answer key",
    "ground truth",
    "planted",
    "twin",
    "decoy",
    "supporting_mechanisms",
)


@pytest.mark.parametrize("path", INFERENCE_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_inference_never_names_scenarios_clubs_or_the_generator(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"\bS\d{2}\b|\bS\d{2}_", text)
    for spec in CATALOGUE:
        assert spec.scenario_id not in text
    for profile in CLUBS:
        assert profile.club.club_id not in text
        assert profile.club.name not in text
    lowered = text.lower()
    for word in FORBIDDEN_WORDS:
        assert word not in lowered, f"{path}: mentions {word!r}"
