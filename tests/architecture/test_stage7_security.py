"""Stage 7 security: the lifecycle is local, deterministic recomputation over observable data.

* No persistence: the lifecycle and the event log open, write and store nothing; state lives in
  memory and leaves only through the caller (the CLI's explicit --json).
* Nothing upstream depends on the lifecycle.
* Truth and identity are decided without a reasoning model: only snapshot evaluation (the
  unchanged Stage 4/5 pipeline) touches the agents' reasoning; reconciliation, identity, the feed
  and the audit do not.
* Lifecycle identifiers derive from observable data: no hidden-truth vocabulary in any lifecycle
  contract, and storyline IDs take only the match, team, metric, direction and anchor.
* Runtime probe: replaying a match loaded from disk, in a clean interpreter, loads neither the
  generator nor the evaluator.

Network, process modules, dynamic code and scenario words are covered for the lifecycle by
test_agent_isolation, test_stage5_security and test_no_scenario_decoding.
"""

import inspect
import json
import re
from pathlib import Path

import pytest

from matcheyes.lifecycle.contracts import LifecycleState
from matcheyes.lifecycle.evaluate import SnapshotEvaluation
from matcheyes.lifecycle.feed import LifecycleFeed
from matcheyes.lifecycle.identity import storyline_id
from tests.architecture.test_agent_isolation import _hidden_vocabulary
from tests.support.imports import SRC_ROOT, imported_modules, python_files

LIFECYCLE = python_files("matcheyes/lifecycle")
EVENT_LOG = SRC_ROOT / "matcheyes" / "ingestion" / "log.py"
UPSTREAM = [
    p
    for layer in ("domain", "ingestion", "analytics", "agents", "orchestration", "personalization")
    for p in python_files(f"matcheyes/{layer}")
]
PERSISTENCE = re.compile(
    r"\bopen\(|write_text|write_bytes|read_text|\.write\(|\bsqlite3\b|\btempfile\b"
    r"|\bpathlib\b|\bjson\.dump\(|\bjson\.load\(|\bshelve\b|\bpickle\b|\bdbm\b|\bcsv\b|environ"
)
"""As Stage 6, except that `json.dumps` (canonical hashing, in memory) is allowed."""
MODEL_FREE = ("contracts.py", "snapshot.py", "identity.py", "reconcile.py", "feed.py", "audit.py")


def test_the_lifecycle_layer_exists() -> None:
    assert {p.name for p in LIFECYCLE} >= {"engine.py", "evaluate.py", *MODEL_FREE}


@pytest.mark.parametrize("path", [*LIFECYCLE, EVENT_LOG], ids=lambda p: p.name)
def test_the_lifecycle_persists_nothing(path: Path) -> None:
    hit = PERSISTENCE.search(path.read_text(encoding="utf-8"))
    assert hit is None, f"{path.name} uses {hit.group(0) if hit else ''}"


@pytest.mark.parametrize("path", UPSTREAM, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_nothing_upstream_imports_the_lifecycle(path: Path) -> None:
    assert "matcheyes.lifecycle" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", MODEL_FREE)
def test_reconciliation_identity_and_audit_use_no_reasoning_model(name: str) -> None:
    path = SRC_ROOT / "matcheyes" / "lifecycle" / name
    for module in imported_modules(path):
        assert module not in ("matcheyes.agents.reasoning", "matcheyes.agents.llm"), module
    text = path.read_text(encoding="utf-8")
    assert "ReasoningModel" not in text and "OpenAICompatibleModel" not in text


def test_lifecycle_contracts_carry_no_hidden_truth_vocabulary() -> None:
    hidden = _hidden_vocabulary()
    for model in (LifecycleState, SnapshotEvaluation, LifecycleFeed):
        schema = json.dumps(model.model_json_schema()).lower()
        leaked = sorted(n for n in hidden if f'"{n.lower()}"' in schema)
        assert leaked == [], (model.__name__, leaked)


def test_storyline_ids_take_only_observable_inputs() -> None:
    params = list(inspect.signature(storyline_id).parameters)
    assert params == ["match_id", "team_id", "metric", "direction", "first_anchor", "taken"]


_REPLAY_PROBE = """
import json, sys
from pathlib import Path
from matcheyes.ingestion.io import load_observable_match
from matcheyes.lifecycle.engine import replay
from matcheyes.lifecycle.feed import lifecycle_feed

match = load_observable_match(Path(sys.argv[1]))
engine = replay(match.info, match.events)
feed = lifecycle_feed(engine.state, engine.status())
loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("matcheyes_synth", "matcheyes_eval"))
print(json.dumps({"loaded": loaded, "storylines": len(engine.state.storylines)}))
"""


@pytest.mark.integration
def test_lifecycle_runtime_never_loads_truth(tmp_path: Path) -> None:
    import subprocess
    import sys

    from matcheyes.ingestion.io import write_observable_match
    from tests.lifecycle.support import in_progress

    write_observable_match(in_progress(), tmp_path)
    result = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", _REPLAY_PROBE, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["loaded"] == []
    assert report["storylines"] > 0
