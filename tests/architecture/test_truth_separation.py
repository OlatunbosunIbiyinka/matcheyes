"""OBSERVABLE MATCH DATA vs HIDDEN GROUND TRUTH (ADR-0005).

MatchEyes must never receive hidden ground truth during inference. These tests enforce that at
four levels: source dependencies, the deployable artefact, the published schema, and runtime
ingestion.
"""

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from matcheyes.domain.entities import MatchInfo
from matcheyes.domain.match import EVENT_ADAPTER, ObservableMatch
from matcheyes_synth.truth import GroundTruth, HiddenTeamState, PlayerAttributes
from tests.support.builders import match_info, minimal_match
from tests.support.imports import SRC_ROOT, imported_modules, python_files

HIDDEN_PACKAGES = ("matcheyes_synth", "matcheyes_eval")
AI_AND_CLOUD_SDK_PREFIXES = ("agent_framework", "openai", "azure", "anthropic", "langchain")

# Generic structural names that legitimately appear in both worlds. Anything else shared
# between the observable schema and the answer key must be reviewed before being added here.
SHARED_STRUCTURAL_FIELDS = frozenset(
    {
        "club",
        "club_id",
        "clock_ms",
        "formation",
        "kind",
        "match_id",
        "name",
        "period",
        "player_id",
        "primary_colour",
        "secondary_colour",
        "short_name",
        "team_id",
    }
)


# --- 1. Source dependencies -------------------------------------------------------------


def test_engine_never_references_hidden_packages() -> None:
    """Textual scan, so dynamic imports (importlib, __import__) are caught too."""
    for path in python_files("matcheyes"):
        source = path.read_text(encoding="utf-8")
        for hidden in HIDDEN_PACKAGES:
            assert hidden not in source, f"{path.relative_to(SRC_ROOT)} references {hidden}"


def test_generator_only_uses_engine_domain_and_ingestion() -> None:
    for path in python_files("matcheyes_synth"):
        for module in imported_modules(path):
            parts = module.split(".")
            assert parts[0] != "matcheyes_eval", f"{path.name} imports the evaluator"
            if parts[0] == "matcheyes" and len(parts) > 1:
                assert parts[1] in {"domain", "ingestion"}, f"{path.name} imports {module}"


def test_generator_is_deterministic_code_without_ai_sdks() -> None:
    for path in python_files("matcheyes_synth"):
        for module in imported_modules(path):
            assert not module.startswith(AI_AND_CLOUD_SDK_PREFIXES), f"{path.name}: {module}"


# --- 2. Deployable artefact -------------------------------------------------------------


def test_wheel_ships_only_the_engine() -> None:
    pyproject = tomllib.loads((SRC_ROOT.parent / "pyproject.toml").read_text("utf-8"))
    packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert packages == ["src/matcheyes"]


# --- 3. Published schema ----------------------------------------------------------------


def _property_names(schema: dict[str, Any]) -> set[str]:
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                names.update(properties)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return names


def test_observable_schema_shares_no_hidden_fields() -> None:
    observable = _property_names(ObservableMatch.model_json_schema())
    hidden = _property_names(GroundTruth.model_json_schema())
    leaked = (observable & hidden) - SHARED_STRUCTURAL_FIELDS
    assert not leaked, f"hidden ground-truth field names in observable schema: {sorted(leaked)}"


# --- 4. Runtime ingestion ---------------------------------------------------------------


def _hidden_field_names(*models: type[BaseModel]) -> list[str]:
    return sorted({name for model in models for name in model.model_fields} - {"player_id"})


HIDDEN_FIELDS = [
    *_hidden_field_names(HiddenTeamState, PlayerAttributes),
    "scenario_id",
    "intervention_id",
    "expected_insights",
]


@pytest.mark.parametrize("hidden_field", HIDDEN_FIELDS)
def test_events_carrying_hidden_fields_are_rejected(hidden_field: str) -> None:
    event = minimal_match().events[1]
    payload = json.loads(EVENT_ADAPTER.dump_json(event))
    payload[hidden_field] = 0.9
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EVENT_ADAPTER.validate_json(json.dumps(payload))


@pytest.mark.parametrize("hidden_field", HIDDEN_FIELDS)
def test_match_metadata_carrying_hidden_fields_is_rejected(hidden_field: str) -> None:
    payload = json.loads(match_info().model_dump_json())
    payload["home"]["club"][hidden_field] = 0.9
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        MatchInfo.model_validate_json(json.dumps(payload))


_RUNTIME_PROBE = """
import importlib, json, pkgutil, sys
from pathlib import Path
import matcheyes
for module in pkgutil.walk_packages(matcheyes.__path__, "matcheyes."):
    importlib.import_module(module.name)
from matcheyes.ingestion.invariants import validate_match
from matcheyes.ingestion.io import load_observable_match
validate_match(load_observable_match(Path(sys.argv[1])))
print(json.dumps(sorted(m for m in sys.modules if m.split(".")[0] in sys.argv[2:])))
"""


def test_engine_runtime_never_loads_hidden_packages() -> None:
    """Catches what static analysis cannot (e.g. obfuscated dynamic imports): import every
    engine module and run ingestion in a clean interpreter, then inspect sys.modules."""
    import subprocess
    import sys

    from tests.support.builders import FIXTURE_DIR

    result = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", _RUNTIME_PROBE, str(FIXTURE_DIR), *HIDDEN_PACKAGES],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == []


def test_observable_writer_emits_no_truth_artefacts(tmp_path: Path) -> None:
    from matcheyes.ingestion.io import write_observable_match

    write_observable_match(minimal_match(), tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["events.jsonl", "match.json"]
