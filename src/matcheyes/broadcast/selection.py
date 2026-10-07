"""The deterministic broadcast selection policy: what interrupts the surface and in which order.

* Only Stage 6 primary-feed views of current revisions reach the screen (fans and broadcasters
  see verified explanations and every compromised insight; insights without a verified
  explanation stay off the surface, as in Stage 6's secondary list).
* At most `MAX_ON_SCREEN` insight cards. A card stays while its storyline is current; a newer
  insight waits for a free card in Stage 6 order (relevance, then time, then candidate ID).
  Nothing is displaced, so a card never disappears without a revision or retraction.
* A revision interrupts the surface only for a material change other than re-anchoring.
  Re-anchor-only, evidence-only and audit-only revisions replace the card silently: the card
  must name the current revision, but the change is history, not news.
"""

from matcheyes.broadcast.contracts import CueKind, MomentKind
from matcheyes.lifecycle.contracts import MATERIAL, ChangeKind

INTERRUPTING = MATERIAL - {ChangeKind.REANCHORED}

PRIORITY = {
    CueKind.RETRACTION: 90,
    CueKind.REVISION: 70,
    CueKind.INSIGHT: 60,
    CueKind.STATUS: 10,
}
SILENT_REVISION_PRIORITY = 20
MOMENT_PRIORITY = {
    MomentKind.GOAL: 80,
    MomentKind.RED_CARD: 80,
    MomentKind.SUBSTITUTION: 40,
    MomentKind.PERIOD_START: 40,
    MomentKind.PERIOD_END: 40,
}
INTERRUPTING_MOMENTS = frozenset({MomentKind.GOAL, MomentKind.RED_CARD})


def interrupts(change_kinds: tuple[ChangeKind, ...]) -> bool:
    return bool(INTERRUPTING & set(change_kinds))
