from pydantic import TypeAdapter

from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import MatchInfo
from matcheyes.domain.events import MatchEvent

EVENT_ADAPTER: TypeAdapter[MatchEvent] = TypeAdapter(MatchEvent)


class ObservableMatch(DomainModel):
    """Everything MatchEyes is allowed to know about a match, and nothing else."""

    info: MatchInfo
    events: tuple[MatchEvent, ...]
