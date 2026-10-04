"""Identifiers visible to the engine must not encode the scenario (that would leak the answer)."""

import hashlib

MATCH_ID_PREFIX = "m-"


def opaque_match_id(scenario_id: str, seed: int, generator_version: str) -> str:
    digest = hashlib.sha256(f"{generator_version}|{scenario_id}|{seed}".encode()).hexdigest()
    return f"{MATCH_ID_PREFIX}{digest[:12]}"
