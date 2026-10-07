"""The match allow-list: observable match directories, and nothing else, loaded at start-up.

A directory is served only if its name is a safe identifier, it holds the observable match
files, and the match it loads carries that same ID. Only `match.json` and `events.jsonl` are
read; any other file in the tree is ignored. Requests name a match by ID and are looked up in
this mapping: no request ever reaches the file system.
"""

import re
from pathlib import Path

from matcheyes.domain.match import ObservableMatch
from matcheyes.ingestion.io import EVENTS_FILE, MATCH_FILE, load_observable_match

MATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
MAX_MATCHES = 8


def load_catalog(root: Path, limit: int = MAX_MATCHES) -> dict[str, ObservableMatch]:
    """Matches under `root`: either one observable match directory, or a directory of them."""
    candidates = [root] if (root / MATCH_FILE).is_file() else sorted(root.iterdir())
    catalog: dict[str, ObservableMatch] = {}
    for path in candidates:
        if len(catalog) >= limit:
            break
        if not (path.is_dir() and MATCH_ID.fullmatch(path.name)):
            continue
        if not ((path / MATCH_FILE).is_file() and (path / EVENTS_FILE).is_file()):
            continue
        match = load_observable_match(path)
        if match.info.match_id != path.name:
            raise ValueError(f"{path.name}: directory name differs from the match ID")
        catalog[match.info.match_id] = match
    if not catalog:
        raise ValueError(f"no observable match directories under {root}")
    return catalog
