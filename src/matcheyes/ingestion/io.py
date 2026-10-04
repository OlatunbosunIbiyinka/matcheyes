"""On-disk observable format: `<dir>/match.json` (MatchInfo) + `<dir>/events.jsonl` (one event
per line, canonical sequence order). Hidden ground truth is never written to, or read from,
an observable directory.
"""

from pathlib import Path

from matcheyes.domain.entities import MatchInfo
from matcheyes.domain.match import EVENT_ADAPTER, ObservableMatch

MATCH_FILE = "match.json"
EVENTS_FILE = "events.jsonl"


def write_observable_match(match: ObservableMatch, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    info = match.info.model_dump_json(indent=2) + "\n"
    (directory / MATCH_FILE).write_text(info, "utf-8", newline="\n")
    lines = (EVENT_ADAPTER.dump_json(event).decode("utf-8") for event in match.events)
    events = "".join(f"{line}\n" for line in lines)
    (directory / EVENTS_FILE).write_text(events, "utf-8", newline="\n")


def load_observable_match(directory: Path) -> ObservableMatch:
    info = MatchInfo.model_validate_json((directory / MATCH_FILE).read_text("utf-8"))
    with (directory / EVENTS_FILE).open(encoding="utf-8") as handle:
        events = tuple(EVENT_ADAPTER.validate_json(line) for line in handle if line.strip())
    return ObservableMatch(info=info, events=events)
