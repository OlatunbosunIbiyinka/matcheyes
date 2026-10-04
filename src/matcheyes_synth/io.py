"""Output layout for generated matches (docs/synthetic-data.md#outputs).

    <root>/observable/<match_id>/match.json, events.jsonl   <- the only thing MatchEyes may read
    <root>/truth/<match_id>/answer_key.json                  <- GroundTruth, evaluation only
    <root>/truth/manifest.json                               <- match_id -> scenario, seed, hashes

Observable directories are named by the opaque match_id, so paths never reveal the scenario.
"""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from matcheyes.ingestion.io import EVENTS_FILE, MATCH_FILE, write_observable_match
from matcheyes_synth.generator import GeneratedMatch
from matcheyes_synth.truth import GroundTruth

OBSERVABLE_DIR = "observable"
TRUTH_DIR = "truth"
ANSWER_KEY_FILE = "answer_key.json"
MANIFEST_FILE = "manifest.json"


class ManifestEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    match_id: str
    scenario_id: str
    seed: int
    generator_version: str
    held_out: bool
    sha256: dict[str, str]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_generated_match(
    generated: GeneratedMatch, root: Path, *, held_out: bool
) -> ManifestEntry:
    match_id = generated.observable.info.match_id
    observable_dir = root / OBSERVABLE_DIR / match_id
    truth_dir = root / TRUTH_DIR / match_id
    write_observable_match(generated.observable, observable_dir)
    truth_dir.mkdir(parents=True, exist_ok=True)
    answer_key = truth_dir / ANSWER_KEY_FILE
    answer_key.write_text(generated.truth.model_dump_json(indent=2) + "\n", "utf-8", newline="\n")
    return ManifestEntry(
        match_id=match_id,
        scenario_id=generated.truth.scenario.scenario_id,
        seed=generated.truth.seed,
        generator_version=generated.truth.generator_version,
        held_out=held_out,
        sha256={
            f"{OBSERVABLE_DIR}/{match_id}/{MATCH_FILE}": _sha256(observable_dir / MATCH_FILE),
            f"{OBSERVABLE_DIR}/{match_id}/{EVENTS_FILE}": _sha256(observable_dir / EVENTS_FILE),
            f"{TRUTH_DIR}/{match_id}/{ANSWER_KEY_FILE}": _sha256(answer_key),
        },
    )


def write_manifest(entries: list[ManifestEntry], root: Path) -> Path:
    path = root / TRUTH_DIR / MANIFEST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(entries, key=lambda e: (e.scenario_id, e.seed))
    payload = [entry.model_dump() for entry in ordered]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", "utf-8", newline="\n")
    return path


def load_answer_key(root: Path, match_id: str) -> GroundTruth:
    path = root / TRUTH_DIR / match_id / ANSWER_KEY_FILE
    return GroundTruth.model_validate_json(path.read_text("utf-8"))
