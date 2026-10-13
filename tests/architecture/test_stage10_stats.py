"""Stage 10.3 statistics boundaries (ADR-0016).

Statistics are facts about a prefix: built from facts and analytics only, never read by a
reasoning layer, carrying no insight or hidden vocabulary, and leaving the cue contract as it was.
"""

import hashlib
import json

from matcheyes.broadcast.contracts import (
    BROADCAST_VERSION,
    SOURCE_FOR,
    Cue,
    CueKind,
    CueTimeline,
)
from matcheyes.broadcast.stats import SnapshotStats
from tests.architecture.test_agent_isolation import _hidden_vocabulary
from tests.support.imports import SRC_ROOT, imported_modules, python_files

STATS = SRC_ROOT / "matcheyes" / "broadcast" / "stats.py"
STATS_SOURCES = ("matcheyes.domain", "matcheyes.ingestion.invariants", "matcheyes.analytics")
STATS_SOURCES += ("matcheyes.lifecycle.snapshot",)
REASONING_LAYERS = ("agents", "orchestration", "personalization", "lifecycle")
INSIGHT_FIELDS = ("narrative", "claims", "evidence", "event_ids", "trace", "verification")
INSIGHT_FIELDS += ("verdict", "storyline_id", "insight", "explanation", "cause", "confidence")

CUE_SCHEMA_SHA256 = "97ad3d61dc880a52cb93aa9865d1314a13463d0cd7824379ab3a6909f02bd979"
"""SHA-256 of the canonical JSON schemas of Cue and CueTimeline at BROADCAST_VERSION 0.1.0."""


def test_statistics_are_built_from_facts_and_analytics_only() -> None:
    for module in imported_modules(STATS):
        if module.startswith("matcheyes."):
            assert module.startswith(STATS_SOURCES), module


def test_no_reasoning_layer_reads_broadcast_or_statistics() -> None:
    for layer in REASONING_LAYERS:
        for path in python_files(f"matcheyes/{layer}"):
            for module in imported_modules(path):
                assert not module.startswith("matcheyes.broadcast"), (path.name, module)


def test_statistics_carry_no_hidden_truth_insight_fields_or_event_ids() -> None:
    schema = json.dumps(SnapshotStats.model_json_schema()).lower()
    leaked = sorted(n for n in _hidden_vocabulary() if f'"{n.lower()}"' in schema)
    assert leaked == []
    for internal in INSIGHT_FIELDS:
        assert f'"{internal}"' not in schema, internal


def test_the_cue_contract_is_unchanged() -> None:
    assert BROADCAST_VERSION == "0.1.0"
    assert [k.value for k in CueKind] == ["moment", "insight", "revision", "retraction", "status"]
    assert set(SOURCE_FOR) == set(CueKind)
    schemas = [Cue.model_json_schema(), CueTimeline.model_json_schema()]
    text = json.dumps(schemas, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == CUE_SCHEMA_SHA256
    assert "stats" not in text
